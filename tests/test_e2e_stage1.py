"""Сквозная проверка Этапа 1 (ТЗ раздел 14, подэтап 1.10; демонстрация Этапа 1).

End-to-end через настоящие компоненты (клиенты источников → source_router →
IngestionService → Pipeline → БД → Telegram-рендер); мокируется только HTTP
(respx), т.к. реальные API-ключи появятся на Stage 0. Проверяется:
- полная цепочка источник → метрики → 7 факторов → Final Score → Risk → БД → Telegram;
- сценарий отказа основного источника (FMP) → переключение на резерв (Alpaca);
- полная недоступность → честное сообщение, без ложного расчёта;
- значения в backend и в Telegram совпадают; воспроизводимость (повтор идентичен).
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import httpx
import pytest_asyncio
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.service import BotService
from app.db.models import Signal
from app.db.repository import list_signals, recompute_from_snapshot
from app.db.session import create_all
from app.ingestion import IngestionService
from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.fmp_client import FMPClient
from app.ingestion.source_router import SourceRouter
from app.monitoring.source_health import SourceHealthMonitor
from app.pipeline import Pipeline

FMP_HIST = re.compile(r".*/stable/historical-price-eod/full")
FMP_RATIOS = re.compile(r".*/stable/ratios")
FMP_METRICS = re.compile(r".*/stable/key-metrics")
FMP_GROWTH = re.compile(r".*/stable/income-statement-growth")
FMP_EARNINGS = re.compile(r".*/stable/earnings")
ALPACA_BARS = re.compile(r".*/v2/stocks/.+/bars")


def _fmp_hist(n: int = 260, base: float = 150.0) -> list[dict]:
    rows = []
    d = date(2025, 1, 1)
    prev = base
    for i in range(n):
        close = base + i * 0.2 + 4.0 * math.sin(i / 7.0)
        open_ = prev
        high = max(open_, close) + 1.2
        low = min(open_, close) - 1.2
        rows.append({
            "date": d.isoformat(), "open": round(open_, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2), "volume": 5_000_000 + i,
        })
        prev = close
        d += timedelta(days=1)
    return list(reversed(rows))  # FMP отдаёт свежие сверху


def _alpaca_bars(n: int = 260, base: float = 150.0) -> dict:
    bars = []
    d = date(2025, 1, 1)
    prev = base
    for i in range(n):
        close = base + i * 0.2 + 4.0 * math.sin(i / 7.0)
        open_ = prev
        bars.append({
            "t": f"{d.isoformat()}T05:00:00Z", "o": round(open_, 2),
            "h": round(max(open_, close) + 1.2, 2), "l": round(min(open_, close) - 1.2, 2),
            "c": round(close, 2), "v": 5_000_000 + i,
        })
        prev = close
        d += timedelta(days=1)
    return {"symbol": "X", "bars": bars}


def _mock_fmp_fundamentals() -> None:
    respx.get(FMP_RATIOS).mock(return_value=httpx.Response(
        200, json=[{"grossProfitMargin": 0.44, "debtEquityRatio": 1.1,
                    "priceEarningsRatio": 28.0, "eps": 6.0}]))
    respx.get(FMP_METRICS).mock(return_value=httpx.Response(
        200, json=[{"eps": 6.0, "forwardPE": 24.0}]))
    respx.get(FMP_GROWTH).mock(return_value=httpx.Response(
        200, json=[{"growthRevenue": 0.12, "growthEPS": 0.18}]))
    respx.get(FMP_EARNINGS).mock(return_value=httpx.Response(
        200, json=[{"date": "2099-01-15"}]))


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


def build_service(session_factory, monitor: SourceHealthMonitor):
    router = SourceRouter(
        fmp=FMPClient(api_key="K", max_retries=1, health_recorder=monitor),
        alpaca=AlpacaClient(api_key_id="I", api_secret_key="S", max_retries=1,
                            health_recorder=monitor),
    )
    ingestion = IngestionService(router, min_history_bars=250)
    return BotService(Pipeline(ingestion, session_factory), session_factory)


def _score_from_text(text: str) -> int:
    m = re.search(r"Final Score:\s*<b>(\d+)/100</b>", text)
    assert m, f"нет Final Score в сообщении: {text}"
    return int(m.group(1))


# --------------------------------------------------------------------------- #
# Happy path: полная цепочка + совпадение backend/Telegram + воспроизводимость
# --------------------------------------------------------------------------- #
@respx.mock
async def test_full_chain_and_reproducibility(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    monitor = SourceHealthMonitor()
    service = build_service(session_factory, monitor)

    # два прогона одного тикера
    text1, sid1 = await service.analyze("AAPL")
    text2, sid2 = await service.analyze("AAPL")

    assert sid1 is not None and sid2 is not None and sid1 != sid2

    async with session_factory() as s:
        count = await s.scalar(
            select(func.count()).select_from(Signal).where(Signal.ticker == "AAPL")
        )
        history = await list_signals(s, "AAPL")
        signal = history[0]
    assert count == 2  # обе записи в истории (append-only)

    # значения в backend и в Telegram совпадают
    assert _score_from_text(text1) == signal.final_score

    # источник данных виден и это основной (FMP)
    assert signal.raw_input_snapshot["features"]["price"] is not None
    assert monitor.snapshot()["fmp"]["status"] == "ok"

    # воспроизводимость: пересчёт из снимка идентичен
    assert recompute_from_snapshot(signal).final_score == signal.final_score
    # два прогона с теми же данными дали одинаковый Final Score
    assert _score_from_text(text1) == _score_from_text(text2)


# --------------------------------------------------------------------------- #
# Отказ основного источника → переключение на резерв (Alpaca)
# --------------------------------------------------------------------------- #
@respx.mock
async def test_primary_source_failover_to_reserve(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(503))          # FMP цены — down
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(200, json=_alpaca_bars()))
    _mock_fmp_fundamentals()                                            # fundamentals из FMP ok
    monitor = SourceHealthMonitor()
    service = build_service(session_factory, monitor)

    text, sid = await service.analyze("AAPL")

    assert sid is not None                       # расчёт получился через резерв
    assert "Final Score" in text
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    assert signal.price is not None              # цены получены (из резерва)
    # FMP помечен как сбойный, Alpaca (резерв) отработал успешно
    assert monitor.snapshot()["fmp"]["status"] == "error"
    assert monitor.snapshot()["alpaca"]["status"] == "ok"


# --------------------------------------------------------------------------- #
# Полная недоступность → честное сообщение, без ложного расчёта
# --------------------------------------------------------------------------- #
@respx.mock
async def test_all_sources_down_honest_message(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(503))
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(500))
    monitor = SourceHealthMonitor()
    service = build_service(session_factory, monitor)

    text, sid = await service.analyze("AAPL")

    assert sid is None
    assert "недоступен" in text
    assert "BUY" not in text and "SELL" not in text  # техошибка ≠ торговый вывод
    async with session_factory() as s:
        count = await s.scalar(select(func.count()).select_from(Signal))
    assert count == 0  # ложный/частичный расчёт не сохранён
