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
from app.ingestion.schemas import Earnings, Filing, Fundamentals, NewsItem
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
        ТЗ 6.6). Фундаментальные данные и earnings опциональны: их неполнота
        помечается в QualityReport и агрегируется в is_incomplete, но не роняет
        сбор (downstream Risk Filter решит про missing_data, подэтап 1.6).
        """
        price_task = self.get_price_history(ticker)
        fundamentals_task = self.get_fundamentals(ticker, period=period)
        earnings_task = self._optional_earnings(ticker)
        (
            (price_history, price_q),
            (fundamentals, fund_q),
            (earnings, earnings_q),
        ) = await asyncio.gather(price_task, fundamentals_task, earnings_task)

        news, filings = await asyncio.gather(
            self._optional_news(ticker), self._optional_filings(ticker)
        )

        quality = {
            "price_history": price_q,
            "fundamentals": fund_q,
            "earnings": earnings_q,
        }
        is_incomplete = (
            price_q.is_incomplete
            or fund_q.is_incomplete
            or earnings_q.is_incomplete
            or len(price_history.bars) == 0
        )
        return MarketData(
            ticker=ticker,
            price_history=price_history,
            fundamentals=fundamentals,
            earnings=earnings,
            news=news,
            filings=filings,
            quality=quality,
            is_incomplete=is_incomplete,
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
                    issues=["earnings недоступны"],
                ),
            )

    async def _optional_news(self, ticker: str) -> list[NewsItem]:
        try:
            return await self._router.get_news(ticker)
        except SourceError as exc:
            logger.warning("news unavailable ticker=%s: %s", ticker, exc)
            return []

    async def _optional_filings(self, ticker: str) -> list[Filing]:
        try:
            return await self._router.get_filings(ticker)
        except SourceError as exc:
            logger.warning("SEC filings unavailable ticker=%s: %s", ticker, exc)
            return []
