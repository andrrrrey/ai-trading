"""Расчётное ядро одного сигнала: входные данные → метрики → 7 факторов → Final
Score → Risk Filter (ТЗ разделы 2, 7–9).

Одна и та же функция используется и в живом пайплайне, и при воспроизведении
расчёта по сохранённой записи истории (``replay_from_raw_inputs``). Поэтому
повтор на зафиксированных входных данных гарантированно идёт по тому же коду
(чек-лист 1.7.9, 1.10.4).

``raw_inputs`` — снимок исходных данных, на которых выполнен расчёт: OHLCV тикера
и бенчмарка, fundamentals, дата отчётности, новости, SEC filings и дата расчёта.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.features.catalysts import CatalystAnalysis, analyze_catalysts
from app.features.indicators import FeatureSet, compute_features
from app.ingestion.normalization import PriceBar, PriceHistory
from app.ingestion.schemas import Filing, Fundamentals, NewsItem
from app.risk import RiskAssessment, compute_risk
from app.scoring import (
    FactorScores,
    FinalScore,
    ScoringConfig,
    compute_factor_scores,
    compute_final_score,
)

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class CalculationResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    features: FeatureSet
    catalysts: CatalystAnalysis
    factor_scores: FactorScores
    final: FinalScore
    risk: RiskAssessment


def calculate(
    *,
    price_history: PriceHistory,
    benchmark: PriceHistory | None,
    fundamentals: Fundamentals,
    next_earnings_date: date | None,
    earnings_available: bool,
    news: list[NewsItem],
    filings: list[Filing],
    calc_date: date,
    config: ScoringConfig,
) -> CalculationResult:
    features = compute_features(price_history, benchmark)
    catalysts = analyze_catalysts(news, filings)
    factor_scores = compute_factor_scores(
        features, fundamentals, config, news_sentiment=catalysts.sentiment
    )
    final = compute_final_score(factor_scores, config)
    missing = [name for name, value in final.factor_scores.items() if value is None]
    if not earnings_available:
        missing.append("earnings")
    risk = compute_risk(
        features,
        next_earnings_date=next_earnings_date,
        fundamentals=fundamentals,
        news_sentiment=catalysts.sentiment,
        missing_factors=missing,
        today=calc_date,
    )
    return CalculationResult(
        features=features,
        catalysts=catalysts,
        factor_scores=factor_scores,
        final=final,
        risk=risk,
    )


# --------------------------------------------------------------------------- #
# Снимок исходных данных и воспроизведение
# --------------------------------------------------------------------------- #
def _history_to_raw(history: PriceHistory | None) -> dict | None:
    if history is None:
        return None
    return {
        "ticker": history.ticker,
        "source": history.source,
        "fetched_at": history.fetched_at.isoformat(),
        "columns": BAR_COLUMNS,
        "bars": [
            [b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in history.bars
        ],
    }


def _history_from_raw(raw: dict | None) -> PriceHistory | None:
    if not raw:
        return None
    fetched_at = datetime.fromisoformat(raw["fetched_at"])
    bars = [
        PriceBar(
            ticker=raw["ticker"],
            date=date.fromisoformat(row[0]),
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
            source=raw["source"],
            fetched_at=fetched_at,
        )
        for row in raw["bars"]
    ]
    return PriceHistory(
        ticker=raw["ticker"], bars=bars, source=raw["source"], fetched_at=fetched_at
    )


def build_raw_inputs(
    *,
    price_history: PriceHistory,
    benchmark: PriceHistory | None,
    fundamentals: Fundamentals,
    next_earnings_date: date | None,
    earnings_available: bool,
    news: list[NewsItem],
    filings: list[Filing],
    calc_date: date,
    quality: dict | None = None,
) -> dict:
    """JSON-снимок всех исходных данных расчёта (сохраняется в историю)."""
    return {
        "calc_date": calc_date.isoformat(),
        "price_history": _history_to_raw(price_history),
        "benchmark": _history_to_raw(benchmark),
        "fundamentals": fundamentals.model_dump(mode="json"),
        "earnings": {
            "next_earnings_date": (
                next_earnings_date.isoformat() if next_earnings_date else None
            ),
            "available": earnings_available,
        },
        "news": [item.model_dump(mode="json") for item in news],
        "filings": [item.model_dump(mode="json") for item in filings],
        "quality": quality or {},
    }


def replay_from_raw_inputs(raw: dict, config: ScoringConfig) -> CalculationResult:
    """Повторяет расчёт по сохранённому снимку исходных данных."""
    history = _history_from_raw(raw["price_history"])
    earnings = raw.get("earnings") or {}
    next_date = earnings.get("next_earnings_date")
    return calculate(
        price_history=history,
        benchmark=_history_from_raw(raw.get("benchmark")),
        fundamentals=Fundamentals.model_validate(raw["fundamentals"]),
        next_earnings_date=date.fromisoformat(next_date) if next_date else None,
        earnings_available=bool(earnings.get("available", True)),
        news=[NewsItem.model_validate(item) for item in raw.get("news") or []],
        filings=[Filing.model_validate(item) for item in raw.get("filings") or []],
        calc_date=date.fromisoformat(raw["calc_date"]),
        config=config,
    )
