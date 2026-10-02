"""Правило полноты данных v1.2: два уровня (ТЗ раздел 8, чек-лист 1.2.2, 1.6.6)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.calculation import assess_data_quality
from app.features.indicators import FeatureSet
from app.ingestion.normalization import PriceBar, PriceHistory
from app.ingestion.schemas import Fundamentals
from app.scoring.thresholds import DataQualityConfig, ScoringConfig, load_scoring_config

NOW = datetime(2026, 9, 30, tzinfo=UTC)
FACTORS_OK = {name: 50 for name in (
    "momentum", "growth", "fundamentals", "relative_strength", "volume", "valuation", "catalysts"
)}


def history(n: int) -> PriceHistory:
    bars = [
        PriceBar(ticker="X", date=date(2025, 1, 1) + timedelta(days=i), open=10, high=11,
                 low=9, close=10, volume=1_000_000, source="fmp", fetched_at=NOW)
        for i in range(n)
    ]
    return PriceHistory(ticker="X", bars=bars, source="fmp", fetched_at=NOW)


FUND_OK = Fundamentals(ticker="X", revenue_growth=0.1, eps_growth=0.1, eps=1.0,
                       gross_margin=0.4, debt_equity=1.0, pe=20.0, source="fmp", fetched_at=NOW)
FEATURES_OK = FeatureSet(ticker="X", price=10.0, avg_volume_20d=1_000_000)


def assess(**overrides):
    args = dict(
        cfg=DataQualityConfig(),
        price_history=history(260),
        benchmark=history(260),
        fundamentals=FUND_OK,
        features=FEATURES_OK,
        factor_scores=FACTORS_OK,
        earnings_available=True,
        news_available=True,
        filings_available=True,
    )
    args.update(overrides)
    return assess_data_quality(**args)


def test_complete_data_is_high_confidence():
    quality = assess()
    assert quality.confidence == "high" and quality.reasons == []


def test_unknown_earnings_date_reduces_confidence():
    quality = assess(earnings_available=False)
    assert quality.confidence == "reduced"
    assert "риск близкой отчётности не проверен" in quality.reduced[0]


def test_one_news_source_down_reduces_both_down_is_critical():
    assert assess(news_available=False).confidence == "reduced"
    sec = assess(
        filings_available=False, data_issues=["SEC EDGAR недоступен (sec_edgar: timeout)"]
    )
    assert sec.reduced == ["SEC EDGAR недоступен (sec_edgar: timeout)"]
    both = assess(news_available=False, filings_available=False)
    assert both.confidence == "insufficient"


def test_history_and_benchmark_thresholds():
    assert assess(price_history=history(200)).confidence == "reduced"
    assert assess(price_history=history(199)).confidence == "insufficient"
    assert assess(benchmark=None).confidence == "insufficient"


def test_missing_factor_is_critical():
    quality = assess(factor_scores={**FACTORS_OK, "valuation": None})
    assert quality.critical == ["фактор valuation не рассчитан"]


def test_thresholds_live_in_versioned_config():
    cfg = load_scoring_config()
    assert cfg.version == "v1.5"
    assert cfg.data_quality == DataQualityConfig(
        min_history_bars_critical=200,
        min_history_bars_full=250,
        fundamentals_missing_critical=3,
        max_last_bar_age_business_days=3,
        check_actively_trading=True,
    )
    # записи до v1.2 воспроизводятся без уровней неполноты
    legacy = ScoringConfig.model_validate({"version": "v1.1", "data_quality": None})
    assert legacy.data_quality is None
