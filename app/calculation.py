"""Расчётное ядро одного сигнала: входные данные → метрики → 7 факторов → Final
Score → Risk Filter (ТЗ разделы 2, 7–9).

Одна и та же функция используется и в живом пайплайне, и при воспроизведении
расчёта по сохранённой записи истории (``replay_from_raw_inputs``). Поэтому
повтор на зафиксированных входных данных гарантированно идёт по тому же коду
(чек-лист 1.7.9, 1.10.4).

``raw_inputs`` — снимок исходных данных, на которых выполнен расчёт: OHLCV тикера
и бенчмарка, fundamentals, дата отчётности, новости, SEC filings, доступность
источников, причины неполноты данных, текущая котировка, профиль и дата расчёта.

Полнота входных данных (версия формул v1.2+, ``assess_data_quality``) делится на
два уровня (ТЗ раздел 8: «при отсутствии критичных данных система снижает
confidence либо не формирует сигнал»):

- критично (``insufficient``) — Final Score не выдаётся;
- некритично (``reduced``) — Final Score выдаётся с пониженной достоверностью.

В обоих случаях причины попадают во флаг missing_data. Пороги — в разделе
``data_quality`` config/thresholds.yaml.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.features.catalysts import CatalystAnalysis, analyze_catalysts
from app.features.indicators import FeatureSet, compute_features
from app.ingestion.normalization import KEY_FUNDAMENTAL_FIELDS, PriceBar, PriceHistory
from app.ingestion.schemas import CompanyProfile, Filing, Fundamentals, NewsItem, Quote
from app.risk import RiskAssessment, compute_risk
from app.scoring import (
    FactorScores,
    FinalScore,
    ScoringConfig,
    compute_factor_scores,
    compute_final_score,
)
from app.scoring.thresholds import DataQualityConfig

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


CONFIDENCE_HIGH = "high"
CONFIDENCE_REDUCED = "reduced"
CONFIDENCE_INSUFFICIENT = "insufficient"


class DataQuality(BaseModel):
    """Оценка полноты данных расчёта: достоверность и причины по уровням."""

    model_config = ConfigDict(frozen=True)

    confidence: str  # high | reduced | insufficient
    critical: list[str] = []
    reduced: list[str] = []

    @property
    def reasons(self) -> list[str]:
        return [*self.critical, *self.reduced]


class CalculationResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    features: FeatureSet
    catalysts: CatalystAnalysis
    factor_scores: FactorScores
    final: FinalScore
    risk: RiskAssessment
    # None — версии формул до v1.2
    data_quality: DataQuality | None = None


def _detail(data_issues: list[str] | None, prefix: str, default: str) -> str:
    """Причина из ingestion (с источником и классом ошибки), если она есть."""
    for issue in data_issues or []:
        if issue.startswith(prefix):
            return issue
    return default


def assess_data_quality(
    *,
    cfg: DataQualityConfig,
    price_history: PriceHistory,
    benchmark: PriceHistory | None,
    fundamentals: Fundamentals,
    features: FeatureSet,
    factor_scores: dict[str, int | None],
    earnings_available: bool,
    news_available: bool,
    filings_available: bool,
    data_issues: list[str] | None = None,
) -> DataQuality:
    critical: list[str] = []
    reduced: list[str] = []

    bars = len(price_history.bars)
    if bars < cfg.min_history_bars_critical:
        critical.append(
            f"история цен {bars} < {cfg.min_history_bars_critical} баров — EMA200 не рассчитать"
        )
    elif bars < cfg.min_history_bars_full:
        reduced.append(f"история цен {bars} < {cfg.min_history_bars_full} баров")

    if benchmark is None or not benchmark.bars:
        critical.append(
            _detail(data_issues, "бенчмарк", "бенчмарк SPY недоступен")
            + " — Relative Strength не рассчитать"
        )

    missing_metrics = list(features.missing)
    if features.avg_volume_20d is None and "avg_volume_20d" not in missing_metrics:
        missing_metrics.append("avg_volume_20d")
    if missing_metrics and bars >= cfg.min_history_bars_critical:
        critical.append("нет ключевых метрик: " + ", ".join(missing_metrics))

    missing_fund = [f for f in KEY_FUNDAMENTAL_FIELDS if getattr(fundamentals, f) is None]
    failed_parts = (
        " (недоступны запросы: " + ", ".join(fundamentals.unavailable_parts) + ")"
        if fundamentals.unavailable_parts
        else ""
    )
    if len(missing_fund) >= cfg.fundamentals_missing_critical:
        critical.append(
            f"нет {len(missing_fund)} из {len(KEY_FUNDAMENTAL_FIELDS)} ключевых "
            "fundamentals: " + ", ".join(missing_fund) + failed_parts
        )
    elif missing_fund:
        reduced.append("нет fundamentals: " + ", ".join(missing_fund) + failed_parts)
    elif fundamentals.unavailable_parts:
        # ключевые показатели есть, но часть отчётности недоступна (напр. forward P/E
        # из key-metrics — тогда Valuation считается по текущему P/E)
        reduced.append(
            "fundamentals: недоступны запросы " + ", ".join(fundamentals.unavailable_parts)
        )

    if not news_available and not filings_available:
        critical.append("недоступны и новости, и SEC EDGAR — Catalysts не рассчитать")
    elif not news_available:
        reduced.append(_detail(data_issues, "новости", "новости недоступны"))
    elif not filings_available:
        reduced.append(_detail(data_issues, "SEC EDGAR", "SEC EDGAR недоступен"))

    if not earnings_available:
        reduced.append(
            _detail(data_issues, "дата отчётности", "дата отчётности неизвестна")
            + " — риск близкой отчётности не проверен"
        )

    for name, value in factor_scores.items():
        if value is None:
            critical.append(f"фактор {name} не рассчитан")

    if critical:
        confidence = CONFIDENCE_INSUFFICIENT
    elif reduced:
        confidence = CONFIDENCE_REDUCED
    else:
        confidence = CONFIDENCE_HIGH
    return DataQuality(confidence=confidence, critical=critical, reduced=reduced)


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
    news_available: bool = True,
    filings_available: bool = True,
    data_issues: list[str] | None = None,
) -> CalculationResult:
    features = compute_features(price_history, benchmark)
    catalysts = analyze_catalysts(
        news,
        filings,
        news_available=news_available,
        filings_available=filings_available,
        calc_date=calc_date,
        config=config.factor_scores.catalysts,
    )
    factor_scores = compute_factor_scores(features, fundamentals, config, catalysts=catalysts)
    final = compute_final_score(factor_scores, config)

    if config.data_quality is not None:
        quality = assess_data_quality(
            cfg=config.data_quality,
            price_history=price_history,
            benchmark=benchmark,
            fundamentals=fundamentals,
            features=features,
            factor_scores=final.factor_scores,
            earnings_available=earnings_available,
            news_available=news_available,
            filings_available=filings_available,
            data_issues=data_issues,
        )
        if quality.critical and final.final_score is not None:
            final = final.model_copy(
                update={
                    "final_score": None,
                    "is_incomplete": True,
                    "rule": final.rule + "; Final Score не выдан: критичная неполнота данных",
                }
            )
        risk = compute_risk(
            features,
            next_earnings_date=next_earnings_date,
            news_sentiment=catalysts.sentiment,
            missing_reasons=quality.reasons,
            today=calc_date,
            config=config.risk_filter,
        )
        return CalculationResult(
            features=features,
            catalysts=catalysts,
            factor_scores=factor_scores,
            final=final,
            risk=risk,
            data_quality=quality,
        )

    # Версии формул до v1.2: неполнота только помечается флагом.
    missing = [
        f"фактор {name} не рассчитан"
        for name, value in final.factor_scores.items()
        if value is None
    ]
    if data_issues is not None:
        missing.extend(data_issues)
    elif not earnings_available:  # записи, сохранённые до появления data_issues
        missing.append("earnings")
    risk = compute_risk(
        features,
        next_earnings_date=next_earnings_date,
        fundamentals=fundamentals,
        news_sentiment=catalysts.sentiment,
        missing_factors=missing,
        today=calc_date,
        config=config.risk_filter,
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
    news_available: bool = True,
    filings_available: bool = True,
    data_issues: list[str] | None = None,
    quote: Quote | None = None,
    profile: CompanyProfile | None = None,
    warnings: list[str] | None = None,
    quality: dict | None = None,
    earnings_api_available: bool = True,
) -> dict:
    """JSON-снимок всех исходных данных расчёта (сохраняется в историю)."""
    return {
        "news_available": news_available,
        "filings_available": filings_available,
        "data_issues": list(data_issues or []),
        "warnings": list(warnings or []),
        "quote": quote.model_dump(mode="json") if quote else None,
        "profile": profile.model_dump(mode="json") if profile else None,
        "calc_date": calc_date.isoformat(),
        "price_history": _history_to_raw(price_history),
        "benchmark": _history_to_raw(benchmark),
        "fundamentals": fundamentals.model_dump(mode="json"),
        "earnings": {
            "next_earnings_date": (
                next_earnings_date.isoformat() if next_earnings_date else None
            ),
            # available — известна будущая дата; api_available — API ответил
            "available": earnings_available,
            "api_available": earnings_api_available,
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
        news_available=raw.get("news_available", True),
        filings_available=raw.get("filings_available", True),
        data_issues=raw.get("data_issues"),
    )
