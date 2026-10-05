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
    """Текущая котировка: цена и момент, к которому она относится (биржевое время)."""

    ticker: str
    price: float | None = None
    quote_time: datetime | None = None


class CompanyProfile(SourcedModel):
    """Корпоративные данные эмитента (FMP /stable/profile)."""

    ticker: str
    company_name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    market_cap: float | None = None
    is_actively_trading: bool | None = None


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
    # Объёмы покрывают лишь часть рынка (Alpaca free = только биржа IEX):
    # абсолютный порог ликвидности по ним не проверяется.
    volume_partial: bool = False


class Fundamentals(SourcedModel):
    ticker: str
    period: str | None = None
    # Даты и периоды сохраняются вместе со значениями: без них нельзя доказать,
    # что EPS, growth и мультипликаторы относятся к сопоставимой отчётности.
    statement_date: date | None = None
    fiscal_year: str | None = None
    growth_date: date | None = None
    growth_fiscal_year: str | None = None
    revenue_growth: float | None = None
    eps_growth: float | None = None
    eps_growth_basis: str | None = None
    eps: float | None = None
    eps_previous: float | None = None
    eps_basis: str | None = None
    gross_margin: float | None = None
    debt_equity: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    # P/E за последние 12 месяцев (FMP ratios-ttm); годовой pe может отставать на год.
    pe_ttm: float | None = None
    pe_ttm_as_of: datetime | None = None
    # Проставляется слоем нормализации: часть ключевых метрик отсутствует.
    is_incomplete: bool = False
    # Запросы, которые не удалось выполнить: "key-metrics (fmp: timeout)".
    unavailable_parts: list[str] = []


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
    # Пункты 8-K (например "2.02,9.01") — тип события по классификации SEC.
    items: str | None = None
