"""Репозиторий: запись/чтение сигналов и связанных данных (ТЗ разделы 5, 13).

signals — append-only: каждый расчёт создаёт новую строку с полным
raw_input_snapshot, что позволяет восстановить цепочку от входных данных до
Final Score (принцип воспроизводимости, ТЗ 7, 13).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.calculation import CalculationResult, replay_from_raw_inputs
from app.db.models import (
    FactorScoresRow,
    FormulaVersionRow,
    FundamentalsSnapshot,
    MarketContext,
    Signal,
    TelegramUser,
)
from app.db.models import (
    PriceHistory as PriceHistoryRow,
)
from app.db.models import (
    Ticker as TickerRow,
)
from app.features.catalysts import CatalystAnalysis
from app.features.indicators import FeatureSet
from app.ingestion.normalization import PriceHistory
from app.ingestion.schemas import CompanyProfile, Fundamentals
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


class StorageError(RuntimeError):
    """Расчёт не удалось сохранить: транзакция отменена, ничего не записано."""


class FormulaVersionConflictError(RuntimeError):
    """В БД уже есть эта версия формул, но с другими весами/порогами."""


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
    commit: bool = True,
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
    if commit:
        await session.commit()
        await session.refresh(signal)
    else:
        await session.flush()  # signal.id нужен связанным записям той же транзакции
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
            # до v1.4 пороги Risk Filter не сохранялись: они были равны значениям
            # по умолчанию, а не текущему config/thresholds.yaml
            "risk_filter": stored_formula.get("risk_filter") or {},
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


def _dialect_insert(session: AsyncSession, model):
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        return postgresql_insert(model)
    if dialect == "sqlite":
        return sqlite_insert(model)
    raise NotImplementedError(f"upsert не поддержан для диалекта {dialect}")


async def upsert_price_history(
    session: AsyncSession, history: PriceHistory, *, commit: bool = True
) -> int:
    """Записывает бары одним upsert по (ticker, date); возвращает число баров.

    Повторная загрузка той же даты обновляет значения, дублей не возникает
    (UNIQUE(ticker, date)).
    """
    rows = [
        {
            "ticker": bar.ticker,
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "source": bar.source,
            "fetched_at": bar.fetched_at,
        }
        for bar in history.bars
    ]
    # пакетами: лимит параметров запроса (SQLite 32 766, PostgreSQL 65 535)
    for start in range(0, len(rows), 1000):
        stmt = _dialect_insert(session, PriceHistoryRow).values(rows[start : start + 1000])
        stmt = stmt.on_conflict_do_update(
            index_elements=[PriceHistoryRow.ticker, PriceHistoryRow.date],
            set_={
                col: getattr(stmt.excluded, col)
                for col in ("open", "high", "low", "close", "volume", "source", "fetched_at")
            },
        )
        await session.execute(stmt)
    if commit:
        await session.commit()
    return len(rows)


async def upsert_ticker(
    session: AsyncSession, ticker: str, profile: CompanyProfile | None = None
) -> None:
    """Справочник тикеров: название и биржа из профиля компании (если есть)."""
    values = {"ticker": ticker.upper(), "is_active": True}
    update_values: dict = {"is_active": True}
    if profile is not None:
        values |= {"name": profile.company_name, "exchange": profile.exchange}
        update_values |= {"name": profile.company_name, "exchange": profile.exchange}
    stmt = _dialect_insert(session, TickerRow).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=[TickerRow.ticker], set_=update_values)
    await session.execute(stmt)


async def save_calculation_context(
    session: AsyncSession,
    *,
    signal: Signal,
    calc_date: date,
    fundamentals: Fundamentals,
    features: FeatureSet,
    risk: RiskAssessment,
    next_earnings_date: date | None,
    news_sentiment: float | None,
    profile: CompanyProfile | None,
    price_histories: list[PriceHistory],
    commit: bool = True,
) -> None:
    """Раскладывает данные расчёта по профильным таблицам схемы (ТЗ раздел 5).

    Полный снимок для воспроизведения остаётся в signals.raw_input_snapshot;
    таблицы ниже дают обычный реляционный доступ к тем же данным:
    tickers, price_history (OHLCV тикера и SPY), fundamentals_snapshot и
    market_context — со ссылкой на signal_id.
    """
    await upsert_ticker(session, signal.ticker, profile)
    for history in price_histories:
        await upsert_price_history(session, history, commit=False)
    session.add(
        FundamentalsSnapshot(
            signal_id=signal.id,
            ticker=signal.ticker,
            as_of_date=calc_date,
            statement_date=fundamentals.statement_date,
            fiscal_year=fundamentals.fiscal_year,
            growth_date=fundamentals.growth_date,
            growth_fiscal_year=fundamentals.growth_fiscal_year,
            revenue_growth=fundamentals.revenue_growth,
            eps_growth=fundamentals.eps_growth,
            eps_growth_basis=fundamentals.eps_growth_basis,
            eps=fundamentals.eps,
            eps_previous=fundamentals.eps_previous,
            eps_basis=fundamentals.eps_basis,
            eps_turnaround=fundamentals.eps_turnaround,
            gross_margin=fundamentals.gross_margin,
            debt_equity=fundamentals.debt_equity,
            pe=fundamentals.pe,
            pe_ttm=fundamentals.pe_ttm,
            pe_ttm_as_of=fundamentals.pe_ttm_as_of,
            forward_pe=fundamentals.forward_pe,
            source=fundamentals.source,
        )
    )
    session.add(
        MarketContext(
            signal_id=signal.id,
            ticker=signal.ticker,
            as_of_date=calc_date,
            next_earnings_date=next_earnings_date,
            gap_pct=features.gap_pct,
            avg_volume_20d=features.avg_volume_20d,
            distance_to_ema_pct=features.distance_to_ema20,
            news_sentiment=news_sentiment,
            negative_news_flag="news_risk" in risk.flag_names(),
        )
    )
    if commit:
        await session.commit()


async def touch_telegram_user(session: AsyncSession, chat_id: int, username: str | None) -> None:
    """Учёт пользователей бота: первый и последний визит."""
    now = _utcnow()
    stmt = _dialect_insert(session, TelegramUser).values(
        chat_id=chat_id, username=username, first_seen=now, last_seen=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[TelegramUser.chat_id],
        set_={"username": username, "last_seen": now},
    )
    await session.execute(stmt)
    await session.commit()


def _formula_row_values(version: FormulaVersion) -> dict:
    return {
        "version": version.version,
        "weights": version.final_score_weights,
        "thresholds": {
            **version.factor_params,
            "data_quality": version.data_quality,
            "risk_filter": version.risk_filter,
        },
        "is_active": True,
    }


def _as_json(value) -> object:
    """Нормализация для сравнения с jsonb (кортежи → списки)."""
    return json.loads(json.dumps(value, sort_keys=True))


async def check_formula_version(session: AsyncSession, version: FormulaVersion) -> None:
    """Номер версии формул однозначно задаёт веса и пороги (ТЗ 7.2, 8).

    Если в formula_versions уже есть эта версия с другими параметрами, значит
    config/thresholds.yaml изменили без смены номера версии: одна «версия»
    означала бы разные формулы. Бросает FormulaVersionConflictError.
    """
    row = await session.get(FormulaVersionRow, version.version)
    if row is None:
        return
    values = _formula_row_values(version)
    changed = [
        key
        for key in ("weights", "thresholds")
        if _as_json(getattr(row, key)) != _as_json(values[key])
    ]
    if changed:
        raise FormulaVersionConflictError(
            f"версия формул {version.version} уже сохранена в БД с другими "
            f"параметрами ({', '.join(changed)}): изменения в config/thresholds.yaml "
            "требуют нового номера version"
        )


async def ensure_formula_version_consistent(
    session_factory, config: ScoringConfig | None = None
) -> None:
    """Проверка при старте сервисов: активная версия формул не конфликтует с БД."""
    async with session_factory() as session:
        await check_formula_version(session, active_formula_version(config))


async def upsert_active_formula_version(
    session: AsyncSession, version: FormulaVersion, *, commit: bool = True
) -> None:
    """Делает версию активной (ровно одна is_active), сохраняя веса/пороги.

    Версию с тем же номером, но другими параметрами не перезаписывает
    (FormulaVersionConflictError) — журнал версий остаётся достоверным.
    """
    await check_formula_version(session, version)
    await session.execute(update(FormulaVersionRow).values(is_active=False))
    values = _formula_row_values(version)
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
    if commit:
        await session.commit()
