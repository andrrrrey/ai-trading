"""Final Score — взвешенная сумма 7 факторов (ТЗ разделы 7–8).

Final Score = 0.20·Momentum + 0.15·Growth + 0.15·Fundamentals + 0.15·RelStrength
            + 0.10·Volume + 0.10·Valuation + 0.15·Catalysts

Округление: Final Score и каждый Factor Score округляются до целого числа ОДИН
раз — на выходе Score Engine (ТЗ 8, пункт «Округление»). Используется округление
«половина вверх» (не банковское — прямое требование ТЗ). Все интерфейсы (Telegram,
БД, AI) работают уже с округлёнными значениями.

Если хотя бы один фактор недоступен, Final Score не рассчитывается. Это сохраняет
утверждённые веса неизменными и не выдаёт неполный набор за полный расчёт.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from app.scoring.factor_scores import FACTOR_NAMES, FactorScores
from app.scoring.thresholds import ScoringConfig, load_scoring_config


def round_half_up(value: float) -> int:
    """Округление «половина вверх» для неотрицательных Score (ТЗ 8: не банковское)."""
    return int(math.floor(value + 0.5))


class FinalScore(BaseModel):
    """Итог Score Engine: округлённые факторные Score, Final Score и версия формул."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    final_score: int | None
    factor_scores: dict[str, int | None]
    weights_used: dict[str, float]
    formula_version: str
    is_incomplete: bool
    rule: str


def compute_final_score(
    factor_scores: FactorScores, config: ScoringConfig | None = None
) -> FinalScore:
    """Считает Final Score по факторным Score и весам из конфигурации."""
    cfg = config or load_scoring_config()
    weights = cfg.final_score_weights.as_dict()
    raw_scores = factor_scores.as_dict()  # {name: float | None}

    missing = [name for name in FACTOR_NAMES if raw_scores[name] is None]
    if missing:
        final_value: int | None = None
        weights_used = weights
    else:
        weighted_sum = sum(raw_scores[name] * weights[name] for name in FACTOR_NAMES)
        final_value = max(0, min(100, round_half_up(weighted_sum)))
        weights_used = weights

    rounded_factors = {
        name: (None if raw_scores[name] is None else round_half_up(raw_scores[name]))
        for name in FACTOR_NAMES
    }
    rule = (
        "взвешенная сумма факторов "
        + " + ".join(f"{weights[n]:.2f}·{n}" for n in FACTOR_NAMES)
        + (f"; Final Score не рассчитан (нет: {', '.join(missing)})" if missing else "")
    )

    return FinalScore(
        ticker=factor_scores.ticker,
        final_score=final_value,
        factor_scores=rounded_factors,
        weights_used=weights_used,
        formula_version=cfg.version,
        is_incomplete=bool(missing),
        rule=rule,
    )
