"""Маршрутизатор источников данных (ТЗ раздел 6.5).

Выбирает основной источник (FMP) и переключается на резерв при ошибке/таймауте:
цены → Alpaca, новости → Finnhub. Возвращаемые DTO уже содержат поле ``source``,
поэтому в сохранённых данных видно, какой источник фактически использован.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.base_client import SourceError
from app.ingestion.finnhub_client import FinnhubClient
from app.ingestion.fmp_client import FMPClient
from app.ingestion.schemas import (
    Earnings,
    Filing,
    Fundamentals,
    NewsItem,
    Quote,
    RawPriceHistory,
)
from app.ingestion.sec_edgar_client import SECEdgarClient

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AllSourcesUnavailableError(SourceError):
    """Основной и резервный источники недоступны для критичных данных."""


class SourceRouter:
    def __init__(
        self,
        *,
        fmp: FMPClient,
        alpaca: AlpacaClient | None = None,
        finnhub: FinnhubClient | None = None,
        sec_edgar: SECEdgarClient | None = None,
    ):
        self._fmp = fmp
        self._alpaca = alpaca
        self._finnhub = finnhub
        self._sec = sec_edgar

    async def aclose(self) -> None:
        for client in (self._fmp, self._alpaca, self._finnhub, self._sec):
            if client is not None:
                await client.aclose()

    async def _with_fallback(
        self,
        *,
        what: str,
        primary: Callable[[], Awaitable[T]],
        primary_name: str,
        fallback: Callable[[], Awaitable[T]] | None = None,
        fallback_name: str | None = None,
    ) -> T:
        try:
            return await primary()
        except SourceError as primary_exc:
            if fallback is None:
                raise
            logger.warning(
                "source=%s failed for %s (%s) → переключение на резерв %s",
                primary_name,
                what,
                primary_exc,
                fallback_name,
            )
            try:
                return await fallback()
            except SourceError as fallback_exc:
                raise AllSourcesUnavailableError(
                    f"{what}: и основной ({primary_name}), и резервный "
                    f"({fallback_name}) источники недоступны",
                    source=primary_name,
                ) from fallback_exc

    async def get_price_history(self, ticker: str) -> RawPriceHistory:
        """История цен: FMP → Alpaca."""
        fallback = None
        if self._alpaca is not None:
            fallback = lambda: self._alpaca.get_price_history(ticker)  # noqa: E731
        return await self._with_fallback(
            what=f"price_history[{ticker}]",
            primary=lambda: self._fmp.get_price_history(ticker),
            primary_name="fmp",
            fallback=fallback,
            fallback_name="alpaca" if fallback else None,
        )

    async def get_news(self, ticker: str) -> list[NewsItem]:
        """Новости: FMP → Finnhub."""
        fallback = None
        if self._finnhub is not None:
            fallback = lambda: self._finnhub.get_company_news(ticker)  # noqa: E731
        return await self._with_fallback(
            what=f"news[{ticker}]",
            primary=lambda: self._fmp.get_news(ticker),
            primary_name="fmp",
            fallback=fallback,
            fallback_name="finnhub" if fallback else None,
        )

    async def get_quote(self, ticker: str) -> Quote:
        return await self._fmp.get_quote(ticker)

    async def get_fundamentals(self, ticker: str, period: str = "annual") -> Fundamentals:
        return await self._fmp.get_fundamentals(ticker, period=period)

    async def get_earnings(self, ticker: str) -> Earnings:
        return await self._fmp.get_earnings(ticker)

    async def get_filings(self, ticker: str) -> list[Filing]:
        """Филинги — только SEC EDGAR (официальный источник)."""
        if self._sec is None:
            raise SourceError("sec_edgar не сконфигурирован", source="sec_edgar")
        return await self._sec.get_recent_filings(ticker)


def build_source_router(settings, health_recorder=None) -> SourceRouter:
    """Собирает SourceRouter из настроек приложения (app.config.Settings).

    Резервные клиенты (Alpaca/Finnhub/SEC) создаются только при наличии ключей —
    на MVP они опциональны, но при наличии подключаются как fallback.
    """
    fmp = FMPClient(
        api_key=settings.fmp_api_key,
        base_url=settings.fmp_base_url,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        health_recorder=health_recorder,
    )
    alpaca = None
    if settings.alpaca_api_key_id and settings.alpaca_api_secret_key:
        alpaca = AlpacaClient(
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
            base_url=settings.alpaca_base_url,
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            health_recorder=health_recorder,
        )
    finnhub = None
    if settings.finnhub_api_key:
        finnhub = FinnhubClient(
            api_key=settings.finnhub_api_key,
            base_url=settings.finnhub_base_url,
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            health_recorder=health_recorder,
        )
    sec = SECEdgarClient(
        user_agent=settings.sec_user_agent,
        base_url=settings.sec_base_url,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        health_recorder=health_recorder,
    )
    return SourceRouter(fmp=fmp, alpaca=alpaca, finnhub=finnhub, sec_edgar=sec)
