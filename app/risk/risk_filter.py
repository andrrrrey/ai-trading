"""Risk Filter — базовый набор флагов риска (ТЗ раздел 9).

Этап 1 реализует 6 флагов: high_volatility, event_risk, gap_risk, overextension,
liquidity_risk, missing_data. Флаг news_risk зависит от новостного сентимента и
переносится на подэтап 2.1.

Каждый флаг возвращает причину и конкретное значение, вызвавшее срабатывание.
Выход — RiskAssessment {active_flags, risk_level, allowed_max_status} — передаётся
в Rule Engine (2.2) как отдельный, независимый от Final Score вход: «Final Score
показывает качество идеи, Risk Filter — можно ли её использовать сейчас» (КП с. 5, 8).

missing_data всегда даёт risk_level=high — неполный расчёт не может быть выдан за
надёжный сигнал.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from app.features.indicators import FeatureSet
from app.ingestion.schemas import Fundamentals
from app.scoring.thresholds import DEFAULT_THRESHOLDS_PATH
from app.status import TradeStatus, min_status

logger = logging.getLogger(__name__)


class RiskFilterConfig(BaseModel):
    high_volatility_atr_ratio: float = 0.05
    event_risk_days: int = 3
    gap_risk_pct: float = 5.0
    overextension_ema20_pct: float = 15.0
    liquidity_min_avg_volume: float = 500_000
    news_risk_sentiment: float = -0.3  # используется на подэтапе 2.1


def load_risk_config(path: str | Path | None = None) -> RiskFilterConfig:
    resolved = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    with open(resolved, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RiskFilterConfig.model_validate(data.get("risk_filter", {}))


class RiskFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    flag: str
    reason: str
    value: float | None = None


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    active_flags: list[RiskFlag]
    risk_level: str  # low | medium | high
    allowed_max_status: TradeStatus

    def flag_names(self) -> list[str]:
        return [f.flag for f in self.active_flags]


# Потолок допустимого статуса для каждого флага (ТЗ раздел 9).
_FLAG_CEILINGS: dict[str, TradeStatus] = {
    "high_volatility": TradeStatus.WATCH,      # допускает только WATCH/SELL
    "event_risk": TradeStatus.BUY_ON_DIP,      # до BUY ON DIP/WATCH
    "gap_risk": TradeStatus.BUY_ON_DIP,
    "overextension": TradeStatus.BUY_ON_DIP,   # BUY → BUY ON DIP
    "liquidity_risk": TradeStatus.WATCH,       # блокирует BUY (Rule Engine → WATCH)
    "missing_data": TradeStatus.WATCH,         # максимум WATCH, никогда BUY/BUY ON DIP
}


def _business_days_until(target: date, today: date) -> int | None:
    """Число рабочих дней (Пн–Пт) от today до target включительно; None если в прошлом."""
    if target < today:
        return None
    days = 0
    cursor = today
    while cursor < target:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def compute_risk(
    features: FeatureSet,
    *,
    next_earnings_date: date | None = None,
    fundamentals: Fundamentals | None = None,
    today: date | None = None,
    config: RiskFilterConfig | None = None,
) -> RiskAssessment:
    """Считает RiskAssessment по фичам, дате отчётности и полноте данных."""
    cfg = config or load_risk_config()
    today = today or date.today()
    flags: list[RiskFlag] = []

    # high_volatility: ATR/close > порога
    if features.atr_pct is not None and features.atr_pct > cfg.high_volatility_atr_ratio:
        flags.append(
            RiskFlag(
                flag="high_volatility",
                reason=(
                    f"ATR/close = {features.atr_pct * 100:.1f}%, "
                    f"порог {cfg.high_volatility_atr_ratio * 100:.0f}%"
                ),
                value=features.atr_pct,
            )
        )

    # gap_risk: |Gap%| > порога
    if features.gap_pct is not None and abs(features.gap_pct) > cfg.gap_risk_pct:
        flags.append(
            RiskFlag(
                flag="gap_risk",
                reason=f"Gap {features.gap_pct:.1f}%, порог ±{cfg.gap_risk_pct:.0f}%",
                value=features.gap_pct,
            )
        )

    # overextension: Distance to EMA20 > порога
    if (
        features.distance_to_ema20 is not None
        and features.distance_to_ema20 > cfg.overextension_ema20_pct
    ):
        flags.append(
            RiskFlag(
                flag="overextension",
                reason=(
                    f"Отклонение от EMA20 {features.distance_to_ema20:.1f}%, "
                    f"порог {cfg.overextension_ema20_pct:.0f}%"
                ),
                value=features.distance_to_ema20,
            )
        )

    # liquidity_risk: средний объём 20д < порога
    if (
        features.avg_volume_20d is not None
        and features.avg_volume_20d < cfg.liquidity_min_avg_volume
    ):
        flags.append(
            RiskFlag(
                flag="liquidity_risk",
                reason=(
                    f"Средний объём 20д {features.avg_volume_20d:,.0f} < "
                    f"{cfg.liquidity_min_avg_volume:,.0f}"
                ),
                value=features.avg_volume_20d,
            )
        )

    # event_risk: отчётность в ближайшие N рабочих дней
    if next_earnings_date is not None:
        bd = _business_days_until(next_earnings_date, today)
        if bd is not None and bd <= cfg.event_risk_days:
            flags.append(
                RiskFlag(
                    flag="event_risk",
                    reason=f"Отчётность через {bd} раб. дн. (порог {cfg.event_risk_days})",
                    value=float(bd),
                )
            )

    # missing_data: не хватает ключевых входных данных (цена/объём/fundamentals)
    missing_reasons = list(features.missing)
    if features.avg_volume_20d is None:
        missing_reasons.append("avg_volume_20d")
    if fundamentals is not None and fundamentals.is_incomplete:
        missing_reasons.append("fundamentals")
    if missing_reasons:
        flags.append(
            RiskFlag(
                flag="missing_data",
                reason="Нет ключевых данных: " + ", ".join(sorted(set(missing_reasons))),
                value=None,
            )
        )

    risk_level = _risk_level(flags)
    allowed = TradeStatus.BUY
    for f in flags:
        allowed = min_status(allowed, _FLAG_CEILINGS[f.flag])

    assessment = RiskAssessment(
        ticker=features.ticker,
        active_flags=flags,
        risk_level=risk_level,
        allowed_max_status=allowed,
    )
    logger.info(
        "risk ticker=%s level=%s allowed_max=%s flags=%s",
        features.ticker,
        risk_level,
        allowed.value,
        assessment.flag_names(),
    )
    return assessment


def _risk_level(flags: list[RiskFlag]) -> str:
    names = {f.flag for f in flags}
    if "missing_data" in names:
        return "high"  # неполный расчёт всегда high (ТЗ 9)
    if not flags:
        return "low"
    if "liquidity_risk" in names or len(flags) >= 3:
        return "high"
    return "medium"  # 1–2 флага
