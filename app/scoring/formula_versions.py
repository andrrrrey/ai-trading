"""Реестр версий формул/весов (ТЗ разделы 8, 13).

Каждый расчёт фиксирует, какая версия формул и весов использована
(``signals.formula_version``), чтобы история оставалась сравнимой при изменении
порогов. Источник активной версии на подэтапе 1.5 — config/thresholds.yaml;
таблица formula_versions в БД (append при изменении порогов, ровно одна активная)
добавляется на подэтапе 1.7.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.scoring.thresholds import ScoringConfig, load_scoring_config


class FormulaVersion(BaseModel):
    """Снимок весов и параметров формул для одной версии."""

    model_config = ConfigDict(frozen=True)

    version: str
    final_score_weights: dict[str, float]
    factor_params: dict
    is_active: bool = True

    def snapshot(self) -> dict:
        """Полный снимок для сохранения в jsonb (formula_versions / signals)."""
        return {
            "version": self.version,
            "final_score_weights": self.final_score_weights,
            "factor_params": self.factor_params,
        }


def active_formula_version(config: ScoringConfig | None = None) -> FormulaVersion:
    """Активная версия формул, собранная из текущей конфигурации Score Engine."""
    cfg = config or load_scoring_config()
    return FormulaVersion(
        version=cfg.version,
        final_score_weights=cfg.final_score_weights.as_dict(),
        factor_params=cfg.factor_scores.model_dump(),
        is_active=True,
    )
