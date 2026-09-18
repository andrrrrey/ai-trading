"""DTO-модели данных источников (ТЗ раздел 6).

Каждая модель несёт поля ``source`` и ``fetched_at`` — требование подэтапа 1.1
("каждый клиент пишет source и fetched_at при сохранении данных"). Полная
нормализация, дедупликация и строгая валидация полей — подэтап 1.2.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SourcedModel(BaseModel):
    """Базовая модель с происхождением и временем получения данных."""

    model_config = ConfigDict(frozen=True)

    source: str
    fetched_at: datetime


class Quote(SourcedModel):
    ticker: str
    price: float


class PriceBar(SourcedModel):
    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceHistory(SourcedModel):
    ticker: str
    bars: list[PriceBar]


class Fundamentals(SourcedModel):
    ticker: str
    period: str | None = None
    revenue_growth: float | None = None
    eps_growth: float | None = None
    eps: float | None = None
    gross_margin: float | None = None
    debt_equity: float | None = None
    pe: float | None = None
    forward_pe: float | None = None


class Earnings(SourcedModel):
    ticker: str
    next_earnings_date: date | None = None


class NewsItem(SourcedModel):
    ticker: str
    title: str
    published_at: datetime | None = None
    url: str | None = None


class Filing(SourcedModel):
    ticker: str
    form: str
    filed_date: date | None = None
    accession_number: str | None = None
