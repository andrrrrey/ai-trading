"""Репозиторий: запись/чтение сигналов и связанных данных (ТЗ разделы 5, 13).

signals — append-only: каждый расчёт создаёт новую строку с полным
raw_input_snapshot, что позволяет восстановить цепочку от входных данных до
Final Score (принцип воспроизводимости, ТЗ 7, 13).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.calculation import CalculationResult, replay_from_raw_inputs
from app.db.models import (
    FactorScoresRow,
    FormulaVersionRow,
    Signal,
)
from app.db.models import (
    PriceHistory as PriceHistoryRow,
)
from app.features.catalysts import CatalystAnalysis
from app.features.indicators import FeatureSet
from app.ingestion.normalization import PriceHistory
from app.ingestion.schemas import Fundamentals
from app.risk.risk_filter import RiskAssessment
from app.scoring import (
    FactorScores,
    FinalScore,
    ScoringConfig,
    active_formula_version,
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
    catalyst_context: dict | None = None,
    formula_snapshot: dict | None = None,
    source_context: dict | None = None,
    raw_inputs: dict | None = None,
    data_quality: dict | None = None,
    timestamp: datetime | None = None,
) -> Signal:
    """Сохраняет один сигнал (append-only) с полным снимком входных данных."""
    snapshot = {
        "features": features.model_dump(mode="json"),
        "fundamentals": fundamentals.model_dump(mode="json"),
        "news_sentiment": news_sentiment,
        "catalysts": catalyst_context or {},
        "sources": source_context or {},
        "calculation": {
            "weights_used": final.weights_used,
            "formula_version": final.formula_version,
            "final_rule": final.rule,
            "formula_config": formula_snapshot or active_formula_version().snapshot(),
        },
        # Исходные данные расчёта (OHLCV тикера и SPY, fundamentals, earnings,
        # новости, filings, дата расчёта) — для воспроизведения всей цепочки.
        "raw_inputs": raw_inputs,
        # Достоверность (high / reduced / insufficient) и причины по уровням.
        "data_quality": data_quality,
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


async def list_signals(session: AsyncSession, ticker: str, *, limit: int = 10) -> list[Signal]:
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
    stmt = select(Signal).where(Signal.id == signal_id).options(selectinload(Signal.factor_scores))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def stored_scoring_config(signal: Signal) -> ScoringConfig:
    """Версия формул/весов, с которой был выполнен расчёт этой записи."""
    stored_formula = ((signal.raw_input_snapshot or {}).get("calculation") or {}).get(
        "formula_config"
    )
    if not stored_formula:
        return load_scoring_config()
    return ScoringConfig.model_validate(
        {
            "version": stored_formula["version"],
            "final_score_weights": stored_formula["final_score_weights"],
            "factor_scores": stored_formula["factor_params"],
            # версии до v1.2 не знали уровней неполноты — повтор без них
            "data_quality": stored_formula.get("data_quality"),
        }
    )


def replay_signal(signal: Signal, config: ScoringConfig | None = None) -> CalculationResult:
    """Полный повтор расчёта от сохранённых исходных данных до Final Score и Risk.

    В отличие от ``recompute_from_snapshot`` (метрики → Score), здесь заново
    считаются и сами метрики — из сохранённых OHLCV, fundamentals и новостей.
    """
    raw = (signal.raw_input_snapshot or {}).get("raw_inputs")
    if not raw:
        raise ValueError(f"signal {signal.id}: исходные данные не сохранены (старая запись)")
    return replay_from_raw_inputs(raw, config or stored_scoring_config(signal))


def recompute_from_snapshot(signal: Signal, config: ScoringConfig | None = None) -> FinalScore:
    """Восстанавливает Final Score из raw_input_snapshot записи истории.

    Даёт воспроизводимость: пересчёт по сохранённым входным данным и той же
    версии формул должен дать идентичный final_score (ТЗ 7).
    """
    snap = signal.raw_input_snapshot
    cfg = config or stored_scoring_config(signal)
    return compute_final_score(factor_scores_from_snapshot(snap, cfg), cfg)


def factor_scores_from_snapshot(snap: dict, cfg: ScoringConfig) -> FactorScores:
    """7 факторов по сохранённым метрикам, fundamentals и Catalyst-контексту."""
    features = FeatureSet.model_validate(snap["features"])
    fundamentals = Fundamentals.model_validate(snap["fundamentals"])
    catalyst_context = snap.get("catalysts") or {}
    if "signal" in catalyst_context:
        return compute_factor_scores(
            features,
            fundamentals,
            cfg,
            catalysts=CatalystAnalysis.model_validate(catalyst_context),
        )
    # записи до версии v1.1: Catalysts считался только по тональности новостей
    return compute_factor_scores(
        features, fundamentals, cfg, news_sentiment=snap.get("news_sentiment")
    )


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
                    ticker=bar.ticker,
                    date=bar.date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    source=bar.source,
                    fetched_at=bar.fetched_at,
                )
            )
        else:
            row.open, row.high, row.low = bar.open, bar.high, bar.low
            row.close, row.volume = bar.close, bar.volume
            row.source, row.fetched_at = bar.source, bar.fetched_at
    await session.commit()
    return len(history.bars)


async def upsert_active_formula_version(session: AsyncSession, version: FormulaVersion) -> None:
    """Делает версию активной (ровно одна is_active), сохраняя веса/пороги."""
    await session.execute(update(FormulaVersionRow).values(is_active=False))
    values = {
        "version": version.version,
        "weights": version.final_score_weights,
        "thresholds": (
            {**version.factor_params, "data_quality": version.data_quality}
            if version.data_quality is not None
            else version.factor_params
        ),
        "is_active": True,
    }
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        stmt = postgresql_insert(FormulaVersionRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[FormulaVersionRow.version],
            set_={
                "weights": stmt.excluded.weights,
                "thresholds": stmt.excluded.thresholds,
                "is_active": True,
            },
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(FormulaVersionRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[FormulaVersionRow.version],
            set_={
                "weights": stmt.excluded.weights,
                "thresholds": stmt.excluded.thresholds,
                "is_active": True,
            },
        )
    else:
        stmt = insert(FormulaVersionRow).values(**values)
    await session.execute(stmt)
    await session.commit()
