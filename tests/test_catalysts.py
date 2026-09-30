"""Тесты детерминированного анализа новостей и SEC-событий."""

from __future__ import annotations

from datetime import UTC, date, datetime

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
