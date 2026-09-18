"""«Сырые» DTO данных источников (ТЗ раздел 6).

Клиенты источников возвращают эти модели ровно так, как пришли данные из API:
отсутствующие числовые значения — ``None`` (никогда не 0 и не выдуманное
значение, ТЗ 6.6). Проверка качества, нормализация и дедупликация выполняются
слоем нормализации (``app.ingestion.normalization``) до передачи в Feature Engine.

Каждая модель несёт поля ``source`` и ``fetched_at`` — происхождение и время
получения данных сохраняются вместе с расчётом (ТЗ 6.1, 13).
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
    price: float | None = None


class RawPriceBar(SourcedModel):
    """OHLCV-бар «как пришёл» — числовые поля могут быть None (пропуск)."""

    ticker: str
    date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class RawPriceHistory(SourcedModel):
    ticker: str
    bars: list[RawPriceBar]


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
    # Проставляется слоем нормализации: часть ключевых метрик отсутствует.
    is_incomplete: bool = False


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
