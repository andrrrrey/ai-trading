"""Feature Engine — технические индикаторы (ТЗ раздел 7).

Все формулы реализованы вручную на pandas/numpy: это даёт полный контроль над
версией формулы (принцип воспроизводимости, КП с. 6) и не зависит от внешних
библиотек (pandas-ta на момент ТЗ — pre-release).

Вход — нормализованная ``PriceHistory`` из слоя ingestion (подэтап 1.2): бары уже
отсортированы по дате, без дублей и пропусков OHLCV. Функции-индикаторы работают
над ``pandas.Series`` и возвращают ряд той же длины (значения до накопления
достаточной истории — ``NaN``). Агрегатор ``compute_features`` собирает последние
значения в ``FeatureSet`` для Score Engine и честно помечает нехватку истории
(``None`` + список ``missing`` — никаких выдуманных чисел).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.ingestion.normalization import PriceHistory


# --------------------------------------------------------------------------- #
# Базовые скользящие средние
# --------------------------------------------------------------------------- #
def sma(series: pd.Series, n: int) -> pd.Series:
    """Простое скользящее среднее за n периодов."""
    return series.rolling(window=n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """Экспоненциальное скользящее среднее (ТЗ 7).

    ``EMA_t = price_t·k + EMA_{t-1}·(1-k)``, ``k = 2/(N+1)``; старт — SMA первых N
    баров (значение появляется начиная с индекса N-1).
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) < n:
        return result
    k = 2.0 / (n + 1)
    prev = float(series.iloc[:n].mean())
    result.iloc[n - 1] = prev
    values = series.to_numpy(dtype=float)
    for i in range(n, len(series)):
        prev = values[i] * k + prev * (1 - k)
        result.iloc[i] = prev
    return result


# --------------------------------------------------------------------------- #
# Wilder-сглаживание (для RSI и ATR)
# --------------------------------------------------------------------------- #
def _wilder(values: np.ndarray, n: int, seed_start: int) -> np.ndarray:
    """Сглаживание Уайлдера (RMA).

    Первое значение — среднее ``n`` элементов, начиная с ``seed_start``, ставится
    в позицию ``seed_start + n - 1``; далее ``avg_t = (avg_{t-1}·(n-1) + x_t)/n``.
    """
    out = np.full(len(values), np.nan, dtype=float)
    seed_end = seed_start + n  # срез [seed_start, seed_end)
    if seed_end > len(values):
        return out
    prev = float(np.mean(values[seed_start:seed_end]))
    out[seed_end - 1] = prev
    for i in range(seed_end, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """RSI по Уайлдеру (ТЗ 7): ``RS = avg_gain/avg_loss``, ``RSI = 100 − 100/(1+RS)``."""
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) <= n:
        return result
    delta = series.diff().to_numpy(dtype=float)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    # delta[0] = NaN → сглаживание стартует с индекса 1 (первый реальный дельта)
    gains[0] = 0.0
    losses[0] = 0.0
    avg_gain = _wilder(gains, n, seed_start=1)
    avg_loss = _wilder(losses, n, seed_start=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.divide(avg_gain, avg_loss)
        rsi_vals = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 (только рост) → RSI = 100
    rsi_vals = np.where((avg_loss == 0) & ~np.isnan(avg_gain), 100.0, rsi_vals)
    return pd.Series(rsi_vals, index=series.index, dtype=float)


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 12/26/9 (ТЗ 7): (MACD, Signal, Histogram)."""
    macd_line = ema(series, fast) - ema(series, slow)
    # Signal = EMA9 по ряду MACD (считаем по валидному хвосту, чтобы seed=SMA был
    # корректным, а не смешивался с ведущими NaN).
    signal_line = pd.Series(np.nan, index=series.index, dtype=float)
    valid = macd_line.dropna()
    if len(valid) >= signal:
        signal_line.loc[valid.index] = ema(valid, signal).to_numpy()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """ATR по Уайлдеру (ТЗ 7). ``TR = max(H−L, |H−C_{t-1}|, |L−C_{t-1}|)``."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0])  # нет предыдущего close
    atr_vals = _wilder(tr.to_numpy(dtype=float), n, seed_start=0)
    return pd.Series(atr_vals, index=close.index, dtype=float)


# --------------------------------------------------------------------------- #
# Объём, изменение цены, относительная сила
# --------------------------------------------------------------------------- #
def volume_ratio(volume: pd.Series, n: int = 20) -> pd.Series:
    """Отношение текущего объёма к среднему за n периодов (ТЗ 7)."""
    avg = sma(volume, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = volume / avg
    return ratio.replace([np.inf, -np.inf], np.nan)


def price_change(series: pd.Series, n: int) -> pd.Series:
    """Процентное изменение цены за n баров: ``(close_t − close_{t-n})/close_{t-n}·100``."""
    return series.pct_change(periods=n, fill_method=None) * 100.0


def relative_strength(
    ticker_close: pd.Series, benchmark_close: pd.Series, n: int = 63
) -> float | None:
    """Относительная сила против бенчмарка (SPY) за n дней, в проц. пунктах (ТЗ 7).

    ``RelStrength = (ret_n(ticker) − ret_n(SPY))·100``, ``ret_n = close_t/close_{t-n} − 1``.
    """
    ticker_ret = _period_return(ticker_close, n)
    bench_ret = _period_return(benchmark_close, n)
    if ticker_ret is None or bench_ret is None:
        return None
    return (ticker_ret - bench_ret) * 100.0


def _period_return(series: pd.Series, n: int) -> float | None:
    if len(series) <= n:
        return None
    last = float(series.iloc[-1])
    base = float(series.iloc[-1 - n])
    if base == 0:
        return None
    return last / base - 1.0


def gap_pct(open_price: float, prev_close: float) -> float | None:
    """Гэп открытия: ``(open_t − close_{t-1})/close_{t-1}·100`` (ТЗ 7)."""
    if prev_close == 0:
        return None
    return (open_price - prev_close) / prev_close * 100.0


def distance_to_ema(close: float, ema_value: float | None) -> float | None:
    """Отклонение цены от EMA: ``(close − EMA)/EMA·100`` (ТЗ 7)."""
    if ema_value is None or ema_value == 0 or np.isnan(ema_value):
        return None
    return (close - ema_value) / ema_value * 100.0


# --------------------------------------------------------------------------- #
# Агрегатор фич
# --------------------------------------------------------------------------- #
def price_history_to_frame(history: PriceHistory) -> pd.DataFrame:
    """Преобразует нормализованную историю в DataFrame, индексированный по дате."""
    rows = [
        {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in history.bars
    ]
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.set_index("date").sort_index()
    return frame


def _last_valid(series: pd.Series) -> float | None:
    """Последнее не-NaN значение ряда как float, иначе None."""
    valid = series.dropna()
    if valid.empty:
        return None
    return float(valid.iloc[-1])


class FeatureSet(BaseModel):
    """Последние значения технических метрик по тикеру — вход Score Engine (1.4)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    price: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    atr14: float | None = None
    atr_pct: float | None = None
    volume_ratio20: float | None = None
    avg_volume_20d: float | None = None
    price_change_1d: float | None = None
    price_change_5d: float | None = None
    price_change_20d: float | None = None
    relative_strength_63d: float | None = None
    gap_pct: float | None = None
    distance_to_ema20: float | None = None
    distance_to_ema50: float | None = None
    is_incomplete: bool = False
    missing: list[str] = []


# Критические метрики: их отсутствие делает набор фич неполным (→ missing_data в 1.6).
_CRITICAL_FEATURES = ("price", "ema20", "rsi14", "atr14")


def compute_features(
    history: PriceHistory, benchmark: PriceHistory | None = None
) -> FeatureSet:
    """Считает FeatureSet по истории цен и (опционально) бенчмарку SPY.

    Метрики, для которых не хватает истории, остаются None и попадают в ``missing``;
    никакие значения не выдумываются (ТЗ 6.6, 7).
    """
    frame = price_history_to_frame(history)
    if frame.empty:
        return FeatureSet(
            ticker=history.ticker, is_incomplete=True, missing=["price_history"]
        )

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]

    ema20 = _last_valid(ema(close, 20))
    ema50 = _last_valid(ema(close, 50))
    ema200 = _last_valid(ema(close, 200))
    macd_line, macd_signal, macd_hist = macd(close)
    atr14 = _last_valid(atr(high, low, close, 14))
    price = float(close.iloc[-1])
    avg_vol20 = _last_valid(sma(volume, 20))

    bench_close = (
        price_history_to_frame(benchmark)["close"]
        if benchmark is not None and benchmark.bars
        else None
    )
    rel_strength = (
        relative_strength(close, bench_close) if bench_close is not None else None
    )

    features = FeatureSet(
        ticker=history.ticker,
        price=price,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi14=_last_valid(rsi(close, 14)),
        macd=_last_valid(macd_line),
        macd_signal=_last_valid(macd_signal),
        macd_histogram=_last_valid(macd_hist),
        atr14=atr14,
        atr_pct=(atr14 / price if atr14 is not None and price else None),
        volume_ratio20=_last_valid(volume_ratio(volume, 20)),
        avg_volume_20d=avg_vol20,
        price_change_1d=_last_valid(price_change(close, 1)),
        price_change_5d=_last_valid(price_change(close, 5)),
        price_change_20d=_last_valid(price_change(close, 20)),
        relative_strength_63d=rel_strength,
        gap_pct=(
            gap_pct(float(frame["open"].iloc[-1]), float(close.iloc[-2]))
            if len(close) >= 2
            else None
        ),
        distance_to_ema20=distance_to_ema(price, ema20),
        distance_to_ema50=distance_to_ema(price, ema50),
    )

    missing = [f for f in _CRITICAL_FEATURES if getattr(features, f) is None]
    return features.model_copy(update={"missing": missing, "is_incomplete": bool(missing)})
