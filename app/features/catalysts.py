"""Детерминированная оценка катализаторов: новости + события SEC (фактор Catalysts).

Этап 1 не использует LLM для числовых расчётов. Правило v1.1:

1. Новости: каждый уникальный заголовок получает словарную оценку -1/0/+1;
   ``news_sentiment`` — среднее.
2. SEC (официальный источник, приоритетнее СМИ):
   - 8-K оценивается по пунктам события (positive/negative_8k_items в конфиге),
     уведомления о просрочке отчётности (NT 10-K / NT 10-Q) — всегда −1;
   - учитываются только события не старше ``sec_event_lookback_days``; вес
     события ``w = 1 − возраст/окно`` (свежее событие весит больше);
   - ``sec_event_score = Σ знак·w / Σ w`` по событиям с ненулевым знаком;
   - 10-K/10-Q в окне ``sec_report_lookback_days`` — подтверждение свежей
     отчётности (показывается как evidence, на знак не влияет).
3. ``signal`` = взвешенное среднее доступных компонент (news_weight, sec_weight).
   Если источники доступны, но событий нет — signal = 0 (нейтрально, 50 баллов).
   Если недоступны и новости, и SEC — signal = None (фактор не рассчитывается).

Одинаковый набор входов и дата расчёта всегда дают один и тот же результат.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.schemas import Filing, NewsItem
from app.scoring.thresholds import CatalystsConfig

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

    kind: str  # news | sec_event | sec_report | filing
    title: str
    date: str | None = None
    source: str
    url: str | None = None
    score: float | None = None
    weight: float | None = None


class CatalystAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Тональность новостей (вход news_risk в Risk Filter).
    sentiment: float | None
    sec_event_score: float | None = None
    # Итоговый сигнал Catalysts ∈ [-1, 1] (вход фактора).
    signal: float | None = None
    news_available: bool = True
    filings_available: bool = True
    news_count: int = 0
    filing_count: int = 0
    sec_event_count: int = 0
    recent_report_count: int = 0
    sources: list[str] = Field(default_factory=list)
    latest_news_at: str | None = None
    latest_filing_date: str | None = None
    calc_date: str | None = None
    evidence: list[CatalystEvidence] = Field(default_factory=list)
    rule: str = (
        "signal = (news_weight·news_sentiment + sec_weight·sec_event_score) / Σ весов "
        "доступных компонент; news_sentiment — среднее оценок заголовков (−1/0/+1); "
        "sec_event_score — Σ знак·w / Σ w по 8-K/NT за окно, w = 1 − возраст/окно"
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


def filing_event_sign(filing: Filing, cfg: CatalystsConfig) -> float:
    """Знак события SEC: +1 / −1 / 0 по форме и пунктам 8-K."""
    if filing.form in cfg.negative_forms:
        return -1.0
    if filing.form != "8-K":
        return 0.0
    items = {item.strip() for item in (filing.items or "").split(",") if item.strip()}
    if items & set(cfg.negative_8k_items):
        return -1.0
    if items & set(cfg.positive_8k_items):
        return 1.0
    return 0.0


def _filing_title(filing: Filing) -> str:
    title = f"SEC {filing.form}"
    if filing.items:
        title += f" (пункты {filing.items})"
    return title


def analyze_catalysts(
    news: list[NewsItem],
    filings: list[Filing],
    *,
    news_available: bool = True,
    filings_available: bool = True,
    calc_date: date | None = None,
    config: CatalystsConfig | None = None,
    evidence_limit: int = 8,
) -> CatalystAnalysis:
    """Дедуплицирует входы и формирует воспроизводимый Catalyst-контекст."""
    cfg = config or CatalystsConfig()
    calc_date = calc_date or datetime.now(UTC).date()

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
    seen_filings: set[tuple[str, str | None, str | None]] = set()
    for filing in sorted(
        filings,
        key=lambda f: (
            f.filed_date.isoformat() if f.filed_date else "",
            f.accession_number or "",
            f.form,
        ),
        reverse=True,
    ):
        key = (filing.form, filing.accession_number, str(filing.filed_date))
        if key not in seen_filings:
            seen_filings.add(key)
            unique_filings.append(filing)

    # --- новости ---
    headline_scores = [_headline_score(item.title) for item in unique_news]
    sentiment = sum(headline_scores) / len(headline_scores) if headline_scores else None

    # --- события SEC ---
    sec_evidence: list[CatalystEvidence] = []
    report_evidence: list[CatalystEvidence] = []
    signed_sum = 0.0
    signed_weight = 0.0
    sec_event_count = 0
    for filing in unique_filings:
        if filing.filed_date is None:
            continue
        age = (calc_date - filing.filed_date).days
        if age < 0:
            continue
        if filing.form == "8-K" or filing.form in cfg.negative_forms:
            if age >= cfg.sec_event_lookback_days:
                continue
            weight = 1.0 - age / cfg.sec_event_lookback_days
            sign = filing_event_sign(filing, cfg)
            sec_event_count += 1
            if sign != 0.0:
                signed_sum += sign * weight
                signed_weight += weight
            sec_evidence.append(
                CatalystEvidence(
                    kind="sec_event",
                    title=_filing_title(filing),
                    date=filing.filed_date.isoformat(),
                    source=filing.source,
                    score=sign,
                    weight=round(weight, 4),
                )
            )
        elif filing.form in ("10-K", "10-Q") and age < cfg.sec_report_lookback_days:
            report_evidence.append(
                CatalystEvidence(
                    kind="sec_report",
                    title=_filing_title(filing) + " — свежая отчётность",
                    date=filing.filed_date.isoformat(),
                    source=filing.source,
                )
            )
    sec_event_score = (signed_sum / signed_weight) if signed_weight > 0 else None

    # --- итоговый сигнал ---
    components: list[tuple[float, float]] = []
    if news_available and sentiment is not None:
        components.append((sentiment, cfg.news_weight))
    if filings_available and sec_event_score is not None:
        components.append((sec_event_score, cfg.sec_weight))
    if components:
        total = sum(w for _, w in components)
        signal: float | None = sum(v * w for v, w in components) / total
    elif news_available or filings_available:
        signal = 0.0  # источники ответили, значимых событий нет — нейтрально
    else:
        signal = None  # нет ни новостей, ни SEC — фактор не рассчитывается

    news_evidence = [
        CatalystEvidence(
            kind="news",
            title=item.title,
            date=item.published_at.isoformat() if item.published_at else None,
            source=item.source,
            url=item.url,
            score=score,
        )
        for item, score in zip(unique_news, headline_scores, strict=False)
    ]
    # SEC-события первыми: официальный источник приоритетнее СМИ.
    evidence = sec_evidence + news_evidence + report_evidence

    sources = sorted({item.source for item in unique_news} | {f.source for f in unique_filings})
    latest_news = next((n.published_at for n in unique_news if n.published_at), None)
    latest_filing = next((f.filed_date for f in unique_filings if f.filed_date), None)
    return CatalystAnalysis(
        sentiment=sentiment,
        sec_event_score=sec_event_score,
        signal=signal,
        news_available=news_available,
        filings_available=filings_available,
        news_count=len(unique_news),
        filing_count=len(unique_filings),
        sec_event_count=sec_event_count,
        recent_report_count=len(report_evidence),
        sources=sources,
        latest_news_at=latest_news.isoformat() if latest_news else None,
        latest_filing_date=latest_filing.isoformat() if latest_filing else None,
        calc_date=calc_date.isoformat(),
        evidence=evidence[:evidence_limit],
    )
