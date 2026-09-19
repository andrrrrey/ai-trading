"""Тесты Final Score и версий формул (ТЗ разделы 7–8, 13; DoD подэтапа 1.5)."""
from __future__ import annotations

import pytest

from app.scoring import (
    active_formula_version,
    compute_final_score,
    load_scoring_config,
    round_half_up,
)
from app.scoring.factor_scores import FACTOR_NAMES, FactorScore, FactorScores

CFG = load_scoring_config()


def fs_of(**scores: float | None) -> FactorScores:
    """Строит FactorScores с заданными баллами (отсутствующие = None)."""
    def fac(name: str, value: float | None) -> FactorScore:
        return FactorScore(
            name=name, score=value, inputs={}, rule="test", is_incomplete=value is None
        )

    return FactorScores(
        ticker="AAPL", **{n: fac(n, scores.get(n)) for n in FACTOR_NAMES}
    )


ALL_SIXTY = dict.fromkeys(FACTOR_NAMES, 60.0)


# --------------------------------------------------------------------------- #
# round_half_up — не банковское округление (ТЗ 8)
# --------------------------------------------------------------------------- #
def test_round_half_up_not_bankers():
    assert round_half_up(2.5) == 3       # банковское дало бы 2
    assert round_half_up(74.5) == 75
    assert round_half_up(0.5) == 1
    assert round_half_up(99.4) == 99
    assert round(2.5) == 2               # контраст: встроенный round — банковский


# --------------------------------------------------------------------------- #
# Взвешенная сумма (ручная сверка)
# --------------------------------------------------------------------------- #
def test_uniform_scores_equal_final():
    result = compute_final_score(fs_of(**ALL_SIXTY))
    assert result.final_score == 60
    assert result.is_incomplete is False


def test_weighted_sum_hand_computed():
    # 0.2·80+0.15·70+0.15·60+0.15·90+0.10·50+0.10·40+0.15·100 = 73.0
    result = compute_final_score(
        fs_of(momentum=80, growth=70, fundamentals=60, relative_strength=90,
              volume=50, valuation=40, catalysts=100)
    )
    assert result.final_score == 73


def test_final_score_range_clamped():
    assert compute_final_score(fs_of(**dict.fromkeys(FACTOR_NAMES, 100.0))).final_score == 100
    assert compute_final_score(fs_of(**dict.fromkeys(FACTOR_NAMES, 0.0))).final_score == 0


def test_single_rounding_half_up():
    # взвешенная сумма = 74.5 → 75 (одно округление половина-вверх)
    result = compute_final_score(fs_of(**dict.fromkeys(FACTOR_NAMES, 74.5)))
    assert result.final_score == 75
    assert result.factor_scores["momentum"] == 75  # факторы тоже округлены


# --------------------------------------------------------------------------- #
# Неполнота: ренормировка весов без выдуманных значений
# --------------------------------------------------------------------------- #
def test_missing_factor_renormalizes_weights():
    scores = dict(ALL_SIXTY)
    scores["catalysts"] = None
    result = compute_final_score(fs_of(**scores))
    assert result.final_score == 60           # 60 при ренормировке остаётся 60
    assert result.is_incomplete is True
    assert result.factor_scores["catalysts"] is None
    assert result.weights_used["catalysts"] == 0.0
    assert sum(result.weights_used.values()) == pytest.approx(1.0, abs=0.01)


def test_all_missing_gives_none():
    result = compute_final_score(fs_of())
    assert result.final_score is None
    assert result.is_incomplete is True


# --------------------------------------------------------------------------- #
# Воспроизводимость, чувствительность, версия формул
# --------------------------------------------------------------------------- #
def test_deterministic():
    a = compute_final_score(fs_of(**ALL_SIXTY))
    b = compute_final_score(fs_of(**ALL_SIXTY))
    assert a == b


def test_sensitivity_to_factor_change():
    base = compute_final_score(fs_of(**dict.fromkeys(FACTOR_NAMES, 50.0)))
    scores = dict.fromkeys(FACTOR_NAMES, 50.0)
    scores["momentum"] = 100.0
    changed = compute_final_score(fs_of(**scores))
    assert changed.final_score > base.final_score


def test_formula_version_recorded():
    result = compute_final_score(fs_of(**ALL_SIXTY))
    assert result.formula_version == CFG.version


def test_active_formula_version_snapshot():
    fv = active_formula_version()
    assert fv.version == CFG.version
    assert fv.is_active is True
    assert fv.final_score_weights["momentum"] == 0.20
    snap = fv.snapshot()
    assert set(snap) == {"version", "final_score_weights", "factor_params"}
