"""Тесты Score Engine — 7 факторных Score (ТЗ раздел 8, DoD подэтапа 1.4)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.features.indicators import FeatureSet
from app.ingestion.schemas import Fundamentals
from app.scoring import compute_factor_scores, load_scoring_config
from app.scoring.factor_scores import FACTOR_NAMES

FETCHED = datetime(2025, 9, 18, tzinfo=UTC)
APPROX = {"abs": 0.01}
CFG = load_scoring_config()


def features(**kw) -> FeatureSet:
    return FeatureSet(ticker="AAPL", **kw)


def fundamentals(**kw) -> Fundamentals:
    return Fundamentals(ticker="AAPL", source="fmp", fetched_at=FETCHED, **kw)


# --------------------------------------------------------------------------- #
# Конфигурация весов Final Score (DoD 1.4: веса 20/15/15/15/10/10/15)
# --------------------------------------------------------------------------- #
def test_final_score_weights_match_kp():
    w = CFG.final_score_weights.as_dict()
    assert w == {
        "momentum": 0.20, "growth": 0.15, "fundamentals": 0.15,
        "relative_strength": 0.15, "volume": 0.10, "valuation": 0.10,
        "catalysts": 0.15,
    }
    assert sum(w.values()) == pytest.approx(1.0, **APPROX)


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #
def test_momentum_perfect_bull_structure():
    fs = compute_factor_scores(
        features(price=100, ema20=95, ema50=90, ema200=85, rsi14=65,
                 macd_histogram=1.0),
        fundamentals(),
    )
    assert fs.momentum.score == pytest.approx(100.0, **APPROX)


def test_momentum_rsi_overbought_penalty():
    # только RSI-компонента доступна: rsi=85 → 100-(85-70)*5 = 25
    fs = compute_factor_scores(features(rsi14=85.0), fundamentals())
    assert fs.momentum.score == pytest.approx(25.0, **APPROX)


# --------------------------------------------------------------------------- #
# Growth / Fundamentals / RelStrength / Volume / Valuation / Catalysts
# --------------------------------------------------------------------------- #
def test_growth_weighted():
    # rev 10% → 50+10*2=70; eps 20% → 50+20*1.5=80; avg .5/.5 = 75
    fs = compute_factor_scores(
        features(), fundamentals(revenue_growth=0.10, eps_growth=0.20)
    )
    assert fs.growth.score == pytest.approx(75.0, **APPROX)


def test_fundamentals_components():
    # eps>0→70; gm 0.44→44; D/E 1.0 → 100-50=50; avg = 54.6667
    fs = compute_factor_scores(
        features(), fundamentals(eps=5.0, gross_margin=0.44, debt_equity=1.0)
    )
    assert fs.fundamentals.score == pytest.approx(54.6667, **APPROX)


def test_relative_strength_clamp():
    assert compute_factor_scores(
        features(relative_strength_63d=10.0), fundamentals()
    ).relative_strength.score == pytest.approx(100.0, **APPROX)
    assert compute_factor_scores(
        features(relative_strength_63d=-20.0), fundamentals()
    ).relative_strength.score == pytest.approx(0.0, **APPROX)


def test_volume_ratio_scale():
    assert compute_factor_scores(
        features(volume_ratio20=1.0), fundamentals()
    ).volume.score == pytest.approx(50.0, **APPROX)
    assert compute_factor_scores(
        features(volume_ratio20=2.0), fundamentals()
    ).volume.score == pytest.approx(100.0, **APPROX)


def test_valuation_inverse_scale():
    assert compute_factor_scores(
        features(), fundamentals(pe=10.0)
    ).valuation.score == pytest.approx(100.0, **APPROX)
    assert compute_factor_scores(
        features(), fundamentals(pe=25.0)
    ).valuation.score == pytest.approx(50.0, **APPROX)
    assert compute_factor_scores(
        features(), fundamentals(pe=40.0)
    ).valuation.score == pytest.approx(0.0, **APPROX)


def test_valuation_negative_pe():
    fs = compute_factor_scores(features(), fundamentals(pe=-5.0))
    expected = CFG.factor_scores.valuation.pe_negative_score
    assert fs.valuation.score == pytest.approx(expected, **APPROX)


def test_valuation_prefers_forward_pe():
    fs = compute_factor_scores(features(), fundamentals(pe=40.0, forward_pe=10.0))
    assert fs.valuation.score == pytest.approx(100.0, **APPROX)


def test_catalysts_from_sentiment():
    fs = compute_factor_scores(features(), fundamentals(), news_sentiment=0.5)
    assert fs.catalysts.score == pytest.approx(75.0, **APPROX)  # 50 + 0.5*50


def test_catalysts_incomplete_without_sentiment():
    fs = compute_factor_scores(features(), fundamentals())
    assert fs.catalysts.score is None
    assert fs.catalysts.is_incomplete is True


# --------------------------------------------------------------------------- #
# Оркестрация / трассировка (DoD 1.4: метрики → правило → оценка)
# --------------------------------------------------------------------------- #
def test_all_seven_factors_present_with_trace():
    fs = compute_factor_scores(
        features(price=100, ema20=95, ema50=90, ema200=85, rsi14=65,
                 macd_histogram=0.5, volume_ratio20=1.5, relative_strength_63d=4.0),
        fundamentals(revenue_growth=0.1, eps_growth=0.15, eps=4.0,
                     gross_margin=0.5, debt_equity=0.8, pe=22.0),
        news_sentiment=0.2,
    )
    scores = fs.as_dict()
    assert set(scores) == set(FACTOR_NAMES)
    for name in FACTOR_NAMES:
        factor = getattr(fs, name)
        assert factor.score is not None
        assert 0.0 <= factor.score <= 100.0
        assert factor.inputs           # входные метрики показаны
        assert factor.rule             # правило расчёта показано


def test_missing_metric_makes_factor_incomplete():
    # нет relative_strength → фактор неполный, score None, но не падает
    fs = compute_factor_scores(features(rsi14=60.0), fundamentals())
    assert fs.relative_strength.score is None
    assert fs.relative_strength.is_incomplete is True


def test_scores_are_deterministic():
    args = (
        features(price=100, ema20=95, ema50=90, ema200=85, rsi14=65,
                 macd_histogram=0.5, volume_ratio20=1.5, relative_strength_63d=4.0),
        fundamentals(revenue_growth=0.1, eps_growth=0.15, eps=4.0,
                     gross_margin=0.5, debt_equity=0.8, pe=22.0),
    )
    first = compute_factor_scores(*args, news_sentiment=0.2)
    second = compute_factor_scores(*args, news_sentiment=0.2)
    assert first == second
