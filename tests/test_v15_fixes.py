"""Исправления по итогам ревью Этапа 1 (формулы v1.5, эксплуатация, бот).

Каждый тест закрывает один найденный дефект или пограничный случай:
A — эксплуатация (логи, Retry-After, версия формул, конфиг, source_health);
B — логика расчёта (D/E, Relative Strength, свежесть истории, незакрытый бар,
    IEX-объёмы, P/E TTM, ошибки FMP в теле ответа, дата отчётности, окно новостей,
    отклонение вниз от EMA20);
C — Telegram (тикеры классов акций, двойной запуск, длинные сообщения).

Новые правила формул включаются только версией v1.5: записи v1.0–v1.4
воспроизводятся по прежним правилам.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
import pytest_asyncio
import respx
import yaml
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from alembic import command
from app.bot.service import TICKER_PATTERN, BotService, parse_ticker
from app.bot.templates import split_message
from app.calculation import (
    assess_data_quality,
    build_raw_inputs,
    business_days_between,
    calculate,
    replay_from_raw_inputs,
)
from app.db.models import FormulaVersionRow, SourceHealth
from app.db.repository import (
    FormulaVersionConflictError,
    check_formula_version,
    ensure_formula_version_consistent,
    get_signal,
    upsert_active_formula_version,
)
from app.db.session import create_all
from app.features.indicators import FeatureSet, relative_strength
from app.ingestion import fmp_client as fmp_module
from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.base_client import (
    MAX_RETRY_AFTER_SECONDS,
    BaseSourceClient,
    RetryableSourceError,
    SourceError,
)
from app.ingestion.finnhub_client import FinnhubClient
from app.ingestion.fmp_client import FMPClient, fmp_symbol
from app.ingestion.normalization import (
    PriceBar,
    PriceHistory,
    QualityReport,
    drop_unfinished_session_bar,
)
from app.ingestion.schemas import CompanyProfile, Earnings, Fundamentals
from app.ingestion.sec_edgar_client import SECEdgarClient
from app.ingestion.service import MarketData
from app.ingestion.source_router import SourceRouter, TickerNotFoundError
from app.monitoring.probe import run_probe_loop
from app.monitoring.source_health import SourceHealthMonitor
from app.pipeline import Pipeline
from app.risk import compute_risk
from app.scoring import (
    ScoringConfig,
    active_formula_version,
    compute_factor_scores,
    load_scoring_config,
)
from app.scoring.thresholds import DataQualityConfig, RiskFilterConfig
from tests.conftest import FakeHealthRecorder

NY = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
V15 = load_scoring_config()  # config/thresholds.yaml — v1.5
LEGACY = ScoringConfig()  # значения по умолчанию = правила v1.4


def _history(
    n: int,
    *,
    last: date,
    ticker: str = "X",
    volume: float = 2_000_000,
    source: str = "fmp",
    volume_partial: bool = False,
) -> PriceHistory:
    bars = []
    prev = 100.0
    for i in range(n):
        close = 100.0 + i * 0.2 + 3.0 * math.sin(i / 8.0)
        bars.append(
            PriceBar(
                ticker=ticker,
                date=last - timedelta(days=n - 1 - i),
                open=prev,
                high=max(prev, close) + 1.0,
                low=min(prev, close) - 1.0,
                close=close,
                volume=volume,
                source=source,
                fetched_at=NOW,
            )
        )
        prev = close
    return PriceHistory(
        ticker=ticker, bars=bars, source=source, fetched_at=NOW, volume_partial=volume_partial
    )


FUND = Fundamentals(
    ticker="X",
    revenue_growth=0.1,
    eps_growth=0.1,
    eps=2.0,
    gross_margin=0.4,
    debt_equity=0.5,
    pe=20.0,
    source="fmp",
    fetched_at=NOW,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    await create_all(engine)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# =========================================================================== #
# A. Эксплуатация
# =========================================================================== #
@respx.mock
async def test_api_key_is_not_written_to_logs(caplog):
    """A1: httpx на INFO писал URL с apikey — ключ FMP попадал в логи."""
    respx.get(re.compile(r".*/stable/quote")).mock(
        return_value=httpx.Response(200, json=[{"price": 1.0}])
    )
    client = FMPClient(api_key="SECRET_KEY_123", max_retries=1)
    with caplog.at_level(logging.INFO):
        await client.get_quote("AAPL")
    await client.aclose()
    assert "SECRET_KEY_123" not in caplog.text


async def test_retry_after_wait_is_capped():
    """A2: Retry-After: 3600 не должен подвешивать бот на час."""
    client = BaseSourceClient(source="t", base_url="https://x.test", max_retries=2)
    wait = client._wait_strategy()

    class Outcome:
        def __init__(self, exc):
            self._exc = exc

        def exception(self):
            return self._exc

    class State:
        attempt_number = 1

        def __init__(self, retry_after):
            self.outcome = Outcome(
                RetryableSourceError("x", source="t", status_code=429, retry_after=retry_after)
            )

    assert wait(State(3600)) == MAX_RETRY_AFTER_SECONDS
    assert wait(State(2)) == 2
    assert wait(State(-5)) == 0
    await client.aclose()


async def test_same_formula_version_with_other_params_is_rejected(session_factory):
    """A3: правка thresholds.yaml без смены номера версии не перезаписывает журнал."""
    version = active_formula_version(V15)
    async with session_factory() as session:
        await upsert_active_formula_version(session, version)
        # та же версия и те же параметры — повторный upsert допустим
        await upsert_active_formula_version(session, version)

    changed_cfg = V15.model_copy(
        update={
            "final_score_weights": V15.final_score_weights.model_copy(
                update={"momentum": 0.25, "volume": 0.05}
            )
        }
    )
    changed = active_formula_version(changed_cfg)
    assert changed.version == version.version
    async with session_factory() as session:
        with pytest.raises(FormulaVersionConflictError, match="нового номера version"):
            await upsert_active_formula_version(session, changed)
    with pytest.raises(FormulaVersionConflictError):
        await ensure_formula_version_consistent(session_factory, changed_cfg)

    # журнал не изменился: в БД остались исходные веса
    async with session_factory() as session:
        row = await session.get(FormulaVersionRow, version.version)
        assert row.weights["momentum"] == 0.20
        await check_formula_version(session, version)  # исходная версия — без конфликта


async def test_new_formula_version_number_is_accepted(session_factory):
    async with session_factory() as session:
        await upsert_active_formula_version(session, active_formula_version(V15))
    bumped = V15.model_copy(update={"version": "v1.6-test"})
    await ensure_formula_version_consistent(session_factory, bumped)
    async with session_factory() as session:
        await upsert_active_formula_version(session, active_formula_version(bumped))
        rows = (await session.execute(select(FormulaVersionRow))).scalars().all()
    assert {r.version: r.is_active for r in rows} == {"v1.5": False, "v1.6-test": True}


def test_config_is_mounted_into_app_and_bot():
    """A4: пороги меняются правкой config/ на хосте и перезапуском, без пересборки."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for service in ("app", "bot"):
        assert "./config:/app/config:ro" in compose["services"][service]["volumes"]


async def test_source_health_purge_removes_only_old_rows(session_factory):
    """A5: лента source_health не растёт бесконечно."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add_all(
            [
                SourceHealth(source="fmp", checked_at=now - timedelta(days=40), status="ok"),
                SourceHealth(source="fmp", checked_at=now - timedelta(days=31), status="error"),
                SourceHealth(source="fmp", checked_at=now - timedelta(days=1), status="ok"),
            ]
        )
        await session.commit()
    monitor = SourceHealthMonitor(session_factory)
    assert await monitor.purge_older_than(30) == 2
    assert await monitor.purge_older_than(0) == 0  # 0 — очистка выключена
    async with session_factory() as session:
        left = (await session.execute(select(SourceHealth))).scalars().all()
    assert len(left) == 1


async def test_probe_loop_purges_old_health_rows():
    class EmptyRouter:
        def configured_clients(self):
            return {}

    class Recorder(FakeHealthRecorder):
        def __init__(self):
            super().__init__()
            self.purged: list[int] = []

        async def purge_older_than(self, days: int) -> int:
            self.purged.append(days)
            return 0

    recorder = Recorder()
    task = asyncio.create_task(run_probe_loop(EmptyRouter(), recorder, 3600, retention_days=30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recorder.purged == [30]


def test_migration_creates_source_health_indexes(tmp_path):
    db_path = tmp_path / "idx.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "head")
    engine = create_sync_engine(f"sqlite:///{db_path}")
    names = {ix["name"] for ix in inspect(engine).get_indexes("source_health")}
    engine.dispose()
    assert {
        "ix_source_health_source_checked_at",
        "ix_source_health_source_status_checked_at",
        "ix_source_health_checked_at",
    } <= names


# =========================================================================== #
# B. Логика расчёта
# =========================================================================== #
def _fund(**update) -> Fundamentals:
    return FUND.model_copy(update=update)


def test_negative_equity_gets_worst_debt_score():
    """B1: D/E < 0 (отрицательный капитал) раньше давал лучший балл D/E."""
    features = FeatureSet(ticker="X")
    negative = compute_factor_scores(features, _fund(debt_equity=-5.0), V15).fundamentals
    healthy = compute_factor_scores(features, _fund(debt_equity=0.3), V15).fundamentals
    assert negative.score < healthy.score
    assert "отрицательный капитал" in negative.rule
    # EPS 70, маржа 40, D/E 0 → среднее 36.67
    assert negative.score == pytest.approx((70 + 40 + 0) / 3)
    # записи v1.4 воспроизводятся по прежнему правилу
    legacy = compute_factor_scores(features, _fund(debt_equity=-5.0), LEGACY).fundamentals
    assert legacy.score == pytest.approx((70 + 40 + 100) / 3)


def test_relative_strength_is_aligned_by_date():
    """B2: у тикера нет трёх последних баров — сравнивается одно и то же окно дат."""
    index = [date(2026, 1, 1) + timedelta(days=i) for i in range(100)]
    spy = pd.Series([100.0 + i for i in range(100)], index=index)
    ticker = spy.iloc[:-3]  # та же динамика, но ряд обрывается на 3 бара раньше
    assert relative_strength(ticker, spy, 63, align_by_date=True) == pytest.approx(0.0)
    assert relative_strength(ticker, spy, 63) != pytest.approx(0.0)  # правило до v1.5


def test_relative_strength_uses_alignment_from_formula_version():
    last = date(2026, 10, 1)
    spy = _history(260, last=last, ticker="SPY")
    lagging = PriceHistory(
        ticker="X", bars=spy.bars[:-3], source="fmp", fetched_at=NOW
    )
    common = dict(
        price_history=lagging,
        benchmark=spy,
        fundamentals=FUND,
        next_earnings_date=None,
        earnings_available=False,
        news=[],
        filings=[],
        calc_date=last,
    )
    assert calculate(**common, config=V15).features.relative_strength_63d == pytest.approx(0)
    assert calculate(**common, config=LEGACY).features.relative_strength_63d != pytest.approx(0)


def _assess(cfg: DataQualityConfig, history: PriceHistory, **kw):
    return assess_data_quality(
        cfg=cfg,
        price_history=history,
        benchmark=kw.pop("benchmark", history),
        fundamentals=FUND,
        features=FeatureSet(ticker="X", price=10.0, avg_volume_20d=1_000_000),
        factor_scores=dict.fromkeys(
            ("momentum", "growth", "fundamentals", "relative_strength", "volume",
             "valuation", "catalysts"),
            50,
        ),
        earnings_available=True,
        news_available=True,
        filings_available=True,
        **kw,
    )


def test_business_days_between():
    friday, monday = date(2026, 10, 2), date(2026, 10, 5)
    assert business_days_between(friday, monday) == 1
    assert business_days_between(friday, friday) == 0
    assert business_days_between(monday, friday) == 0
    assert business_days_between(date(2026, 9, 25), monday) == 6


def test_stale_history_blocks_final_score():
    """B3: тикер не торгуется / источник отдаёт старые данные → не «высокая достоверность»."""
    cfg = V15.data_quality
    calc = date(2026, 10, 5)  # понедельник
    fresh = _assess(cfg, _history(260, last=date(2026, 10, 2)), calc_date=calc)  # пятница
    assert fresh.confidence == "high"
    stale = _assess(cfg, _history(260, last=date(2026, 9, 1)), calc_date=calc)
    assert stale.confidence == "insufficient"
    assert "история цен устарела: последний бар 2026-09-01" in stale.critical[0]
    # до v1.5 проверки не было
    legacy = _assess(DataQualityConfig(), _history(260, last=date(2026, 9, 1)), calc_date=calc)
    assert legacy.confidence == "high"


def test_not_actively_trading_is_critical():
    history = _history(260, last=date(2026, 10, 2))
    quality = _assess(
        V15.data_quality, history, calc_date=date(2026, 10, 2), actively_trading=False
    )
    assert quality.critical == ["по данным профиля эмитента бумага не торгуется"]
    unknown = _assess(V15.data_quality, history, calc_date=date(2026, 10, 2), actively_trading=None)
    assert unknown.confidence == "high"


def test_actively_trading_is_replayed_from_stored_profile():
    last = date(2026, 10, 1)
    history = _history(260, last=last)
    inputs = dict(
        price_history=history,
        benchmark=_history(260, last=last, ticker="SPY"),
        fundamentals=FUND,
        next_earnings_date=None,
        earnings_available=True,
        news=[],
        filings=[],
        calc_date=last,
    )
    profile = CompanyProfile(
        ticker="X", is_actively_trading=False, source="fmp", fetched_at=NOW
    )
    live = calculate(**inputs, config=V15, actively_trading=False)
    replayed = replay_from_raw_inputs(build_raw_inputs(**inputs, profile=profile), V15)
    assert live.final.final_score is None
    assert replayed.data_quality == live.data_quality


@pytest.mark.parametrize(
    "now_et,dropped",
    [
        (datetime(2026, 10, 2, 11, 0, tzinfo=NY), True),  # идёт сессия
        (datetime(2026, 10, 2, 16, 10, tzinfo=NY), True),  # закрытие, объёмы не итоговые
        (datetime(2026, 10, 2, 16, 30, tzinfo=NY), False),  # день завершён
        (datetime(2026, 10, 3, 11, 0, tzinfo=NY), False),  # следующий день: бар вчерашний
    ],
)
def test_unfinished_session_bar_is_dropped(now_et, dropped):
    """B4: бар текущей сессии с неполным объёмом не участвует в расчёте."""
    history = _history(30, last=date(2026, 10, 2))
    result, dropped_date = drop_unfinished_session_bar(history, now_et.astimezone(UTC))
    assert (dropped_date is not None) is dropped
    assert len(result.bars) == (29 if dropped else 30)


class _FakeIngestion:
    def __init__(self, market: MarketData, benchmark: PriceHistory):
        self._market = market
        self._benchmark = benchmark

    async def get_market_data(self, ticker):
        return self._market

    async def get_price_history(self, ticker):
        return self._benchmark, QualityReport(source="fmp")


def _market(history: PriceHistory, **kw) -> MarketData:
    return MarketData(
        ticker=history.ticker,
        price_history=history,
        fundamentals=FUND.model_copy(update={"ticker": history.ticker}),
        earnings=Earnings(ticker=history.ticker, source="fmp", fetched_at=NOW),
        quality={},
        is_incomplete=False,
        **kw,
    )


async def test_pipeline_excludes_unfinished_bar_and_says_so(session_factory):
    today = date(2026, 10, 2)
    history = _history(260, last=today)
    benchmark = _history(260, last=today, ticker="SPY")
    pipeline = Pipeline(
        _FakeIngestion(_market(history), benchmark),
        session_factory,
        clock=lambda: datetime(2026, 10, 2, 11, 0, tzinfo=NY),
    )
    service = BotService(pipeline, session_factory)
    text, signal_id = await service.analyze("X")
    assert signal_id is not None
    assert "бар текущей сессии 2026-10-02 ещё не закрыт" in text
    async with session_factory() as session:
        signal = await get_signal(session, signal_id)
    raw = signal.raw_input_snapshot["raw_inputs"]
    assert raw["price_history"]["bars"][-1][0] == "2026-10-01"
    assert raw["benchmark"]["bars"][-1][0] == "2026-10-01"
    assert raw["calc_date"] == "2026-10-02"


@respx.mock
async def test_alpaca_iex_feed_marks_partial_volume():
    """B5: бесплатный фид IEX — доля рынка; sip — полный объём."""
    route = respx.get(re.compile(r".*/v2/stocks/X/bars")).mock(
        return_value=httpx.Response(
            200,
            json={"bars": [{"t": "2026-10-01T04:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2,
                            "v": 1000}]},
        )
    )
    iex = AlpacaClient(api_key_id="i", api_secret_key="s", max_retries=1)
    sip = AlpacaClient(api_key_id="i", api_secret_key="s", max_retries=1, feed="sip")
    assert (await iex.get_price_history("X")).volume_partial is True
    assert route.calls.last.request.url.params["feed"] == "iex"
    assert (await sip.get_price_history("X")).volume_partial is False
    assert route.calls.last.request.url.params["feed"] == "sip"
    await iex.aclose()
    await sip.aclose()


def test_partial_volume_skips_liquidity_flag_and_reduces_confidence():
    last = date(2026, 10, 1)
    inputs = dict(
        price_history=_history(260, last=last, volume=20_000, source="alpaca",
                               volume_partial=True),
        benchmark=_history(260, last=last, ticker="SPY"),
        fundamentals=FUND,
        next_earnings_date=date(2099, 1, 1),
        earnings_available=True,
        news=[],
        filings=[],
        calc_date=last,
    )
    result = calculate(**inputs, config=V15)
    assert "liquidity_risk" not in result.risk.flag_names()
    assert result.data_quality.confidence == "reduced"
    assert "порог ликвидности не проверен" in result.data_quality.reduced[0]
    # тот же объём из полного фида — флаг ликвидности есть
    full = calculate(
        **{**inputs, "price_history": _history(260, last=last, volume=20_000)}, config=V15
    )
    assert "liquidity_risk" in full.risk.flag_names()
    # признак сохраняется в исходных данных и учитывается при повторе
    replayed = replay_from_raw_inputs(build_raw_inputs(**inputs), V15)
    assert replayed.risk == result.risk


def test_valuation_prefers_ttm_pe_over_annual():
    """B6: годовой P/E отстаёт на год — без forward P/E берётся P/E TTM."""
    features = FeatureSet(ticker="X")
    fund = _fund(pe=40.0, pe_ttm=10.0, forward_pe=None)
    v15 = compute_factor_scores(features, fund, V15).valuation
    assert v15.score == 100.0 and "P/E TTM" in v15.rule and v15.inputs["pe_ttm"] == 10.0
    assert compute_factor_scores(features, fund, LEGACY).valuation.score == 0.0
    with_forward = compute_factor_scores(features, _fund(pe_ttm=10.0, forward_pe=40.0), V15)
    assert with_forward.valuation.score == 0.0  # forward P/E по-прежнему приоритетен


@respx.mock
async def test_ratios_ttm_failure_does_not_make_fundamentals_incomplete():
    respx.get(re.compile(r".*/stable/ratios-ttm")).mock(return_value=httpx.Response(402))
    respx.get(re.compile(r".*/stable/ratios(\?|$)")).mock(
        return_value=httpx.Response(200, json=[{"priceToEarningsRatio": 30.0}])
    )
    respx.get(re.compile(r".*/stable/key-metrics")).mock(return_value=httpx.Response(200, json=[]))
    respx.get(re.compile(r".*/stable/income-statement-growth")).mock(
        return_value=httpx.Response(200, json=[])
    )
    client = FMPClient(api_key="k", max_retries=1)
    fundamentals = await client.get_fundamentals("AAPL")
    await client.aclose()
    assert fundamentals.pe == 30.0 and fundamentals.pe_ttm is None
    assert fundamentals.unavailable_parts == []


@pytest.mark.parametrize(
    "message,kind",
    [
        ("Limit Reach . Please upgrade your plan or visit our documentation", "rate_limit"),
        ("Invalid API KEY. Feel free to create a Free API Key", "auth"),
        ("Something unexpected", "invalid_response"),
    ],
)
@respx.mock
async def test_fmp_error_in_200_body_is_an_api_error(message, kind):
    """B7: {"Error Message": ...} с HTTP 200 — не «тикер не найден»."""
    respx.get(re.compile(r".*/stable/historical-price-eod/full")).mock(
        return_value=httpx.Response(200, json={"Error Message": message})
    )
    recorder = FakeHealthRecorder()
    router = SourceRouter(fmp=FMPClient(api_key="k", max_retries=1, health_recorder=recorder))
    with pytest.raises(Exception) as caught:
        await router.get_price_history("AAPL")
    await router.aclose()
    assert not isinstance(caught.value, TickerNotFoundError)
    assert caught.value.kind == kind
    assert recorder.statuses == ["error"]  # мониторинг видит сбой, а не успешный ответ


def test_next_earnings_date_uses_new_york_today(monkeypatch):
    """B8: в 21:00 ET (01:00 UTC следующего дня) отчётность текущего дня ещё «сегодня»."""

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(fmp_module, "datetime", FakeDateTime)
    rows = [{"date": "2026-09-30"}, {"date": "2026-10-01"}, {"date": "2026-10-20"}]
    assert fmp_module._next_earnings_date(rows) == date(2026, 10, 1)


@respx.mock
async def test_finnhub_reserve_news_window_matches_formula():
    """B9: резервный Finnhub запрашивает те же 14 дней, что и окно Catalysts."""
    route = respx.get(re.compile(r".*/company-news")).mock(
        return_value=httpx.Response(200, json=[])
    )
    client = FinnhubClient(api_key="k", max_retries=1)
    await client.get_company_news("AAPL")
    await client.aclose()
    params = route.calls.last.request.url.params
    span = date.fromisoformat(params["to"]) - date.fromisoformat(params["from"])
    assert span.days == V15.factor_scores.catalysts.news_lookback_days


def test_strong_deviation_below_ema20_is_flagged():
    """B10: сильное отклонение цены вниз от EMA20 тоже даёт флаг."""
    below = FeatureSet(ticker="X", price=80.0, distance_to_ema20=-20.0)
    risk = compute_risk(below, today=date(2026, 10, 1), config=V15.risk_filter,
                        missing_reasons=[])
    flag = next(f for f in risk.active_flags if f.flag == "overextension")
    assert "ниже EMA20 на 20.0%" in flag.reason and flag.value == -20.0
    near = FeatureSet(ticker="X", price=95.0, distance_to_ema20=-10.0)
    near_risk = compute_risk(
        near, today=date(2026, 10, 1), config=V15.risk_filter, missing_reasons=[]
    )
    assert near_risk.active_flags == []
    legacy = compute_risk(
        below, today=date(2026, 10, 1), config=RiskFilterConfig(), missing_reasons=[]
    )
    assert legacy.active_flags == []


# =========================================================================== #
# C. Telegram и тикеры классов акций
# =========================================================================== #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("BRK.B", "BRK.B"),
        ("brk-b", "BRK.B"),
        ("$BF/B", "BF.B"),
        ("GOOGL", "GOOGL"),
        ("BRK.", None),
        ("BRK.BBB", None),
        ("TOOLONG.B", None),
    ],
)
def test_share_class_tickers_are_parsed(text, expected):
    assert parse_ticker(text) == expected
    assert (re.match(TICKER_PATTERN, text) is not None) is (expected is not None)


def test_share_class_symbol_per_source():
    assert fmp_symbol("BRK.B") == "BRK-B"
    assert fmp_symbol("aapl") == "AAPL"


@respx.mock
async def test_sec_finds_cik_for_share_class():
    respx.get(re.compile(r".*company_tickers\.json")).mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 1067983, "ticker": "BRK-B"}})
    )
    client = SECEdgarClient(user_agent="t t@example.org", max_retries=1)
    assert await client.get_cik("BRK.B") == "0001067983"
    await client.aclose()


@respx.mock
async def test_sec_unexpected_submissions_format_is_a_source_error():
    respx.get(re.compile(r".*company_tickers\.json")).mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "X"}})
    )
    respx.get(re.compile(r".*/submissions/.*")).mock(return_value=httpx.Response(200, json=[]))
    client = SECEdgarClient(user_agent="t t@example.org", max_retries=1)
    with pytest.raises(SourceError) as caught:
        await client.get_recent_filings("X")
    await client.aclose()
    assert caught.value.kind == "invalid_response"


class _SlowPipeline:
    def __init__(self):
        self.calls = 0
        self.release = asyncio.Event()

    async def run(self, ticker):
        self.calls += 1
        await self.release.wait()
        raise TickerNotFoundError("нет", source="fmp", kind="not_found")


async def test_same_ticker_in_same_chat_runs_once():
    """C: повторное сообщение во время расчёта не запускает второй расчёт."""
    pipeline = _SlowPipeline()
    service = BotService(pipeline, session_factory=None)
    first = asyncio.create_task(service.analyze("aapl", chat_id=1))
    await asyncio.sleep(0)
    assert service.is_running("AAPL", 1)
    text, signal_id = await service.analyze("AAPL", chat_id=1)
    assert "уже выполняется" in text and signal_id is None
    # другой чат или другой тикер — независимые расчёты
    other_chat = asyncio.create_task(service.analyze("AAPL", chat_id=2))
    await asyncio.sleep(0)
    pipeline.release.set()
    await first
    await other_chat
    assert pipeline.calls == 2
    assert not service.is_running("AAPL", 1)  # после завершения можно запросить снова


async def test_render_failure_after_save_is_reported():
    class OkPipeline:
        async def run(self, ticker):
            return 42

    class BrokenSessionFactory:
        def __call__(self):
            raise RuntimeError("db down")

    service = BotService(OkPipeline(), BrokenSessionFactory())
    text, signal_id = await service.analyze("AAPL")
    assert signal_id is None
    assert "Расчёт сохранён (№42)" in text and "/history AAPL" in text


def test_split_message_respects_telegram_limit():
    short = "строка"
    assert split_message(short) == [short]
    lines = [f"• новость {i} " + "x" * 80 for i in range(100)]
    text = "\n".join(lines)
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text  # ничего не потеряно, разрыв — по строкам
    huge = "y" * 2500
    assert [len(p) for p in split_message(huge, limit=1000)] == [1000, 1000, 500]


class _FakeChat:
    id = 7


class _SentMessage:
    def __init__(self, log, text, reply_markup):
        self._log = log
        self.text = text
        self.reply_markup = reply_markup
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _IncomingMessage:
    def __init__(self, text):
        self.text = text
        self.chat = _FakeChat()
        self.from_user = None
        self.sent: list[_SentMessage] = []

    async def answer(self, text, reply_markup=None):
        sent = _SentMessage(self.sent, text, reply_markup)
        self.sent.append(sent)
        return sent


async def test_ticker_handler_shows_progress_and_splits_long_result(monkeypatch):
    from app.bot import handlers

    class LongService(BotService):
        async def _analyze(self, ticker):
            return ("\n".join(f"строка {i} " + "z" * 90 for i in range(100)), 5)

    monkeypatch.setattr(
        handlers, "get_settings", lambda: type("S", (), {"telegram_allowed_ids": []})()
    )
    router = handlers.build_router(LongService(None, None))
    on_ticker = next(
        h.callback for h in router.message.handlers if h.callback.__name__ == "on_ticker"
    )
    message = _IncomingMessage("brk.b")
    await on_ticker(message)

    progress, *result = message.sent
    assert progress.text == "⏳ Считаю BRK.B…" and progress.deleted
    assert len(result) >= 3 and all(len(m.text) <= 4096 for m in result)
    assert [m.reply_markup is not None for m in result] == [False] * (len(result) - 1) + [True]
    buttons = [b.callback_data for row in result[-1].reply_markup.inline_keyboard for b in row]
    assert "sec:details:5" in buttons and "hist:BRK.B" in buttons
