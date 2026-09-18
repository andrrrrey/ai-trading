"""Клиент Alpaca Market Data — резервный источник OHLCV (ТЗ раздел 6.3).

Авторизация через заголовки APCA-API-KEY-ID / APCA-API-SECRET-KEY. Basic-тариф
даёт данные IEX; исторические дневные бары доступны и на free-тарифе — подходит
как fallback для цен, если FMP недоступен.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.ingestion.base_client import BaseSourceClient, HealthRecorder
from app.ingestion.schemas import RawPriceBar, RawPriceHistory

_NEW_YORK = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(UTC)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar_date(value: Any) -> date | None:
    """Дата бара в торговой зоне America/New_York (ТЗ 6.6)."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_NEW_YORK).date()


class AlpacaClient(BaseSourceClient):
    source = "alpaca"

    def __init__(
        self,
        *,
        api_key_id: str,
        api_secret_key: str,
        base_url: str = "https://data.alpaca.markets",
        timeout: float = 15.0,
        max_retries: int = 3,
        health_recorder: HealthRecorder | None = None,
    ):
        super().__init__(
            source="alpaca",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
            },
            health_recorder=health_recorder,
        )

    async def get_price_history(
        self, ticker: str, *, timeframe: str = "1Day", limit: int = 300
    ) -> RawPriceHistory:
        data = await self._get_json(
            f"/v2/stocks/{ticker}/bars",
            params={"timeframe": timeframe, "limit": limit, "adjustment": "raw"},
        )
        raw_bars = data.get("bars", []) if isinstance(data, dict) else []
        fetched = _now()
        bars = [
            RawPriceBar(
                ticker=ticker,
                date=_bar_date(row.get("t")),
                open=_to_float(row.get("o")),
                high=_to_float(row.get("h")),
                low=_to_float(row.get("l")),
                close=_to_float(row.get("c")),
                volume=_to_float(row.get("v")),
                source=self.source,
                fetched_at=fetched,
            )
            for row in raw_bars
            if isinstance(row, dict) and _bar_date(row.get("t")) is not None
        ]
        return RawPriceHistory(
            ticker=ticker, bars=bars, source=self.source, fetched_at=fetched
        )
