"""Тесты слоя БД и истории (ТЗ разделы 5, 13; DoD подэтапа 1.7).

Используется in-memory SQLite (aiosqlite) — модели авторитетны; в проде схема
разворачивается через Alembic (см. test_migrations.py).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import PriceHistory as PriceHistoryRow
from app.db.models import Signal
from app.db.repository import (
    list_signals,
    recompute_from_snapshot,
    save_signal,
    upsert_active_formula_version,
    upsert_price_history,
)
from app.db.session import create_all
from app.features.indicators import FeatureSet
from app.ingestion.normalization import PriceBar, PriceHistory
from app.ingestion.schemas import Fundamentals
from app.risk import compute_risk
from app.scoring import (
    FormulaVersion,
    active_formula_version,
    compute_factor_scores,
    compute_final_score,
)
from app.status import TradeStatus

FETCHED = datetime(2025, 9, 18, tzinfo=UTC)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await create_all(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _features() -> FeatureSet:
    return FeatureSet(
        ticker="AAPL", price=178.3, ema20=170, ema50=160, ema200=140, rsi14=66,
        macd_histogram=0.9, atr14=2.0, atr_pct=0.011, volume_ratio20=1.4,
        avg_volume_20d=50_000_000, relative_strength_63d=7.0,
        distance_to_ema20=4.9, gap_pct=0.5,
    )


def _fundamentals() -> Fundamentals:
    return Fundamentals(
        ticker="AAPL", source="fmp", fetched_at=FETCHED, period="annual",
        revenue_growth=0.12, eps_growth=0.18, eps=6.1, gross_margin=0.44,
        debt_equity=1.2, pe=30.0, forward_pe=26.0,
    )


async def _make_and_save(session, **overrides) -> Signal:
    features = _features()
    fundamentals = _fundamentals()
    fs = compute_factor_scores(features, fundamentals, news_sentiment=0.3)
    final = compute_final_score(fs)
    risk = compute_risk(features)
    return await save_signal(
        session,
        features=features,
        fundamentals=fundamentals,
        final=final,
        risk=risk,
        status=TradeStatus.BUY,
        news_sentiment=0.3,
        **overrides,
    )


# --------------------------------------------------------------------------- #
# append-only: два расчёта одного тикера → две записи
# --------------------------------------------------------------------------- #
async def test_two_calculations_create_two_rows(session):
    await _make_and_save(session)
    await _make_and_save(session)

    count = await session.scalar(
        select(func.count()).select_from(Signal).where(Signal.ticker == "AAPL")
    )
    assert count == 2

    history = await list_signals(session, "AAPL")
    assert len(history) == 2
    assert all(s.final_score is not None for s in history)
    # у каждой записи есть свои 7 факторных Score
    assert history[0].factor_scores is not None
    assert history[0].factor_scores.momentum is not None


# --------------------------------------------------------------------------- #
# восстановление цепочки вход → Final Score (воспроизводимость)
# --------------------------------------------------------------------------- #
async def test_chain_reconstruction_from_snapshot(session):
    signal = await _make_and_save(session)

    # пересчёт по сохранённому raw_input_snapshot даёт идентичный Final Score
    reconstructed = recompute_from_snapshot(signal)
    assert reconstructed.final_score == signal.final_score
    assert reconstructed.ticker == signal.ticker

    # факторные Score, сохранённые в БД, совпадают с пересчитанными
    stored = (await list_signals(session, "AAPL"))[0]
    assert stored.factor_scores.momentum == reconstructed.factor_scores["momentum"]
    assert stored.factor_scores.valuation == reconstructed.factor_scores["valuation"]


async def test_snapshot_contains_inputs(session):
    signal = await _make_and_save(session)
    snap = signal.raw_input_snapshot
    assert snap["features"]["rsi14"] == 66
    assert snap["fundamentals"]["eps"] == 6.1
    assert signal.risk_flags == []          # здоровый тикер — нет флагов
    assert signal.formula_version == "v1.0"


# --------------------------------------------------------------------------- #
# price_history: дедупликация по (ticker, date)
# --------------------------------------------------------------------------- #
async def test_price_history_upsert_dedup(session):
    def hist(close: float) -> PriceHistory:
        bar = PriceBar(
            ticker="AAPL", date=date(2025, 9, 17), open=10, high=12, low=9,
            close=close, volume=1000, source="fmp", fetched_at=FETCHED,
        )
        return PriceHistory(ticker="AAPL", bars=[bar], source="fmp", fetched_at=FETCHED)

    await upsert_price_history(session, hist(11.0))
    await upsert_price_history(session, hist(11.5))  # тот же (ticker, date)

    rows = (await session.execute(select(PriceHistoryRow))).scalars().all()
    assert len(rows) == 1                    # без дублей
    assert rows[0].close == 11.5             # upsert обновил значение


# --------------------------------------------------------------------------- #
# formula_versions: ровно одна активная
# --------------------------------------------------------------------------- #
async def test_formula_version_single_active(session):
    await upsert_active_formula_version(session, active_formula_version())
    other = FormulaVersion(
        version="v1.1", final_score_weights={"momentum": 1.0}, factor_params={}
    )
    await upsert_active_formula_version(session, other)

    from app.db.models import FormulaVersionRow

    active = (
        await session.execute(
            select(FormulaVersionRow).where(FormulaVersionRow.is_active.is_(True))
        )
    ).scalars().all()
    assert len(active) == 1
    assert active[0].version == "v1.1"
