"""Типизированная конфигурация Score Engine из config/thresholds.yaml (ТЗ 8).

Веса Final Score и параметры нормализации факторов версионируются отдельно от
кода (принцип КП с. 7, 9; полноценный реестр версий — formula_versions, 1.5).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

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

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class ScoringConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str = "v1.0"
    final_score_weights: FinalScoreWeights = FinalScoreWeights()
    factor_scores: FactorScoresConfig = FactorScoresConfig()


def load_scoring_config(path: str | Path | None = None) -> ScoringConfig:
    """Читает config/thresholds.yaml в типизированную ScoringConfig."""
    resolved = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    with open(resolved, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ScoringConfig.model_validate(data)
