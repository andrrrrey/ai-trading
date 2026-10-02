"""Клиент Alpaca Market Data — резервный источник OHLCV (ТЗ раздел 6.3).

Авторизация через заголовки APCA-API-KEY-ID / APCA-API-SECRET-KEY. Basic-тариф
даёт данные IEX; исторические дневные бары доступны и на free-тарифе — подходит
как fallback для цен, если FMP недоступен.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.ingestion.base_client import (
    ERROR_NO_DATA,
    BaseSourceClient,
    HealthRecorder,
    SourceError,
)
from app.ingestion.schemas import Quote, RawPriceBar, RawPriceHistory

_NEW_YORK = ZoneInfo("America/New_York")

# Глубина истории в календарных днях: ~500 торговых баров — с запасом для EMA200.
# Без явного ``start`` Alpaca отдаёт бары только с начала текущего дня.
DEFAULT_LOOKBACK_DAYS = 730
PRICE_ADJUSTMENT = "split"
# Фид рыночных данных Alpaca: "iex" (бесплатный тариф — только биржа IEX, это
# несколько процентов общего объёма торгов) или "sip" (все биржи США, платный).
DEFAULT_FEED = "iex"
FULL_VOLUME_FEEDS = frozenset({"sip"})


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
        feed: str = DEFAULT_FEED,
    ):
        self.feed = (feed or DEFAULT_FEED).lower()
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
        self,
        ticker: str,
        *,
        timeframe: str = "1Day",
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        limit: int = 10000,
    ) -> RawPriceHistory:
        fetched = _now()
        start = (fetched.date() - timedelta(days=lookback_days)).isoformat()
        params: dict[str, Any] = {
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            # Единая политика с основным источником: FMP historical-price-eod/full
            # отдаёт цены, скорректированные на сплиты (без дивидендов). Alpaca
            # "split" делает то же самое; "raw" дал бы скачок цены в день сплита
            # и исказил EMA, ATR, гэп и Momentum в резервном режиме.
            "adjustment": PRICE_ADJUSTMENT,
            "feed": self.feed,
        }
        raw_bars: list[Any] = []
        page_token: str | None = None
        while True:
            if page_token:
                params["page_token"] = page_token
            data = await self._get_json(f"/v2/stocks/{ticker}/bars", params=params)
            if not isinstance(data, dict):
                break
            raw_bars.extend(data.get("bars") or [])
            page_token = data.get("next_page_token")
            if not page_token:
                break
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
        if not bars:
            raise SourceError(
                f"alpaca: нет баров для {ticker}", source=self.source, kind=ERROR_NO_DATA
            )
        return RawPriceHistory(
            ticker=ticker,
            bars=bars,
            source=self.source,
            fetched_at=fetched,
            # IEX-объёмы несопоставимы с порогом ликвидности по всему рынку.
            volume_partial=self.feed not in FULL_VOLUME_FEEDS,
        )

    async def get_latest_quote(self, ticker: str) -> Quote:
        """Цена последней сделки (резерв текущей котировки, ТЗ 4: Alpaca — quotes)."""
        data = await self._get_json(
            f"/v2/stocks/{ticker}/trades/latest", params={"feed": self.feed}
        )
        trade = data.get("trade") if isinstance(data, dict) else None
        price = _to_float(trade.get("p")) if isinstance(trade, dict) else None
        if price is None:
            raise SourceError(
                f"alpaca: нет последней сделки для {ticker}",
                source=self.source,
                kind=ERROR_NO_DATA,
            )
        moment = None
        if trade.get("t"):
            try:
                moment = datetime.fromisoformat(str(trade["t"]).replace("Z", "+00:00"))
            except ValueError:
                moment = None
        return Quote(
            ticker=ticker,
            price=price,
            quote_time=moment,
            source=self.source,
            fetched_at=_now(),
        )
