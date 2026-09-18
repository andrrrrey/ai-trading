"""Unit-тесты Feature Engine (ТЗ раздел 7, DoD подэтапа 1.3).

Эталонные значения — математически верифицируемые (константы/монотонные ряды)
и ручные примеры рекурренты Уайлдера (RSI/ATR), рассчитанные в комментариях;
плюс регрессия и проверки чувствительности/идемпотентности на CSV-фикстуре.
"""
from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from app.features import (
    atr,
    compute_features,
    distance_to_ema,
    ema,
    gap_pct,
    macd,
    price_change,
    relative_strength,
    rsi,
    sma,
    volume_ratio,
)
from app.ingestion.normalization import PriceBar, PriceHistory

FIXTURE = Path(__file__).parent / "fixtures" / "sample_ohlcv.csv"
APPROX = {"abs": 0.01}


def load_history(path: Path = FIXTURE, ticker: str = "TEST") -> PriceHistory:
    fetched = datetime(2025, 9, 18, tzinfo=UTC)
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                PriceBar(
                    ticker=ticker,
                    date=date.fromisoformat(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    source="fixture",
                    fetched_at=fetched,
                )
            )
    return PriceHistory(ticker=ticker, bars=bars, source="fixture", fetched_at=fetched)


# --------------------------------------------------------------------------- #
# EMA / SMA
# --------------------------------------------------------------------------- #
def test_ema_of_constant_is_constant():
    s = pd.Series([5.0] * 30)
    result = ema(s, 10)
    assert result.iloc[-1] == pytest.approx(5.0, **APPROX)
    assert pd.isna(result.iloc[8])  # до индекса n-1 — NaN


def test_ema_seed_and_step_hand_computed():
    # series 1..10, n=5. SMA первых 5 = 3.0 (индекс 4).
    # индекс 5: 6·k + 3·(1-k), k=2/6=0.3333 → 2.0 + 2.0 = 4.0
    s = pd.Series([float(i) for i in range(1, 11)])
    result = ema(s, 5)
    assert result.iloc[4] == pytest.approx(3.0, **APPROX)
    assert result.iloc[5] == pytest.approx(4.0, **APPROX)


def test_sma_window():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sma(s, 3).iloc[-1] == pytest.approx(4.0, **APPROX)  # (3+4+5)/3


# --------------------------------------------------------------------------- #
# RSI (Wilder)
# --------------------------------------------------------------------------- #
def test_rsi_all_gains_is_100():
    s = pd.Series([float(i) for i in range(1, 40)])
    assert rsi(s, 14).iloc[-1] == pytest.approx(100.0, **APPROX)


def test_rsi_all_losses_is_zero():
    s = pd.Series([float(i) for i in range(40, 1, -1)])
    assert rsi(s, 14).iloc[-1] == pytest.approx(0.0, **APPROX)


def test_rsi_wilder_hand_computed():
    # prices [10,11,10,11,12], n=3.
    # avg_gain[3]=mean(1,0,1)=0.6667, avg_loss[3]=mean(0,1,0)=0.3333 → RS=2 → RSI=66.667
    # index4: avg_gain=0.7778, avg_loss=0.2222 → RS=3.5 → RSI=77.778
    s = pd.Series([10.0, 11.0, 10.0, 11.0, 12.0])
    r = rsi(s, 3)
    assert r.iloc[3] == pytest.approx(66.6667, **APPROX)
    assert r.iloc[4] == pytest.approx(77.7778, **APPROX)


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #
def test_macd_of_constant_is_zero():
    s = pd.Series([50.0] * 60)
    macd_line, signal_line, hist = macd(s)
    assert macd_line.iloc[-1] == pytest.approx(0.0, **APPROX)
    assert signal_line.iloc[-1] == pytest.approx(0.0, **APPROX)
    assert hist.iloc[-1] == pytest.approx(0.0, **APPROX)


def test_macd_equals_ema_difference():
    s = pd.Series([float(i) for i in range(1, 80)])
    macd_line, _, _ = macd(s)
    expected = ema(s, 12).iloc[-1] - ema(s, 26).iloc[-1]
    assert macd_line.iloc[-1] == pytest.approx(expected, **APPROX)


# --------------------------------------------------------------------------- #
# ATR (Wilder)
# --------------------------------------------------------------------------- #
def test_atr_wilder_hand_computed():
    # TR = [1, 1.5, 1, 2]; seed ATR[2]=mean(1,1.5,1)=1.1667; ATR[3]=(1.1667·2+2)/3=1.4444
    high = pd.Series([10.0, 11.0, 10.5, 12.0])
    low = pd.Series([9.0, 10.0, 9.5, 10.0])
    close = pd.Series([9.5, 10.5, 10.0, 11.5])
    a = atr(high, low, close, 3)
    assert a.iloc[2] == pytest.approx(1.1667, **APPROX)
    assert a.iloc[3] == pytest.approx(1.4444, **APPROX)


# --------------------------------------------------------------------------- #
# Volume / price change / rel.strength / gap / distance
# --------------------------------------------------------------------------- #
def test_volume_ratio_hand():
    v = pd.Series([100.0, 100.0, 100.0, 100.0, 200.0])
    # sma3 последнего = mean(100,100,200)=133.33 → 200/133.33 = 1.5
    assert volume_ratio(v, 3).iloc[-1] == pytest.approx(1.5, **APPROX)


def test_price_change():
    s = pd.Series([100.0, 105.0, 110.0, 120.0])
    assert price_change(s, 1).iloc[-1] == pytest.approx(9.0909, **APPROX)
    assert price_change(s, 2).iloc[-1] == pytest.approx(14.2857, **APPROX)


def test_relative_strength_hand():
    ticker = pd.Series([100.0, 100.0, 110.0])
    bench = pd.Series([100.0, 100.0, 105.0])
    # ret2 ticker=0.10, spy=0.05 → (0.10-0.05)·100 = 5.0
    assert relative_strength(ticker, bench, 2) == pytest.approx(5.0, **APPROX)


def test_relative_strength_insufficient_history_none():
    assert relative_strength(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0]), 5) is None


def test_gap_pct():
    assert gap_pct(11.0, 10.0) == pytest.approx(10.0, **APPROX)
    assert gap_pct(9.5, 10.0) == pytest.approx(-5.0, **APPROX)
    assert gap_pct(10.0, 0.0) is None


def test_distance_to_ema():
    assert distance_to_ema(110.0, 100.0) == pytest.approx(10.0, **APPROX)
    assert distance_to_ema(100.0, None) is None


# --------------------------------------------------------------------------- #
# compute_features (агрегатор) — фикстура, полнота, идемпотентность, чувствительность
# --------------------------------------------------------------------------- #
def test_compute_features_full_history():
    features = compute_features(load_history())
    assert features.is_incomplete is False
    assert features.missing == []
    for field in ("price", "ema20", "ema50", "ema200", "rsi14", "atr14"):
        assert getattr(features, field) is not None
    assert 0.0 <= features.rsi14 <= 100.0
    assert features.atr_pct is not None and features.atr_pct > 0


def test_compute_features_short_history_incomplete():
    history = load_history()
    short = PriceHistory(
        ticker=history.ticker,
        bars=history.bars[:30],  # мало для EMA200/EMA50
        source=history.source,
        fetched_at=history.fetched_at,
    )
    features = compute_features(short)
    assert features.ema200 is None  # честно None, не выдумано
    assert features.ema50 is None


def test_compute_features_empty_history():
    empty = PriceHistory(
        ticker="X", bars=[], source="fixture",
        fetched_at=datetime(2025, 9, 18, tzinfo=UTC),
    )
    features = compute_features(empty)
    assert features.is_incomplete is True
    assert features.price is None


def test_compute_features_idempotent():
    history = load_history()
    first = compute_features(history)
    second = compute_features(history)
    assert first == second  # воспроизводимость: идентичный вход → идентичный выход


def test_compute_features_sensitivity_to_last_close():
    history = load_history()
    base = compute_features(history)

    bars = list(history.bars)
    last = bars[-1]
    bumped = last.model_copy(update={"close": last.close + 10.0, "high": last.high + 10.0})
    bars[-1] = bumped
    changed = compute_features(
        PriceHistory(ticker=history.ticker, bars=bars, source=history.source,
                     fetched_at=history.fetched_at)
    )
    # рост последней цены предсказуемо повышает 1-дневное изменение и RSI
    assert changed.price_change_1d > base.price_change_1d
    assert changed.rsi14 > base.rsi14
