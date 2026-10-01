"""Сервис ingestion: источники + контроль качества (ТЗ раздел 6.6).

IngestionService связывает выбор источника (SourceRouter, ТЗ 6.5) с нормализацией
и проверкой качества (normalization, ТЗ 6.6) и отдаёт наверх (Feature Engine)
уже очищенные данные вместе с QualityReport. Если данные получить невозможно —
пробрасывается честная ошибка источника, а не частичный/выдуманный расчёт.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.base_client import SourceError
from app.ingestion.normalization import (
    PriceHistory,
    QualityReport,
    normalize_fundamentals,
    normalize_price_history,
)
from app.ingestion.schemas import (
    CompanyProfile,
    Earnings,
    Filing,
    Fundamentals,
    NewsItem,
    Quote,
)
from app.ingestion.source_router import SourceRouter

# Минимальная глубина истории для корректного EMA200 (ТЗ раздел 7).
DEFAULT_MIN_HISTORY_BARS = 250
logger = logging.getLogger(__name__)


class MarketData(BaseModel):
    """Очищенный набор входных данных по тикеру для Feature/Score Engine."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    price_history: PriceHistory
    fundamentals: Fundamentals
    earnings: Earnings
    news: list[NewsItem] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    quality: dict[str, QualityReport]
    is_incomplete: bool
    quote: Quote | None = None
    profile: CompanyProfile | None = None
    news_available: bool = True
    filings_available: bool = True
    # Причины неполноты входных данных → флаг missing_data в Risk Filter.
    data_issues: list[str] = Field(default_factory=list)
    # Некритичные предупреждения (показываются пользователю, на риск не влияют).
    warnings: list[str] = Field(default_factory=list)


class IngestionService:
    def __init__(self, router: SourceRouter, *, min_history_bars: int = DEFAULT_MIN_HISTORY_BARS):
        self._router = router
        self._min_history_bars = min_history_bars

    async def get_price_history(
        self, ticker: str, *, min_bars: int | None = None
    ) -> tuple[PriceHistory, QualityReport]:
        raw = await self._router.get_price_history(ticker)
        return normalize_price_history(
            raw, min_bars=self._min_history_bars if min_bars is None else min_bars
        )

    async def get_fundamentals(
        self, ticker: str, period: str = "annual"
    ) -> tuple[Fundamentals, QualityReport]:
        raw = await self._router.get_fundamentals(ticker, period=period)
        return normalize_fundamentals(raw)

    async def get_market_data(self, ticker: str, *, period: str = "annual") -> MarketData:
        """Собирает и нормализует полный набор входных данных по тикеру.

        Цены критичны — их недоступность пробрасывается как ошибка (честный отказ,
        ТЗ 6.6). Остальные наборы опциональны: их отсутствие или неполнота не
        роняют сбор, но попадают в ``data_issues`` (→ флаг missing_data и пометка
        «неполный расчёт») либо в ``warnings``.
        """
        (
            (price_history, price_q),
            (fundamentals, fund_q),
            (earnings, earnings_q),
        ) = await asyncio.gather(
            self.get_price_history(ticker),
            self.get_fundamentals(ticker, period=period),
            self._optional_earnings(ticker),
        )
        (
            (news, news_error),
            (filings, filings_error),
            (quote, quote_error),
            (profile, profile_error),
        ) = await asyncio.gather(
            self._optional(self._router.get_news(ticker), "news", ticker, default=[]),
            self._optional(self._router.get_filings(ticker), "filings", ticker, default=[]),
            self._optional(self._router.get_quote(ticker), "quote", ticker),
            self._optional(self._router.get_profile(ticker), "profile", ticker),
        )

        quality = {
            "price_history": price_q,
            "fundamentals": fund_q,
            "earnings": earnings_q,
        }
        data_issues: list[str] = []
        if price_q.is_incomplete:
            data_issues.append("история цен: " + "; ".join(price_q.issues))
        if fund_q.is_incomplete:
            data_issues.append("fundamentals: " + ", ".join(fund_q.issues))
        if earnings_q.is_incomplete:
            data_issues.append("дата отчётности: " + "; ".join(earnings_q.issues))
        if news_error:
            data_issues.append(f"новости недоступны ({news_error})")
        if filings_error:
            data_issues.append(f"SEC EDGAR недоступен ({filings_error})")

        warnings: list[str] = []
        if quote_error:
            warnings.append(
                f"текущая котировка недоступна ({quote_error}) — показана цена закрытия EOD"
            )
        if profile_error:
            warnings.append(f"корпоративный профиль недоступен ({profile_error})")

        is_incomplete = bool(data_issues) or len(price_history.bars) == 0
        return MarketData(
            ticker=ticker,
            price_history=price_history,
            fundamentals=fundamentals,
            earnings=earnings,
            news=news,
            filings=filings,
            quality=quality,
            is_incomplete=is_incomplete,
            quote=quote,
            profile=profile,
            news_available=news_error is None,
            filings_available=filings_error is None,
            data_issues=data_issues,
            warnings=warnings,
        )

    async def _optional_earnings(self, ticker: str) -> tuple[Earnings, QualityReport]:
        try:
            earnings = await self._router.get_earnings(ticker)
            return earnings, QualityReport(source=earnings.source, total_rows=1, valid_rows=1)
        except SourceError as exc:
            logger.warning("earnings unavailable ticker=%s: %s", ticker, exc)
            return (
                Earnings(
                    ticker=ticker,
                    next_earnings_date=None,
                    source=exc.source or "unavailable",
                    fetched_at=datetime.now(UTC),
                ),
                QualityReport(
                    source=exc.source or "unavailable",
                    total_rows=1,
                    valid_rows=0,
                    is_incomplete=True,
                    issues=[f"недоступна ({exc.source}: {exc.kind})"],
                ),
            )

    @staticmethod
    async def _optional(coro, what: str, ticker: str, *, default=None):
        """Опциональный набор: (значение, None) или (default, «источник: причина»)."""
        try:
            return await coro, None
        except SourceError as exc:
            logger.warning("%s unavailable ticker=%s: %s", what, ticker, exc)
            return default, f"{exc.source}: {exc.kind}"
