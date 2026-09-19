"""Тесты базового Telegram-интерфейса (ТЗ раздел 12, DoD подэтапа 1.9)."""
from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.service import BotService, parse_ticker
from app.db.models import Signal
from app.db.session import create_all
from app.ingestion.base_client import SourceError
from app.ingestion.normalization import PriceBar, PriceHistory, QualityReport
from app.ingestion.schemas import Earnings, Fundamentals
from app.ingestion.service import MarketData
from app.ingestion.source_router import AllSourcesUnavailableError
from app.pipeline import Pipeline

FETCHED = datetime(2025, 9, 18, tzinfo=UTC)


def _price_history(ticker: str, n: int, base: float) -> PriceHistory:
    bars = []
    d = date(2025, 1, 1)
    prev = base
    for i in range(n):
        close = base + i * 0.2 + 3.0 * math.sin(i / 8.0)
        open_ = prev
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        bars.append(
            PriceBar(
                ticker=ticker, date=d, open=open_, high=high, low=low, close=close,
                volume=2_000_000 + (i % 5) * 100_000, source="fake", fetched_at=FETCHED,
            )
        )
        prev = close
        d += timedelta(days=1)
    return PriceHistory(ticker=ticker, bars=bars, source="fake", fetched_at=FETCHED)


def _market(ticker: str, n: int = 260, base: float = 100.0) -> MarketData:
    return MarketData(
        ticker=ticker,
        price_history=_price_history(ticker, n, base),
        fundamentals=Fundamentals(
            ticker=ticker, source="fake", fetched_at=FETCHED, period="annual",
            revenue_growth=0.12, eps_growth=0.18, eps=5.0, gross_margin=0.42,
            debt_equity=1.1, pe=28.0, forward_pe=24.0,
        ),
        earnings=Earnings(ticker=ticker, next_earnings_date=None, source="fake",
                          fetched_at=FETCHED),
        quality={},
        is_incomplete=False,
    )


class FakeIngestion:
    def __init__(self, markets: dict[str, MarketData], *, unavailable: set[str] = frozenset()):
        self._markets = markets
        self._unavailable = unavailable

    async def get_market_data(self, ticker: str) -> MarketData:
        if ticker in self._unavailable:
            raise AllSourcesUnavailableError("нет источников", source="fmp")
        if ticker not in self._markets:
            raise SourceError(f"пустой ответ {ticker}", source="fmp")
        return self._markets[ticker]

    async def get_price_history(self, ticker: str):
        if ticker in self._markets:
            return self._markets[ticker].price_history, QualityReport(source="fake")
        # бенчмарк SPY по умолчанию
        return _price_history(ticker, 260, 400.0), QualityReport(source="fake")


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await create_all(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_service(session_factory, markets, **kw) -> BotService:
    ingestion = FakeIngestion(markets, **kw)
    return BotService(Pipeline(ingestion, session_factory), session_factory)


# --------------------------------------------------------------------------- #
# parse_ticker
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [("NVDA", "NVDA"), ("$nvda", "NVDA"), ("aapl", "AAPL"),
     ("hello world", None), ("TOOLONG", None), ("", None), ("/start", None)],
)
def test_parse_ticker(text, expected):
    assert parse_ticker(text) == expected


# --------------------------------------------------------------------------- #
# Анализ тикера
# --------------------------------------------------------------------------- #
async def test_analyze_returns_message_and_saves_signal(session_factory):
    service = make_service(session_factory, {"NVDA": _market("NVDA")})
    text, signal_id = await service.analyze("NVDA")

    assert signal_id is not None
    assert "NVDA" in text
    assert "Final Score" in text
    assert "Momentum" in text          # 7 факторов в сообщении
    assert "Не является" in text       # дисклеймер

    async with session_factory() as s:
        count = await s.scalar(select(func.count()).select_from(Signal))
    assert count == 1


async def test_unknown_ticker_returns_error_no_crash(session_factory):
    service = make_service(session_factory, {"NVDA": _market("NVDA")})
    text, signal_id = await service.analyze("ZZZZ")
    assert signal_id is None
    assert "Не удалось найти тикер ZZZZ" in text
    # техошибка не превратилась в торговый вывод
    assert "SELL" not in text and "BUY" not in text


async def test_sources_unavailable_returns_service_message(session_factory):
    service = make_service(
        session_factory, {"NVDA": _market("NVDA")}, unavailable={"NVDA"}
    )
    text, signal_id = await service.analyze("NVDA")
    assert signal_id is None
    assert "временно недоступен" in text


async def test_incomplete_data_marks_message(session_factory):
    # короткая история → часть метрик None → неполный расчёт
    service = make_service(session_factory, {"TINY": _market("TINY", n=15)})
    text, signal_id = await service.analyze("TINY")
    assert signal_id is not None
    assert "неполн" in text.lower()


# --------------------------------------------------------------------------- #
# Кнопки-разделы и история
# --------------------------------------------------------------------------- #
async def test_sections_render_from_stored_signal(session_factory):
    service = make_service(session_factory, {"NVDA": _market("NVDA")})
    _, signal_id = await service.analyze("NVDA")

    details = await service.section(signal_id, "details")
    metrics = await service.section(signal_id, "metrics")
    risk = await service.section(signal_id, "risk")
    assert "расшифровка" in details.lower()
    assert "RSI14" in metrics
    assert "Risk Filter" in risk


async def test_section_unknown_signal(session_factory):
    service = make_service(session_factory, {})
    assert "не найден" in (await service.section(999, "details")).lower()


async def test_history_lists_signals(session_factory):
    service = make_service(session_factory, {"NVDA": _market("NVDA")})
    await service.analyze("NVDA")
    await service.analyze("NVDA")
    text = await service.history("NVDA")
    assert text.count("Score") >= 2  # две записи истории


# --------------------------------------------------------------------------- #
# Параллельные запросы по разным тикерам не смешиваются (DoD 1.9)
# --------------------------------------------------------------------------- #
async def test_concurrent_requests_do_not_mix(session_factory):
    markets = {
        "NVDA": _market("NVDA", base=100.0),
        "AAPL": _market("AAPL", base=200.0),
        "TSLA": _market("TSLA", base=300.0),
    }
    service = make_service(session_factory, markets)

    results = await asyncio.gather(
        service.analyze("NVDA"), service.analyze("AAPL"), service.analyze("TSLA")
    )
    ids = [sid for _, sid in results]

    assert len(set(ids)) == 3                      # разные signal_id
    for (text, _sid), ticker in zip(results, ("NVDA", "AAPL", "TSLA"), strict=False):
        assert ticker in text                      # каждый ответ про свой тикер

    async with session_factory() as s:
        rows = (await s.execute(select(Signal))).scalars().all()
    assert {r.ticker for r in rows} == {"NVDA", "AAPL", "TSLA"}


# --------------------------------------------------------------------------- #
# Smoke: aiogram-слой импортируется и роутер собирается
# --------------------------------------------------------------------------- #
def test_bot_layer_imports(session_factory):
    from app.bot.handlers import build_router
    from app.bot.keyboards import main_keyboard

    service = BotService(None, session_factory)  # type: ignore[arg-type]
    router = build_router(service)
    assert router is not None
    kb = main_keyboard(1, "NVDA")
    assert len(kb.inline_keyboard) == 2
