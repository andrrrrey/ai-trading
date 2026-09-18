"""Сервис ingestion: источники + контроль качества (ТЗ раздел 6.6).

IngestionService связывает выбор источника (SourceRouter, ТЗ 6.5) с нормализацией
и проверкой качества (normalization, ТЗ 6.6) и отдаёт наверх (Feature Engine)
уже очищенные данные вместе с QualityReport. Если данные получить невозможно —
пробрасывается честная ошибка источника, а не частичный/выдуманный расчёт.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.ingestion.normalization import (
    PriceHistory,
    QualityReport,
    normalize_fundamentals,
    normalize_price_history,
)
from app.ingestion.schemas import Earnings, Fundamentals
from app.ingestion.source_router import SourceRouter

# Минимальная глубина истории для корректного EMA200 (ТЗ раздел 7).
DEFAULT_MIN_HISTORY_BARS = 250


class MarketData(BaseModel):
    """Очищенный набор входных данных по тикеру для Feature/Score Engine."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    price_history: PriceHistory
    fundamentals: Fundamentals
    earnings: Earnings
    quality: dict[str, QualityReport]
    is_incomplete: bool


class IngestionService:
    def __init__(
        self, router: SourceRouter, *, min_history_bars: int = DEFAULT_MIN_HISTORY_BARS
    ):
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
        price_history, price_q = await self.get_price_history(ticker)
        fundamentals, fund_q = await self.get_fundamentals(ticker, period=period)
        earnings = await self._router.get_earnings(ticker)

        quality = {"price_history": price_q, "fundamentals": fund_q}
        is_incomplete = (
            price_q.is_incomplete
            or fund_q.is_incomplete
            or len(price_history.bars) == 0
        )
        return MarketData(
            ticker=ticker,
            price_history=price_history,
            fundamentals=fundamentals,
            earnings=earnings,
            quality=quality,
            is_incomplete=is_incomplete,
        )
