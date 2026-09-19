"""Тесты базового Risk Filter (ТЗ раздел 9, DoD подэтапа 1.6)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.features.indicators import FeatureSet
from app.ingestion.schemas import Fundamentals
from app.risk import compute_risk
from app.status import TradeStatus

FETCHED = datetime(2025, 9, 18, tzinfo=UTC)


def feat(**kw) -> FeatureSet:
    """«Здоровые» фичи без единого флага риска; переопределяем нужное поле."""
    base = dict(
        price=100.0, ema20=95.0, ema50=90.0, ema200=85.0, rsi14=60.0,
        atr14=1.0, atr_pct=0.01, volume_ratio20=1.2, avg_volume_20d=1_000_000.0,
        gap_pct=1.0, distance_to_ema20=5.0,
    )
    base.update(kw)
    return FeatureSet(ticker="AAPL", **base)


# --------------------------------------------------------------------------- #
# Нет флагов
# --------------------------------------------------------------------------- #
def test_no_flags_low_risk_allows_buy():
    r = compute_risk(feat())
    assert r.active_flags == []
    assert r.risk_level == "low"
    assert r.allowed_max_status == TradeStatus.BUY


# --------------------------------------------------------------------------- #
# Каждый флаг на граничных значениях
# --------------------------------------------------------------------------- #
def test_high_volatility_boundary():
    assert "high_volatility" in compute_risk(feat(atr_pct=0.06)).flag_names()
    assert "high_volatility" not in compute_risk(feat(atr_pct=0.04)).flag_names()


def test_high_volatility_ceiling_watch():
    r = compute_risk(feat(atr_pct=0.06))
    assert r.allowed_max_status == TradeStatus.WATCH


def test_gap_risk_both_directions():
    assert "gap_risk" in compute_risk(feat(gap_pct=6.0)).flag_names()
    assert "gap_risk" in compute_risk(feat(gap_pct=-6.0)).flag_names()
    assert "gap_risk" not in compute_risk(feat(gap_pct=4.0)).flag_names()


def test_overextension_boundary_and_ceiling():
    r = compute_risk(feat(distance_to_ema20=16.0))
    assert "overextension" in r.flag_names()
    assert r.allowed_max_status == TradeStatus.BUY_ON_DIP
    assert "overextension" not in compute_risk(feat(distance_to_ema20=14.0)).flag_names()


def test_liquidity_risk_makes_high_and_watch():
    r = compute_risk(feat(avg_volume_20d=400_000.0))
    assert "liquidity_risk" in r.flag_names()
    assert r.risk_level == "high"           # liquidity_risk → high
    assert r.allowed_max_status == TradeStatus.WATCH
    assert "liquidity_risk" not in compute_risk(feat(avg_volume_20d=600_000.0)).flag_names()


def test_event_risk_within_business_days():
    # today=Пн 2025-09-15, earnings=Ср 2025-09-17 → 2 раб. дня <= 3
    r = compute_risk(
        feat(), next_earnings_date=date(2025, 9, 17), today=date(2025, 9, 15)
    )
    names = r.flag_names()
    assert "event_risk" in names
    ev = next(f for f in r.active_flags if f.flag == "event_risk")
    assert ev.value == 2.0
    assert r.allowed_max_status == TradeStatus.BUY_ON_DIP


def test_event_risk_far_earnings_no_flag():
    r = compute_risk(
        feat(), next_earnings_date=date(2025, 10, 15), today=date(2025, 9, 15)
    )
    assert "event_risk" not in r.flag_names()


# --------------------------------------------------------------------------- #
# missing_data — всегда high, максимум WATCH
# --------------------------------------------------------------------------- #
def test_missing_data_from_missing_volume():
    r = compute_risk(feat(avg_volume_20d=None))
    assert "missing_data" in r.flag_names()
    assert r.risk_level == "high"
    assert r.allowed_max_status == TradeStatus.WATCH


def test_missing_data_from_incomplete_fundamentals():
    fund = Fundamentals(
        ticker="AAPL", source="fmp", fetched_at=FETCHED, is_incomplete=True
    )
    r = compute_risk(feat(), fundamentals=fund)
    assert "missing_data" in r.flag_names()
    assert r.risk_level == "high"


def test_missing_data_from_incomplete_features():
    r = compute_risk(feat(rsi14=None, missing=["rsi14"]))
    assert "missing_data" in r.flag_names()
    reason = next(f for f in r.active_flags if f.flag == "missing_data").reason
    assert "rsi14" in reason


# --------------------------------------------------------------------------- #
# risk_level и комбинации
# --------------------------------------------------------------------------- #
def test_single_flag_is_medium():
    r = compute_risk(feat(distance_to_ema20=20.0))
    assert r.risk_level == "medium"


def test_three_flags_is_high():
    r = compute_risk(feat(atr_pct=0.06, gap_pct=7.0, distance_to_ema20=20.0))
    assert len(r.active_flags) == 3
    assert r.risk_level == "high"
    # самый строгий потолок среди флагов — WATCH (из high_volatility)
    assert r.allowed_max_status == TradeStatus.WATCH


def test_reasons_and_values_present():
    r = compute_risk(feat(atr_pct=0.08))
    flag = r.active_flags[0]
    assert flag.reason                       # причина показана
    assert flag.value is not None            # конкретное значение показано
