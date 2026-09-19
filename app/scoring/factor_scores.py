"""Score Engine — семь факторных Score (ТЗ раздел 8).

Каждый фактор нормализуется в шкалу 0–100 по конфигурируемой формуле v1
(параметры в config/thresholds.yaml). Формулы внутри факторов эвристические —
открытый вопрос для тюнинга после запуска (ТЗ 16).

Каждый FactorScore несёт входные метрики, текст правила расчёта и итог — чтобы по
любому сигналу была видна цепочка «метрики → правило → оценка» (DoD 1.4) и она
сохранялась в историю (raw_input_snapshot, подэтап 1.7). Метрика, для которой не
хватает входных данных, даёт score=None и is_incomplete=True — никаких выдуманных
значений (ТЗ 6.6).
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from app.features.indicators import FeatureSet
from app.ingestion.schemas import Fundamentals
from app.scoring.thresholds import (
    FactorScoresConfig,
    FundamentalsConfig,
    MomentumConfig,
    ScoringConfig,
    load_scoring_config,
)

logger = logging.getLogger(__name__)

FACTOR_NAMES = (
    "momentum",
    "growth",
    "fundamentals",
    "relative_strength",
    "volume",
    "valuation",
    "catalysts",
)


class FactorScore(BaseModel):
    """Оценка одного фактора с прозрачной трассировкой расчёта."""

    model_config = ConfigDict(frozen=True)

    name: str
    score: float | None
    inputs: dict[str, float | None]
    rule: str
    is_incomplete: bool = False


class FactorScores(BaseModel):
    """Набор из 7 факторных Score (вход Final Score, подэтап 1.5)."""

    model_config = ConfigDict(frozen=True)

    momentum: FactorScore
    growth: FactorScore
    fundamentals: FactorScore
    relative_strength: FactorScore
    volume: FactorScore
    valuation: FactorScore
    catalysts: FactorScore

    def as_dict(self) -> dict[str, float | None]:
        return {name: getattr(self, name).score for name in FACTOR_NAMES}


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _weighted(components: list[tuple[float | None, float]]) -> float | None:
    """Взвешенное среднее по доступным (не-None) компонентам с ренормировкой весов."""
    available = [(v, w) for v, w in components if v is not None and w > 0]
    total_weight = sum(w for _, w in available)
    if not available or total_weight == 0:
        return None
    return sum(v * w for v, w in available) / total_weight


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #
def _rsi_score(rsi: float, cfg: MomentumConfig) -> float:
    lo, hi = cfg.rsi_sweet_spot
    if lo <= rsi <= hi:
        return 100.0
    if rsi < lo:
        return _clamp(100.0 - (lo - rsi) * 2.0)
    if rsi >= cfg.rsi_overbought:
        return _clamp(100.0 - (rsi - hi) * 5.0)  # усиленный штраф за перекупленность
    return _clamp(100.0 - (rsi - hi) * 3.0)


def _macd_score(hist: float, price: float, cfg: MomentumConfig) -> float | None:
    if price <= 0:
        return None
    return _clamp(50.0 + (hist / price * 100.0) * cfg.macd_hist_multiplier)


def _ema_structure_score(
    close: float, ema20: float | None, ema50: float | None, ema200: float | None
) -> float | None:
    """Бычья структура тренда: close > EMA20 > EMA50 > EMA200 = 100 (ТЗ 8)."""
    chain: list[float] = [close]
    for ema in (ema20, ema50, ema200):
        if ema is None:
            break
        chain.append(ema)
    if len(chain) < 2:
        return None
    steps = len(chain) - 1
    holds = sum(1 for i in range(steps) if chain[i] > chain[i + 1])
    return holds / steps * 100.0


def _momentum(features: FeatureSet, cfg: MomentumConfig) -> FactorScore:
    inputs = {
        "rsi14": features.rsi14,
        "macd_histogram": features.macd_histogram,
        "ema20": features.ema20,
        "ema50": features.ema50,
        "ema200": features.ema200,
        "price": features.price,
    }
    rsi_c = _rsi_score(features.rsi14, cfg) if features.rsi14 is not None else None
    macd_c = (
        _macd_score(features.macd_histogram, features.price, cfg)
        if features.macd_histogram is not None and features.price is not None
        else None
    )
    ema_c = (
        _ema_structure_score(
            features.price, features.ema20, features.ema50, features.ema200
        )
        if features.price is not None
        else None
    )
    score = _weighted(
        [
            (rsi_c, cfg.weight_rsi),
            (macd_c, cfg.weight_macd),
            (ema_c, cfg.weight_ema_structure),
        ]
    )
    rule = (
        f"{cfg.weight_rsi:.0%} RSI-скор ({_fmt(rsi_c)}) + "
        f"{cfg.weight_macd:.0%} MACD-скор ({_fmt(macd_c)}) + "
        f"{cfg.weight_ema_structure:.0%} EMA-структура ({_fmt(ema_c)})"
    )
    return _make("momentum", score, inputs, rule)


# --------------------------------------------------------------------------- #
# Growth / Fundamentals / RelStrength / Volume / Valuation / Catalysts
# --------------------------------------------------------------------------- #
def _growth(features_fund: Fundamentals, cfg) -> FactorScore:
    rg, eg = features_fund.revenue_growth, features_fund.eps_growth
    rev_c = (
        _clamp(50.0 + rg * 100.0 * cfg.revenue_growth_multiplier)
        if rg is not None
        else None
    )
    eps_c = (
        _clamp(50.0 + eg * 100.0 * cfg.eps_growth_multiplier)
        if eg is not None
        else None
    )
    score = _weighted([(rev_c, 0.5), (eps_c, 0.5)])
    rule = (
        f"0.5·clamp(50 + RevGrowth%·{cfg.revenue_growth_multiplier}) ({_fmt(rev_c)}) + "
        f"0.5·clamp(50 + EPSGrowth%·{cfg.eps_growth_multiplier}) ({_fmt(eps_c)})"
    )
    return _make(
        "growth", score, {"revenue_growth": rg, "eps_growth": eg}, rule
    )


def _fundamentals(fund: Fundamentals, cfg: FundamentalsConfig) -> FactorScore:
    eps, gm, de = fund.eps, fund.gross_margin, fund.debt_equity
    eps_c = (
        (cfg.eps_positive_score if eps > 0 else cfg.eps_negative_score)
        if eps is not None
        else None
    )
    gm_c = _clamp(gm * 100.0) if gm is not None else None
    de_c = _clamp(100.0 - de * cfg.debt_equity_penalty) if de is not None else None
    score = _weighted([(eps_c, 1.0), (gm_c, 1.0), (de_c, 1.0)])
    rule = (
        f"avg(EPS-скор {_fmt(eps_c)}, GrossMargin-скор {_fmt(gm_c)}, "
        f"D/E-скор {_fmt(de_c)} [обратная шкала])"
    )
    return _make(
        "fundamentals",
        score,
        {"eps": eps, "gross_margin": gm, "debt_equity": de},
        rule,
    )


def _relative_strength(features: FeatureSet, cfg) -> FactorScore:
    rel = features.relative_strength_63d
    score = (
        _clamp(50.0 + rel * cfg.rel_strength_multiplier) if rel is not None else None
    )
    rule = f"clamp(50 + RelStrength%·{cfg.rel_strength_multiplier})"
    return _make(
        "relative_strength", score, {"relative_strength_63d": rel}, rule
    )


def _volume(features: FeatureSet, cfg) -> FactorScore:
    ratio = features.volume_ratio20
    score = (
        _clamp(50.0 + (ratio - 1.0) * cfg.ratio_multiplier)
        if ratio is not None
        else None
    )
    rule = f"clamp(50 + (VolumeRatio − 1)·{cfg.ratio_multiplier})"
    return _make("volume", score, {"volume_ratio20": ratio}, rule)


def _valuation(fund: Fundamentals, cfg) -> FactorScore:
    pe = fund.forward_pe if fund.forward_pe is not None else fund.pe
    if pe is None:
        score = None
    elif pe <= 0:
        score = cfg.pe_negative_score
    elif pe <= cfg.pe_fair_low:
        score = 100.0
    elif pe >= cfg.pe_fair_high:
        score = 0.0
    else:
        score = _clamp(
            100.0 * (cfg.pe_fair_high - pe) / (cfg.pe_fair_high - cfg.pe_fair_low)
        )
    rule = (
        f"обратная шкала P/E в диапазоне [{cfg.pe_fair_low}, {cfg.pe_fair_high}] "
        f"(ниже P/E = выше балл)"
    )
    return _make(
        "valuation", score, {"pe": fund.pe, "forward_pe": fund.forward_pe}, rule
    )


def _catalysts(sentiment: float | None, cfg) -> FactorScore:
    score = (
        _clamp(50.0 + sentiment * cfg.sentiment_multiplier)
        if sentiment is not None
        else None
    )
    rule = (
        f"clamp(50 + sentiment·{cfg.sentiment_multiplier}), sentiment ∈ [-1, 1]"
        if sentiment is not None
        else "нет новостного сентимента — подключается на подэтапе 2.1"
    )
    return _make("catalysts", score, {"news_sentiment": sentiment}, rule)


# --------------------------------------------------------------------------- #
# Оркестрация
# --------------------------------------------------------------------------- #
def _fmt(value: float | None) -> str:
    return "н/д" if value is None else f"{value:.1f}"


def _make(
    name: str, score: float | None, inputs: dict[str, float | None], rule: str
) -> FactorScore:
    fs = FactorScore(
        name=name, score=score, inputs=inputs, rule=rule, is_incomplete=score is None
    )
    logger.info(
        "factor=%s score=%s inputs=%s rule=%s",
        name,
        _fmt(score),
        {k: v for k, v in inputs.items() if v is not None},
        rule,
    )
    return fs


def compute_factor_scores(
    features: FeatureSet,
    fundamentals: Fundamentals,
    config: ScoringConfig | None = None,
    *,
    news_sentiment: float | None = None,
) -> FactorScores:
    """Считает все 7 факторных Score по FeatureSet и Fundamentals.

    ``news_sentiment`` (∈ [-1, 1]) подаётся на подэтапе 2.1; в Этапе 1 фактор
    Catalysts остаётся неполным.
    """
    cfg: FactorScoresConfig = (config or load_scoring_config()).factor_scores
    return FactorScores(
        momentum=_momentum(features, cfg.momentum),
        growth=_growth(fundamentals, cfg.growth),
        fundamentals=_fundamentals(fundamentals, cfg.fundamentals),
        relative_strength=_relative_strength(features, cfg.relative_strength),
        volume=_volume(features, cfg.volume),
        valuation=_valuation(fundamentals, cfg.valuation),
        catalysts=_catalysts(news_sentiment, cfg.catalysts),
    )
