"""Типизированная конфигурация Score Engine из config/thresholds.yaml (ТЗ 8).

Веса Final Score и параметры нормализации факторов версионируются отдельно от
кода (принцип КП с. 7, 9; полноценный реестр версий — formula_versions, 1.5).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"


class MomentumConfig(BaseModel):
    weight_rsi: float = 0.30
    weight_macd: float = 0.30
    weight_ema_structure: float = 0.40
    rsi_sweet_spot: tuple[float, float] = (60.0, 70.0)
    rsi_overbought: float = 80.0
    macd_hist_multiplier: float = 50.0


class GrowthConfig(BaseModel):
    revenue_growth_multiplier: float = 2.0
    eps_growth_multiplier: float = 1.5


class FundamentalsConfig(BaseModel):
    eps_positive_score: float = 70.0
    eps_negative_score: float = 30.0
    debt_equity_penalty: float = 50.0


class RelativeStrengthConfig(BaseModel):
    rel_strength_multiplier: float = 5.0


class VolumeConfig(BaseModel):
    ratio_multiplier: float = 50.0


class ValuationConfig(BaseModel):
    pe_fair_low: float = 10.0
    pe_fair_high: float = 40.0
    pe_negative_score: float = 25.0


class CatalystsConfig(BaseModel):
    sentiment_multiplier: float = 50.0
    # Вклад источников в сигнал катализаторов: SEC (официальный) приоритетнее СМИ.
    news_weight: float = 1.0
    # Окно актуальности новостей: старше — не учитываются; вес = 1 − возраст/окно.
    news_lookback_days: int = 14
    sec_weight: float = 2.0
    # Окно актуальности события 8-K / NT: вес линейно убывает от 1 до 0.
    sec_event_lookback_days: int = 30
    # 10-K/10-Q считается свежей подтверждённой отчётностью в этом окне.
    sec_report_lookback_days: int = 100
    positive_8k_items: list[str] = ["1.01", "2.01"]
    # Асимметрия: негативные пункты 8-K однозначны, позитивные — нет
    # (1.01 бывает кредитным договором, 2.01 — продажей части бизнеса).
    positive_8k_score: float = 0.5
    negative_8k_score: float = -1.0
    negative_8k_items: list[str] = [
        "1.02", "1.03", "2.04", "2.05", "2.06", "3.01", "4.01", "4.02"
    ]
    negative_forms: list[str] = ["NT 10-K", "NT 10-Q"]


class FactorScoresConfig(BaseModel):
    momentum: MomentumConfig = MomentumConfig()
    growth: GrowthConfig = GrowthConfig()
    fundamentals: FundamentalsConfig = FundamentalsConfig()
    relative_strength: RelativeStrengthConfig = RelativeStrengthConfig()
    volume: VolumeConfig = VolumeConfig()
    valuation: ValuationConfig = ValuationConfig()
    catalysts: CatalystsConfig = CatalystsConfig()


class FinalScoreWeights(BaseModel):
    momentum: float = 0.20
    growth: float = 0.15
    fundamentals: float = 0.15
    relative_strength: float = 0.15
    volume: float = 0.10
    valuation: float = 0.10
    catalysts: float = 0.15

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> FinalScoreWeights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"сумма весов Final Score должна быть 1.0, получено {total}")
        return self

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class DataQualityConfig(BaseModel):
    """Два уровня неполноты данных (ТЗ раздел 8: «при отсутствии критичных данных
    система снижает confidence либо не формирует сигнал»)."""

    # История короче — EMA200 не рассчитать: Final Score не выдаётся.
    min_history_bars_critical: int = 200
    # История короче — достоверность пониженная.
    min_history_bars_full: int = 250
    # Не хватает стольких (и более) ключевых fundamentals — Final Score не выдаётся;
    # меньше (но не ноль) — достоверность пониженная.
    fundamentals_missing_critical: int = 3


class ScoringConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str = "v1.3"
    final_score_weights: FinalScoreWeights = FinalScoreWeights()
    factor_scores: FactorScoresConfig = FactorScoresConfig()
    # None — версии формул до v1.2 (без уровней неполноты), для воспроизведения.
    data_quality: DataQualityConfig | None = DataQualityConfig()


def load_scoring_config(path: str | Path | None = None) -> ScoringConfig:
    """Читает config/thresholds.yaml в типизированную ScoringConfig."""
    resolved = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    with open(resolved, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ScoringConfig.model_validate(data)
