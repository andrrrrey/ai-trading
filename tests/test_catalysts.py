"""Тесты детерминированного анализа новостей и SEC-событий."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.features.catalysts import analyze_catalysts
from app.ingestion.schemas import Filing, NewsItem

FETCHED = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def news(title: str, *, source: str = "fmp") -> NewsItem:
    return NewsItem(
        ticker="AAPL",
        title=title,
        published_at=FETCHED,
        url="https://example.test/news",
        source=source,
        fetched_at=FETCHED,
    )


def test_headline_sentiment_is_deterministic_and_deduplicated():
    inputs = [
        news("Company beats estimates and raises guidance"),
        news("  company BEATS estimates and raises guidance  "),
        news("Company faces fraud investigation"),
        news("Company announces quarterly results"),
    ]

    first = analyze_catalysts(inputs, [])
    second = analyze_catalysts(list(reversed(inputs)), [])

    assert first.news_count == 3
    assert first.sentiment == 0.0
    assert first.model_dump() == second.model_dump()
    assert sorted(item.score for item in first.evidence) == [-1.0, 0.0, 1.0]


def test_sec_filing_is_preserved_as_sourced_evidence():
    filing = Filing(
        ticker="AAPL",
        form="8-K",
        filed_date=date(2026, 9, 29),
        accession_number="0001",
        source="sec_edgar",
        fetched_at=FETCHED,
    )

    result = analyze_catalysts([], [filing, filing])

    assert result.sentiment is None
    assert result.filing_count == 1
    assert result.sources == ["sec_edgar"]
    assert result.latest_filing_date == "2026-09-29"
    assert result.evidence[0].title == "SEC 8-K"


CALC = date(2026, 9, 30)


def filing(form: str, filed: date, items: str | None = None, acc: str = "x") -> Filing:
    return Filing(
        ticker="AAPL",
        form=form,
        filed_date=filed,
        accession_number=acc,
        items=items,
        source="sec_edgar",
        fetched_at=FETCHED,
    )


def test_8k_items_give_deterministic_sign():
    pos = analyze_catalysts([], [filing("8-K", CALC, "1.01,9.01")], calc_date=CALC)
    neg = analyze_catalysts([], [filing("8-K", CALC, "2.02,4.02")], calc_date=CALC)
    neutral = analyze_catalysts([], [filing("8-K", CALC, "2.02,9.01")], calc_date=CALC)
    late = analyze_catalysts([], [filing("NT 10-Q", CALC)], calc_date=CALC)

    assert pos.sec_event_score == 1.0 and pos.signal == 1.0
    assert neg.sec_event_score == -1.0 and neg.signal == -1.0
    assert neutral.sec_event_score is None and neutral.signal == 0.0
    assert late.sec_event_score == -1.0


def test_recency_weight_and_lookback():
    fresh_neg = filing("8-K", CALC, "1.03", acc="a")  # w = 1.0
    old_pos = filing("8-K", date(2026, 9, 15), "1.01", acc="b")  # 15 дн → w = 0.5
    expired = filing("8-K", date(2026, 8, 1), "4.02", acc="c")  # старше 30 дн — не влияет

    result = analyze_catalysts([], [fresh_neg, old_pos, expired], calc_date=CALC)

    assert result.sec_event_count == 2
    assert result.sec_event_score == pytest.approx((-1.0 * 1.0 + 1.0 * 0.5) / 1.5)


def test_sec_has_priority_over_news():
    result = analyze_catalysts(
        [news("Company beats estimates")],  # +1
        [filing("8-K", CALC, "4.02")],  # −1
        calc_date=CALC,
    )
    # (1·(+1) + 2·(−1)) / 3
    assert result.signal == pytest.approx(-1.0 / 3.0)


def test_recent_report_is_confirmation_only():
    result = analyze_catalysts([], [filing("10-Q", date(2026, 8, 1))], calc_date=CALC)
    assert result.recent_report_count == 1
    assert result.signal == 0.0


def test_unavailable_sources():
    sec_down = analyze_catalysts(
        [news("Company beats estimates")], [], filings_available=False, calc_date=CALC
    )
    assert sec_down.signal == 1.0 and sec_down.filings_available is False

    both_down = analyze_catalysts(
        [], [], news_available=False, filings_available=False, calc_date=CALC
    )
    assert both_down.signal is None
