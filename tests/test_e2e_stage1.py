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
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest_asyncio
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.service import BotService
from app.db.models import Signal
from app.db.repository import list_signals, recompute_from_snapshot, replay_signal
from app.db.session import create_all
from app.ingestion import IngestionService
from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.fmp_client import FMPClient
from app.ingestion.sec_edgar_client import SECEdgarClient
from app.ingestion.source_router import SourceRouter
from app.monitoring.source_health import SourceHealthMonitor, latest_data_mode
from app.pipeline import Pipeline

# Новость в окне актуальности относительно даты запуска тестов.
RECENT_NEWS_TS = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

FMP_HIST = re.compile(r".*/stable/historical-price-eod/full")
FMP_RATIOS = re.compile(r".*/stable/ratios")
FMP_METRICS = re.compile(r".*/stable/key-metrics")
FMP_GROWTH = re.compile(r".*/stable/income-statement-growth")
FMP_EARNINGS = re.compile(r".*/stable/earnings")
FMP_NEWS = re.compile(r".*/stable/news/stock")
FMP_QUOTE = re.compile(r".*/stable/quote")
SEC_TICKERS = re.compile(r".*sec\.gov/files/company_tickers\.json")
SEC_SUBMISSIONS = re.compile(r".*/submissions/CIK\d+\.json")
FMP_PROFILE = re.compile(r".*/stable/profile")
ALPACA_BARS = re.compile(r".*/v2/stocks/.+/bars")



def history_start(n: int) -> date:
    """Первый бар истории из n дневных баров, последний из которых — вчера (ET):
    история «свежая» для проверки max_last_bar_age_business_days (v1.5)."""
    return datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=n)

def _fmp_hist(n: int = 260, base: float = 150.0) -> list[dict]:
    rows = []
    d = history_start(n)
    prev = base
    for i in range(n):
        close = base + i * 0.2 + 4.0 * math.sin(i / 7.0)
        open_ = prev
        high = max(open_, close) + 1.2
        low = min(open_, close) - 1.2
        rows.append(
            {
                "date": d.isoformat(),
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": 5_000_000 + i,
            }
        )
        prev = close
        d += timedelta(days=1)
    return list(reversed(rows))  # FMP отдаёт свежие сверху


def _alpaca_bars(n: int = 260, base: float = 150.0) -> dict:
    bars = []
    d = history_start(n)
    prev = base
    for i in range(n):
        close = base + i * 0.2 + 4.0 * math.sin(i / 7.0)
        open_ = prev
        bars.append(
            {
                "t": f"{d.isoformat()}T05:00:00Z",
                "o": round(open_, 2),
                "h": round(max(open_, close) + 1.2, 2),
                "l": round(min(open_, close) - 1.2, 2),
                "c": round(close, 2),
                "v": 5_000_000 + i,
            }
        )
        prev = close
        d += timedelta(days=1)
    return {"symbol": "X", "bars": bars}


def _mock_sec(filings: list[tuple[str, str, str]] | None = None) -> None:
    """SEC EDGAR: справочник CIK и submissions; filings = [(form, date, items)]."""
    filings = filings or [("10-Q", "2026-08-01", "")]
    respx.get(SEC_TICKERS).mock(
        return_value=httpx.Response(
            200,
            json={
                "0": {"cik_str": 320193, "ticker": "AAPL"},
                "1": {"cik_str": 999, "ticker": "SPY"},
            },
        )
    )
    respx.get(SEC_SUBMISSIONS).mock(
        return_value=httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": [f[0] for f in filings],
                        "filingDate": [f[1] for f in filings],
                        "accessionNumber": [f"acc-{i}" for i in range(len(filings))],
                        "items": [f[2] for f in filings],
                    }
                }
            },
        )
    )


def _mock_fmp_fundamentals() -> None:
    _mock_sec()
    respx.get(FMP_QUOTE).mock(
        return_value=httpx.Response(
            200, json=[{"symbol": "AAPL", "price": 201.5, "timestamp": 1758139200}]
        )
    )
    respx.get(FMP_PROFILE).mock(
        return_value=httpx.Response(
            200,
            json=[{"companyName": "Apple Inc.", "exchange": "NASDAQ", "sector": "Technology"}],
        )
    )
    respx.get(FMP_RATIOS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "grossProfitMargin": 0.44,
                    "debtEquityRatio": 1.1,
                    "priceEarningsRatio": 28.0,
                    "eps": 6.0,
                }
            ],
        )
    )
    respx.get(FMP_METRICS).mock(
        return_value=httpx.Response(200, json=[{"eps": 6.0, "forwardPE": 24.0}])
    )
    respx.get(FMP_GROWTH).mock(
        return_value=httpx.Response(200, json=[{"growthRevenue": 0.12, "growthEPS": 0.18}])
    )
    respx.get(FMP_EARNINGS).mock(return_value=httpx.Response(200, json=[{"date": "2099-01-15"}]))
    respx.get(FMP_NEWS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "title": "Company beats estimates and raises guidance",
                    "publishedDate": RECENT_NEWS_TS,
                    "url": "https://example.test/news/1",
                }
            ],
        )
    )


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
        alpaca=AlpacaClient(
            api_key_id="I", api_secret_key="S", max_retries=1, health_recorder=monitor
        ),
        sec_edgar=SECEdgarClient(
            user_agent="test test@example.com", max_retries=1, health_recorder=monitor
        ),
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
    # полный набор данных: без флага missing_data и без пометки «неполный»
    assert "missing_data" not in [f["flag"] for f in signal.risk_flags]
    assert "неполн" not in text1
    # текущая котировка и закрытие EOD показаны раздельно, профиль подключён
    assert "Цена: $201.50 (котировка fmp" in text1
    assert "Закрытие EOD" in text1
    assert "Apple Inc." in text1
    assert signal.price == 201.5

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
    respx.get(FMP_HIST).mock(return_value=httpx.Response(503))  # FMP цены — down
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(200, json=_alpaca_bars()))
    _mock_fmp_fundamentals()  # fundamentals из FMP ok
    monitor = SourceHealthMonitor()
    service = build_service(session_factory, monitor)

    text, sid = await service.analyze("AAPL")

    assert sid is not None  # расчёт получился через резерв
    assert "Final Score" in text
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    assert signal.price is not None  # цены получены (из резерва)
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


# --------------------------------------------------------------------------- #
# Таймаут / сетевой сбой основного источника → резерв (чек-лист 1.1.7, 1.10.5)
# --------------------------------------------------------------------------- #
@respx.mock
async def test_fmp_timeout_switches_to_reserve_and_mode_is_visible(session_factory):
    respx.get(FMP_HIST).mock(side_effect=httpx.ReadTimeout("fmp timeout"))
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(200, json=_alpaca_bars()))
    _mock_fmp_fundamentals()
    monitor = SourceHealthMonitor(session_factory)
    service = build_service(session_factory, monitor)

    text, sid = await service.analyze("AAPL")

    assert sid is not None
    assert "РЕЗЕРВНЫЙ" in text and "alpaca" in text
    assert monitor.snapshot()["fmp"]["last_error"].startswith("timeout")

    mode = await latest_data_mode(session_factory)
    assert mode["mode"] == "reserve"
    assert mode["price_history"]["source"] == "alpaca"
    assert mode["price_history"]["mode"] == "reserve"
    assert mode["fundamentals"]["mode"] == "primary"


@respx.mock
async def test_fmp_network_error_without_reserve_gives_clear_message(session_factory):
    respx.get(FMP_HIST).mock(side_effect=httpx.ConnectError("refused"))
    _mock_fmp_fundamentals()
    router = SourceRouter(fmp=FMPClient(api_key="K", max_retries=1))
    service = BotService(
        Pipeline(IngestionService(router), session_factory), session_factory
    )

    text, sid = await service.analyze("AAPL")

    assert sid is None
    assert "сетевая ошибка" in text and "FMP" in text
    assert "не найден" not in text


# --------------------------------------------------------------------------- #
# Неизвестный тикер: FMP отвечает 200 и [] → понятная ошибка, запись не создаётся
# --------------------------------------------------------------------------- #
@respx.mock
async def test_unknown_ticker_with_real_clients(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=[]))
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(200, json={"bars": None}))
    for pattern in (FMP_RATIOS, FMP_METRICS, FMP_GROWTH, FMP_EARNINGS, FMP_NEWS):
        respx.get(pattern).mock(return_value=httpx.Response(200, json=[]))
    service = build_service(session_factory, SourceHealthMonitor())

    text, sid = await service.analyze("ZZZZQ")

    assert sid is None
    assert "Тикер ZZZZQ не найден" in text
    async with session_factory() as s:
        count = await s.scalar(select(func.count()).select_from(Signal))
    assert count == 0


# --------------------------------------------------------------------------- #
# Ограничение тарифа / неверный ключ → не выдаётся за «тикер не найден»
# --------------------------------------------------------------------------- #
@respx.mock
async def test_fmp_plan_restriction_message(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    # тариф не даёт ни одного из трёх запросов отчётности → честный отказ
    for pattern in (FMP_RATIOS, FMP_METRICS, FMP_GROWTH):
        respx.get(pattern).mock(return_value=httpx.Response(402, json={"Error": "Premium"}))
    service = build_service(session_factory, SourceHealthMonitor())

    text, sid = await service.analyze("AAPL")

    assert sid is None
    assert "HTTP 402" in text and "тариф" in text
    assert "не найден" not in text


@respx.mock
async def test_partial_fundamentals_failure_follows_quality_rule(session_factory):
    # сбой key-metrics: теряется только forward P/E → Score с пониженной достоверностью
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(FMP_METRICS).mock(return_value=httpx.Response(402, json={"Error": "Premium"}))
    service = build_service(session_factory, SourceHealthMonitor())

    text, sid = await service.analyze("AAPL")

    assert sid is not None and "достоверность: пониженная" in text
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    quality = signal.raw_input_snapshot["data_quality"]
    assert signal.final_score is not None
    assert quality["reduced"] == ["fundamentals: недоступны запросы key-metrics (fmp: auth)"]
    assert signal.raw_input_snapshot["fundamentals"]["forward_pe"] is None
    assert signal.raw_input_snapshot["fundamentals"]["pe"] == 28.0  # Valuation по P/E

    # сбой income-statement-growth: нет двух growth-показателей → тоже reduced,
    # но фактор Growth не рассчитать → Final Score не выдаётся (фактор — критично)
    respx.get(FMP_METRICS).mock(
        return_value=httpx.Response(200, json=[{"eps": 6.0, "forwardPE": 24.0}])
    )
    respx.get(FMP_GROWTH).mock(side_effect=httpx.ReadTimeout("slow"))
    text, sid = await service.analyze("AAPL")
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    quality = signal.raw_input_snapshot["data_quality"]
    assert any(
        "income-statement-growth (fmp: timeout)" in reason for reason in quality["reduced"]
    )
    assert "фактор growth не рассчитан" in quality["critical"]
    assert signal.final_score is None


# --------------------------------------------------------------------------- #
# Полное воспроизведение: исходные данные → метрики → факторы → Score → Risk
# --------------------------------------------------------------------------- #
@respx.mock
async def test_replay_from_stored_raw_inputs(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    service = build_service(session_factory, SourceHealthMonitor())
    _, sid = await service.analyze("AAPL")

    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    raw = signal.raw_input_snapshot["raw_inputs"]
    assert len(raw["price_history"]["bars"]) == 260  # все бары, на которых шёл расчёт
    assert raw["benchmark"]["ticker"] == "SPY"
    assert raw["news"][0]["title"].startswith("Company beats")
    assert raw["calc_date"]

    replay = replay_signal(signal)
    assert replay.final.final_score == signal.final_score
    assert replay.final.factor_scores == {
        name: getattr(signal.factor_scores, name) for name in replay.final.factor_scores
    }
    assert replay.features.model_dump(mode="json") == signal.raw_input_snapshot["features"]
    assert [f.model_dump() for f in replay.risk.active_flags] == signal.risk_flags



# --------------------------------------------------------------------------- #
# Полнота данных, два уровня (v1.2+)
# --------------------------------------------------------------------------- #
async def _analyze(session_factory, bars: int = 260) -> tuple[str, Signal]:
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist(bars)))
    _mock_fmp_fundamentals()
    service = build_service(session_factory, SourceHealthMonitor())
    text, _ = await service.analyze("AAPL")
    async with session_factory() as s:
        return text, (await list_signals(s, "AAPL"))[0]


@respx.mock
async def test_full_data_has_high_confidence(session_factory):
    text, signal = await _analyze(session_factory)
    assert signal.raw_input_snapshot["data_quality"]["confidence"] == "high"
    assert "достоверность: высокая" in text


@respx.mock
async def test_short_history_reduces_confidence(session_factory):
    # 249 баров: все метрики, включая EMA200, считаются → Score выдаётся
    text, signal = await _analyze(session_factory, bars=249)

    quality = signal.raw_input_snapshot["data_quality"]
    assert quality["confidence"] == "reduced"
    assert signal.final_score is not None
    flags = {f["flag"]: f["reason"] for f in signal.risk_flags}
    assert "249 < 250" in flags["missing_data"]
    assert "достоверность: пониженная" in text and "249 &lt; 250" in text
    assert replay_signal(signal).risk.flag_names() == [f["flag"] for f in signal.risk_flags]


@respx.mock
async def test_very_short_history_blocks_final_score(session_factory):
    # < 200 баров: EMA200 не рассчитать → Final Score не выдаётся, факторы сохранены
    text, signal = await _analyze(session_factory, bars=150)

    assert signal.final_score is None
    assert signal.factor_scores.momentum is not None
    quality = signal.raw_input_snapshot["data_quality"]
    assert quality["confidence"] == "insufficient"
    assert any("150 < 200" in reason for reason in quality["critical"])
    assert "не рассчитан" in text and "150 &lt; 200" in text
    assert "Score не рассчитан" in await build_service(
        session_factory, SourceHealthMonitor()
    ).history("AAPL")
    replay = replay_signal(signal)
    assert replay.final.final_score is None
    assert replay.data_quality.confidence == "insufficient"


@respx.mock
async def test_missing_fundamentals_levels(session_factory):
    # нет gross margin и D/E (2 из 6) — фактор Fundamentals считается по EPS
    # → пониженная достоверность
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(FMP_RATIOS).mock(
        return_value=httpx.Response(200, json=[{"priceEarningsRatio": 28.0, "eps": 6.0}])
    )
    service = build_service(session_factory, SourceHealthMonitor())
    await service.analyze("AAPL")
    async with session_factory() as s:
        two_missing = (await list_signals(s, "AAPL"))[0]
    assert two_missing.raw_input_snapshot["data_quality"]["confidence"] == "reduced"
    assert two_missing.final_score is not None

    # + нет роста выручки (3 из 6) → Final Score не выдаётся
    respx.get(FMP_GROWTH).mock(return_value=httpx.Response(200, json=[{"growthEPS": 0.18}]))
    await service.analyze("AAPL")
    async with session_factory() as s:
        three_missing = (await list_signals(s, "AAPL"))[0]
    quality = three_missing.raw_input_snapshot["data_quality"]
    assert quality["confidence"] == "insufficient"
    assert three_missing.final_score is None


# --------------------------------------------------------------------------- #
# SEC: событие 8-K влияет на Catalysts; сбой SEC не скрывается
# --------------------------------------------------------------------------- #
async def _score_with_filings(session_factory, filings) -> Signal:
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    _mock_sec(filings)
    service = build_service(session_factory, SourceHealthMonitor())
    _, sid = await service.analyze("AAPL")
    async with session_factory() as s:
        return (await list_signals(s, "AAPL"))[0]


@respx.mock
async def test_sec_negative_8k_lowers_catalysts(session_factory):
    today = date.today().isoformat()
    neutral = await _score_with_filings(session_factory, [("10-Q", today, "")])
    negative = await _score_with_filings(session_factory, [("8-K", today, "4.02,9.01")])

    assert negative.factor_scores.catalysts < neutral.factor_scores.catalysts
    assert negative.final_score < neutral.final_score
    ctx = negative.raw_input_snapshot["catalysts"]
    assert ctx["sec_event_score"] == -1.0
    assert ctx["evidence"][0]["kind"] == "sec_event"


@respx.mock
async def test_sec_unavailable_is_flagged(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(SEC_SUBMISSIONS).mock(side_effect=httpx.ReadTimeout("sec down"))
    service = build_service(session_factory, SourceHealthMonitor())

    text, sid = await service.analyze("AAPL")

    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    flags = {f["flag"]: f["reason"] for f in signal.risk_flags}
    assert "SEC EDGAR недоступен (sec_edgar: timeout)" in flags["missing_data"]
    assert signal.raw_input_snapshot["catalysts"]["filings_available"] is False
    # SEC недоступен, новости есть → Score выдаётся с пониженной достоверностью
    assert signal.final_score is not None
    assert signal.raw_input_snapshot["data_quality"]["confidence"] == "reduced"
    assert "достоверность: пониженная" in text


# --------------------------------------------------------------------------- #
# Telegram HTML: внешний текст экранируется
# --------------------------------------------------------------------------- #
@respx.mock
async def test_external_text_is_html_escaped(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(FMP_NEWS).mock(
        return_value=httpx.Response(
            200,
            json=[{"title": "AT&T <beats> estimates", "publishedDate": RECENT_NEWS_TS}],
        )
    )
    service = build_service(session_factory, SourceHealthMonitor())
    _, sid = await service.analyze("AAPL")

    details = await service.section(sid, "details")
    assert "AT&amp;T &lt;beats&gt; estimates" in details
    assert "<beats>" not in details


# --------------------------------------------------------------------------- #
# Режим данных: недоступные некритичные наборы → degraded, а не primary
# --------------------------------------------------------------------------- #
@respx.mock
async def test_missing_noncritical_sources_mark_mode_degraded(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(FMP_NEWS).mock(return_value=httpx.Response(503))
    respx.get(FMP_QUOTE).mock(return_value=httpx.Response(503))
    respx.get(FMP_PROFILE).mock(return_value=httpx.Response(503))
    respx.get(re.compile(r".*/v2/stocks/AAPL/trades/latest")).mock(
        return_value=httpx.Response(503)
    )
    service = build_service(session_factory, SourceHealthMonitor(session_factory))

    text, sid = await service.analyze("AAPL")

    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    sources = signal.raw_input_snapshot["sources"]
    assert sources["price_history"]["mode"] == "primary"
    assert sources["news"]["mode"] == "unavailable"
    assert sources["quote"]["mode"] == "unavailable"
    assert sources["filings"]["mode"] == "primary"  # SEC участвует в режиме явно
    assert sources["mode"] == "degraded"
    assert "ЧАСТИЧНЫЙ" in text and "новости" in text and "котировка" in text
    assert (await latest_data_mode(session_factory))["mode"] == "degraded"


@respx.mock
async def test_sec_down_marks_mode_degraded(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(SEC_SUBMISSIONS).mock(return_value=httpx.Response(503))
    service = build_service(session_factory, SourceHealthMonitor())

    text, _ = await service.analyze("AAPL")

    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    assert signal.raw_input_snapshot["sources"]["filings"]["mode"] == "unavailable"
    assert signal.raw_input_snapshot["sources"]["mode"] == "degraded"
    assert "SEC EDGAR" in text


# --------------------------------------------------------------------------- #
# Резерв Alpaca: цены согласованы с FMP по сплитам (adjustment=split)
# --------------------------------------------------------------------------- #
def _alpaca_split_bars(adjusted: bool) -> dict:
    """260 дней, сплит 4:1 за 30 дней до конца: raw-цены до сплита в 4 раза выше."""
    data = _alpaca_bars()
    split_index = len(data["bars"]) - 30
    if not adjusted:
        for bar in data["bars"][:split_index]:
            for key in ("o", "h", "l", "c"):
                bar[key] = round(bar[key] * 4, 2)
    return data


@respx.mock
async def test_alpaca_fallback_uses_split_adjusted_prices(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(503))
    _mock_fmp_fundamentals()

    def alpaca_bars(request: httpx.Request) -> httpx.Response:
        adjusted = request.url.params.get("adjustment") == "split"
        return httpx.Response(200, json=_alpaca_split_bars(adjusted))

    route = respx.get(ALPACA_BARS).mock(side_effect=alpaca_bars)
    service = build_service(session_factory, SourceHealthMonitor())

    _, sid = await service.analyze("AAPL")

    assert route.calls[0].request.url.params["adjustment"] == "split"
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    features = signal.raw_input_snapshot["features"]
    # без скачка ×4: цена у EMA200, ATR нормальный, флагов волатильности/гэпа нет
    assert abs(features["distance_to_ema50"]) < 15
    assert features["atr_pct"] < 0.05
    flags = [f["flag"] for f in signal.risk_flags]
    assert "high_volatility" not in flags and "gap_risk" not in flags


# --------------------------------------------------------------------------- #
# Ревью aae5c40: Risk Filter по версии, неизвестная дата отчётности, пустые наборы
# --------------------------------------------------------------------------- #
@respx.mock
async def test_replay_uses_stored_risk_thresholds(session_factory, monkeypatch):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    service = build_service(session_factory, SourceHealthMonitor())
    await service.analyze("AAPL")
    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    stored = signal.raw_input_snapshot["calculation"]["formula_config"]["risk_filter"]
    assert stored["high_volatility_atr_ratio"] == 0.05
    assert "high_volatility" not in [f["flag"] for f in signal.risk_flags]

    # «Новая версия» с жёстким порогом ATR: текущая конфигурация изменилась
    from app.risk import risk_filter
    from app.scoring import thresholds
    from app.scoring.thresholds import RiskFilterConfig

    strict = RiskFilterConfig(high_volatility_atr_ratio=0.0001)
    monkeypatch.setattr(risk_filter, "load_risk_config", lambda path=None: strict)
    original = thresholds.load_scoring_config
    monkeypatch.setattr(
        thresholds,
        "load_scoring_config",
        lambda path=None: original(path).model_copy(update={"risk_filter": strict}),
    )
    # при текущих порогах тот же набор метрик дал бы флаг…
    from app.features.indicators import FeatureSet

    features = FeatureSet.model_validate(signal.raw_input_snapshot["features"])
    assert "high_volatility" in risk_filter.compute_risk(features).flag_names()
    # …но повтор старого сигнала идёт по его сохранённым порогам
    replay = replay_signal(signal)
    assert replay.risk.flag_names() == [f["flag"] for f in signal.risk_flags]


@respx.mock
async def test_unknown_earnings_date_reduces_confidence(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    # API ответил, но только прошедшие даты — будущей даты отчётности нет
    respx.get(FMP_EARNINGS).mock(
        return_value=httpx.Response(200, json=[{"date": "2020-01-30"}])
    )
    service = build_service(session_factory, SourceHealthMonitor())

    text, _ = await service.analyze("AAPL")

    async with session_factory() as s:
        signal = (await list_signals(s, "AAPL"))[0]
    snap = signal.raw_input_snapshot
    assert snap["raw_inputs"]["earnings"] == {
        "next_earnings_date": None, "available": False, "api_available": True
    }
    assert snap["data_quality"]["confidence"] == "reduced"
    assert any("не вернул будущую дату" in r for r in snap["data_quality"]["reduced"])
    assert snap["sources"]["earnings"]["mode"] == "no_data"
    assert snap["sources"]["earnings"]["source"] == "fmp"
    assert "missing_data" in [f["flag"] for f in signal.risk_flags]
    assert "достоверность: пониженная" in text


@respx.mock
async def test_empty_feeds_keep_real_source_and_time(session_factory):
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    respx.get(FMP_NEWS).mock(return_value=httpx.Response(200, json=[]))
    _mock_sec([("4", "2026-09-01", "")])  # только формы вне списка → пустой набор
    service = build_service(session_factory, SourceHealthMonitor())

    await service.analyze("AAPL")

    async with session_factory() as s:
        sources = (await list_signals(s, "AAPL"))[0].raw_input_snapshot["sources"]
    assert sources["news"]["source"] == "fmp" and sources["news"]["sources"] == []
    assert sources["news"]["mode"] == "no_data" and sources["news"]["fetched_at"]
    assert sources["filings"]["source"] == "sec_edgar"
    assert sources["filings"]["mode"] == "no_data" and sources["filings"]["fetched_at"]


@respx.mock
async def test_profile_tables_are_filled(session_factory):
    from app.db.models import (
        FundamentalsSnapshot,
        MarketContext,
        PriceHistory,
        TelegramUser,
        Ticker,
    )

    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    service = build_service(session_factory, SourceHealthMonitor())
    _, sid1 = await service.analyze("AAPL")
    _, sid2 = await service.analyze("AAPL")  # повтор: бары не дублируются
    await service.touch_user(7_000_000_001, "tester")  # ID > 2^31
    await service.touch_user(7_000_000_001, "tester2")

    async with session_factory() as s:
        ticker = await s.get(Ticker, "AAPL")
        bars = await s.scalar(
            select(func.count()).select_from(PriceHistory).where(PriceHistory.ticker == "AAPL")
        )
        spy_bars = await s.scalar(
            select(func.count()).select_from(PriceHistory).where(PriceHistory.ticker == "SPY")
        )
        fund = (await s.execute(select(FundamentalsSnapshot))).scalars().all()
        ctx = (await s.execute(select(MarketContext))).scalars().all()
        user = await s.get(TelegramUser, 7_000_000_001)
    assert ticker.name == "Apple Inc." and ticker.exchange == "NASDAQ"
    assert bars == 260 and spy_bars == 260
    assert [f.signal_id for f in fund] == [sid1, sid2] and fund[0].pe == 28.0
    assert [c.signal_id for c in ctx] == [sid1, sid2]
    assert ctx[0].next_earnings_date == date(2099, 1, 15)
    assert ctx[0].avg_volume_20d is not None
    assert user.username == "tester2"


@respx.mock
async def test_signal_and_context_are_saved_atomically(session_factory, monkeypatch):
    from app import pipeline as pipeline_module
    from app.db.models import FundamentalsSnapshot, MarketContext, PriceHistory, Ticker

    original = pipeline_module.save_calculation_context

    async def fail_after_writing(session, **kwargs):
        await original(session, **kwargs)  # строки уже добавлены в транзакцию…
        raise RuntimeError("disk full")  # …и тут сбой

    monkeypatch.setattr(pipeline_module, "save_calculation_context", fail_after_writing)
    respx.get(FMP_HIST).mock(return_value=httpx.Response(200, json=_fmp_hist()))
    _mock_fmp_fundamentals()
    service = build_service(session_factory, SourceHealthMonitor())

    text, sid = await service.analyze("AAPL")

    assert sid is None
    assert "не сохранён" in text
    assert "Final Score" not in text  # результат не выдаётся без записи в историю
    async with session_factory() as s:
        for model in (Signal, FundamentalsSnapshot, MarketContext, PriceHistory, Ticker):
            assert await s.scalar(select(func.count()).select_from(model)) == 0, model
