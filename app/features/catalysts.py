"""Детерминированная оценка новостей и подтверждённых SEC-событий.

Этап 1 не использует LLM для числовых расчётов. Заголовки новостей получают
прозрачную словарную оценку -1/0/+1, а filings SEC служат подтверждёнными
событиями и сохраняются как evidence. Одинаковый набор входов всегда даёт один
и тот же результат.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.schemas import Filing, NewsItem

_POSITIVE = {
    "beat",
    "beats",
    "growth",
    "upgrade",
    "upgrades",
    "approval",
    "approved",
    "record",
    "profit",
    "profits",
    "surge",
    "surges",
    "raise",
    "raises",
    "raised",
    "strong",
    "expands",
    "expansion",
    "partnership",
    "contract",
    "buyback",
    "dividend",
    "breakthrough",
    "outperform",
}
_NEGATIVE = {
    "miss",
    "misses",
    "decline",
    "declines",
    "downgrade",
    "downgrades",
    "loss",
    "losses",
    "cuts",
    "cut",
    "weak",
    "lawsuit",
    "probe",
    "fraud",
    "recall",
    "bankruptcy",
    "layoff",
    "layoffs",
    "warning",
    "underperform",
    "investigation",
    "default",
}
_TOKEN_RE = re.compile(r"[a-z]+")


class CatalystEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    title: str
    date: str | None = None
    source: str
    url: str | None = None
    score: float | None = None


class CatalystAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    sentiment: float | None
    news_count: int = 0
    filing_count: int = 0
    sources: list[str] = Field(default_factory=list)
    latest_news_at: str | None = None
    latest_filing_date: str | None = None
    evidence: list[CatalystEvidence] = Field(default_factory=list)
    rule: str = (
        "sentiment = среднее прозрачных оценок заголовков (-1/0/+1); "
        "filings SEC сохраняются как подтверждённые события"
    )

    @property
    def has_data(self) -> bool:
        return self.news_count > 0 or self.filing_count > 0


def _headline_score(title: str) -> float:
    tokens = set(_TOKEN_RE.findall(title.lower()))
    positive = len(tokens & _POSITIVE)
    negative = len(tokens & _NEGATIVE)
    if positive == negative:
        return 0.0
    return 1.0 if positive > negative else -1.0


def analyze_catalysts(
    news: list[NewsItem], filings: list[Filing], *, evidence_limit: int = 8
) -> CatalystAnalysis:
    """Дедуплицирует входы и формирует воспроизводимый Catalyst-контекст."""
    unique_news: list[NewsItem] = []
    seen_titles: set[str] = set()
    for item in sorted(
        news,
        key=lambda n: (
            n.published_at or datetime.min.replace(tzinfo=UTC),
            " ".join(n.title.lower().split()),
            n.source,
            n.url or "",
            n.title.strip(),
        ),
        reverse=True,
    ):
        key = " ".join(item.title.lower().split())
        if key and key not in seen_titles:
            seen_titles.add(key)
            unique_news.append(item)

    unique_filings: list[Filing] = []
    seen_filings: set[tuple[str, str | None]] = set()
    for filing in sorted(
        filings,
        key=lambda f: f.filed_date.isoformat() if f.filed_date else "",
        reverse=True,
    ):
        key = (filing.form, filing.accession_number)
        if key not in seen_filings:
            seen_filings.add(key)
            unique_filings.append(filing)

    headline_scores = [_headline_score(item.title) for item in unique_news]
    sentiment = sum(headline_scores) / len(headline_scores) if headline_scores else None

    evidence: list[CatalystEvidence] = []
    for item, score in zip(unique_news, headline_scores, strict=False):
        evidence.append(
            CatalystEvidence(
                kind="news",
                title=item.title,
                date=item.published_at.isoformat() if item.published_at else None,
                source=item.source,
                url=item.url,
                score=score,
            )
        )
    for filing in unique_filings:
        evidence.append(
            CatalystEvidence(
                kind="filing",
                title=f"SEC {filing.form}",
                date=filing.filed_date.isoformat() if filing.filed_date else None,
                source=filing.source,
            )
        )

    sources = sorted({item.source for item in unique_news} | {f.source for f in unique_filings})
    latest_news = next((n.published_at for n in unique_news if n.published_at), None)
    latest_filing = next((f.filed_date for f in unique_filings if f.filed_date), None)
    return CatalystAnalysis(
        sentiment=sentiment,
        news_count=len(unique_news),
        filing_count=len(unique_filings),
        sources=sources,
        latest_news_at=latest_news.isoformat() if latest_news else None,
        latest_filing_date=latest_filing.isoformat() if latest_filing else None,
        evidence=evidence[:evidence_limit],
    )
