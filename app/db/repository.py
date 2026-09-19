"""Репозиторий: запись/чтение сигналов и связанных данных (ТЗ разделы 5, 13).

signals — append-only: каждый расчёт создаёт новую строку с полным
raw_input_snapshot, что позволяет восстановить цепочку от входных данных до
Final Score (принцип воспроизводимости, ТЗ 7, 13).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    FactorScoresRow,
    FormulaVersionRow,
    Signal,
)
from app.db.models import (
    PriceHistory as PriceHistoryRow,
)
from app.features.indicators import FeatureSet
from app.ingestion.normalization import PriceHistory
from app.ingestion.schemas import Fundamentals
from app.risk.risk_filter import RiskAssessment
from app.scoring import (
    FinalScore,
    ScoringConfig,
    compute_factor_scores,
    compute_final_score,
    load_scoring_config,
)
from app.scoring.formula_versions import FormulaVersion
from app.status import TradeStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def save_signal(
    session: AsyncSession,
    *,
    features: FeatureSet,
    fundamentals: Fundamentals,
    final: FinalScore,
    risk: RiskAssessment,
    status: TradeStatus,
    price: float | None = None,
    ai_explanation: dict | None = None,
    news_sentiment: float | None = None,
    timestamp: datetime | None = None,
) -> Signal:
    """Сохраняет один сигнал (append-only) с полным снимком входных данных."""
    snapshot = {
        "features": features.model_dump(mode="json"),
        "fundamentals": fundamentals.model_dump(mode="json"),
        "news_sentiment": news_sentiment,
    }
    signal = Signal(
        ticker=features.ticker,
        timestamp=timestamp or _utcnow(),
        price=price if price is not None else features.price,
        final_score=final.final_score,
        status=str(status),
        risk_flags=[flag.model_dump() for flag in risk.active_flags],
        formula_version=final.formula_version,
        ai_explanation=ai_explanation,
        raw_input_snapshot=snapshot,
        factor_scores=FactorScoresRow(**final.factor_scores),
    )
    session.add(signal)
    await session.commit()
    await session.refresh(signal)
    return signal


async def list_signals(
    session: AsyncSession, ticker: str, *, limit: int = 10
) -> list[Signal]:
    """Последние сигналы по тикеру (для /history и сверки), новые сверху."""
    stmt = (
        select(Signal)
        .where(Signal.ticker == ticker.upper())
        .order_by(Signal.timestamp.desc(), Signal.id.desc())
        .limit(limit)
        .options(selectinload(Signal.factor_scores))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_signal(session: AsyncSession, signal_id: int) -> Signal | None:
    stmt = (
        select(Signal)
        .where(Signal.id == signal_id)
        .options(selectinload(Signal.factor_scores))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def recompute_from_snapshot(
    signal: Signal, config: ScoringConfig | None = None
) -> FinalScore:
    """Восстанавливает Final Score из raw_input_snapshot записи истории.

    Даёт воспроизводимость: пересчёт по сохранённым входным данным и той же
    версии формул должен дать идентичный final_score (ТЗ 7).
    """
    cfg = config or load_scoring_config()
    snap = signal.raw_input_snapshot
    features = FeatureSet.model_validate(snap["features"])
    fundamentals = Fundamentals.model_validate(snap["fundamentals"])
    factor_scores = compute_factor_scores(
        features, fundamentals, cfg, news_sentiment=snap.get("news_sentiment")
    )
    return compute_final_score(factor_scores, cfg)


async def upsert_price_history(session: AsyncSession, history: PriceHistory) -> int:
    """Записывает бары с дедупликацией по (ticker, date); возвращает число баров."""
    for bar in history.bars:
        existing = await session.execute(
            select(PriceHistoryRow).where(
                PriceHistoryRow.ticker == bar.ticker,
                PriceHistoryRow.date == bar.date,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(
                PriceHistoryRow(
                    ticker=bar.ticker, date=bar.date, open=bar.open, high=bar.high,
                    low=bar.low, close=bar.close, volume=bar.volume,
                    source=bar.source, fetched_at=bar.fetched_at,
                )
            )
        else:
            row.open, row.high, row.low = bar.open, bar.high, bar.low
            row.close, row.volume = bar.close, bar.volume
            row.source, row.fetched_at = bar.source, bar.fetched_at
    await session.commit()
    return len(history.bars)


async def upsert_active_formula_version(
    session: AsyncSession, version: FormulaVersion
) -> None:
    """Делает версию активной (ровно одна is_active), сохраняя веса/пороги."""
    await session.execute(update(FormulaVersionRow).values(is_active=False))
    existing = await session.get(FormulaVersionRow, version.version)
    if existing is None:
        session.add(
            FormulaVersionRow(
                version=version.version,
                weights=version.final_score_weights,
                thresholds=version.factor_params,
                is_active=True,
            )
        )
    else:
        existing.weights = version.final_score_weights
        existing.thresholds = version.factor_params
        existing.is_active = True
    await session.commit()
