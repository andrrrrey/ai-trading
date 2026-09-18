"""Тесты нормализации и контроля качества (ТЗ 6.6, DoD подэтапа 1.2)."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.ingestion.normalization import (
    normalize_fundamentals,
    normalize_price_history,
)
from app.ingestion.schemas import Fundamentals, RawPriceBar, RawPriceHistory

FETCHED = datetime(2026, 9, 18, tzinfo=UTC)


def _raw_bar(d: date, **kw) -> RawPriceBar:
    fields = {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000.0}
    fields.update(kw)
    return RawPriceBar(
        ticker="AAPL", date=d, source="fmp", fetched_at=FETCHED, **fields
    )


def _history(bars: list[RawPriceBar]) -> RawPriceHistory:
    return RawPriceHistory(
        ticker="AAPL", bars=bars, source="fmp", fetched_at=FETCHED
    )


def test_sorted_and_deduplicated():
    raw = _history(
        [
            _raw_bar(date(2026, 9, 17), close=11.0),
            _raw_bar(date(2026, 9, 15)),
            _raw_bar(date(2026, 9, 17), close=11.5),  # дубль по дате
        ]
    )
    history, report = normalize_price_history(raw)

    dates = [b.date for b in history.bars]
    assert dates == [date(2026, 9, 15), date(2026, 9, 17)]  # отсортировано, без дублей
    assert report.duplicate_dates == 1
    # upsert: победил последний по дате
    assert history.bars[-1].close == 11.5


def test_missing_value_bar_dropped_not_fabricated():
    raw = _history(
        [
            _raw_bar(date(2026, 9, 16)),
            _raw_bar(date(2026, 9, 17), close=None),  # пропуск close
        ]
    )
    history, report = normalize_price_history(raw)

    assert len(history.bars) == 1
    assert report.dropped_invalid == 1
    # ни один бар не получил выдуманный 0
    assert all(b.close > 0 for b in history.bars)


def test_inconsistent_ohlc_dropped():
    raw = _history([_raw_bar(date(2026, 9, 17), high=8.0, low=9.0)])  # high < low
    history, report = normalize_price_history(raw)

    assert history.bars == []
    assert report.dropped_invalid == 1


def test_min_bars_marks_incomplete():
    raw = _history([_raw_bar(date(2026, 9, 17))])
    _, report = normalize_price_history(raw, min_bars=250)

    assert report.is_incomplete is True
    assert any("недостаточно истории" in issue for issue in report.issues)


def test_fundamentals_incomplete_keeps_none():
    raw = Fundamentals(
        ticker="AAPL",
        period="annual",
        revenue_growth=0.1,
        eps_growth=None,  # пропуск
        eps=6.0,
        gross_margin=0.4,
        debt_equity=1.1,
        pe=30.0,
        source="fmp",
        fetched_at=FETCHED,
    )
    normalized, report = normalize_fundamentals(raw)

    assert normalized.is_incomplete is True
    assert normalized.eps_growth is None  # не заменено на 0
    assert report.valid_rows == 5


def test_fundamentals_complete():
    raw = Fundamentals(
        ticker="AAPL", period="annual", revenue_growth=0.1, eps_growth=0.2,
        eps=6.0, gross_margin=0.4, debt_equity=1.1, pe=30.0,
        source="fmp", fetched_at=FETCHED,
    )
    normalized, report = normalize_fundamentals(raw)

    assert normalized.is_incomplete is False
    assert report.is_incomplete is False
