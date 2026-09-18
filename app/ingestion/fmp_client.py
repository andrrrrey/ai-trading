"""Клиент Financial Modeling Prep — основной источник (ТЗ раздел 6.1).

Авторизация через query-параметр ``apikey``. Эндпоинты семейства ``/stable/*``.
Клиент возвращает DTO с проставленными ``source="fmp"`` и ``fetched_at``.
Глубокая нормализация/валидация — подэтап 1.2.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.ingestion.base_client import BaseSourceClient, HealthRecorder, SourceError
from app.ingestion.schemas import (
    Earnings,
    Fundamentals,
    NewsItem,
    PriceBar,
    PriceHistory,
    Quote,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class FMPClient(BaseSourceClient):
    source = "fmp"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://financialmodelingprep.com",
        timeout: float = 15.0,
        max_retries: int = 3,
        health_recorder: HealthRecorder | None = None,
    ):
        super().__init__(
            source="fmp",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_params={"apikey": api_key},
            health_recorder=health_recorder,
        )

    async def get_quote(self, ticker: str) -> Quote:
        data = await self._get_json("/stable/quote", params={"symbol": ticker})
        row = _first_row(data, source=self.source, ticker=ticker, what="quote")
        return Quote(
            ticker=ticker,
            price=_to_float(row.get("price")) or 0.0,
            source=self.source,
            fetched_at=_now(),
        )

    async def get_price_history(self, ticker: str) -> PriceHistory:
        data = await self._get_json(
            "/stable/historical-price-eod/full", params={"symbol": ticker}
        )
        rows = _as_rows(data)
        fetched = _now()
        bars = [
            PriceBar(
                ticker=ticker,
                date=_to_date(row.get("date")),
                open=_to_float(row.get("open")) or 0.0,
                high=_to_float(row.get("high")) or 0.0,
                low=_to_float(row.get("low")) or 0.0,
                close=_to_float(row.get("close")) or 0.0,
                volume=_to_float(row.get("volume")) or 0.0,
                source=self.source,
                fetched_at=fetched,
            )
            for row in rows
            if _to_date(row.get("date")) is not None
        ]
        return PriceHistory(
            ticker=ticker, bars=bars, source=self.source, fetched_at=fetched
        )

    async def get_profile(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json("/stable/profile", params={"symbol": ticker})
        return _first_row(data, source=self.source, ticker=ticker, what="profile")

    async def get_ratios(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json("/stable/ratios", params={"symbol": ticker})
        return _first_row(data, source=self.source, ticker=ticker, what="ratios")

    async def get_key_metrics(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json("/stable/key-metrics", params={"symbol": ticker})
        return _first_row(data, source=self.source, ticker=ticker, what="key-metrics")

    async def get_income_growth(self, ticker: str) -> dict[str, Any]:
        data = await self._get_json(
            "/stable/income-statement-growth", params={"symbol": ticker}
        )
        return _first_row(
            data, source=self.source, ticker=ticker, what="income-statement-growth"
        )

    async def get_fundamentals(self, ticker: str, period: str = "annual") -> Fundamentals:
        """Композиция ratios + key-metrics + income-statement-growth.

        Все три запроса используют один и тот же ``period`` (ТЗ 6.6) — Revenue
        Growth и EPS Growth не смешивают квартальные и годовые данные.
        """
        ratios = await self._get_json(
            "/stable/ratios", params={"symbol": ticker, "period": period}
        )
        metrics = await self._get_json(
            "/stable/key-metrics", params={"symbol": ticker, "period": period}
        )
        growth = await self._get_json(
            "/stable/income-statement-growth",
            params={"symbol": ticker, "period": period},
        )
        r = _as_rows(ratios)[:1]
        m = _as_rows(metrics)[:1]
        g = _as_rows(growth)[:1]
        r = r[0] if r else {}
        m = m[0] if m else {}
        g = g[0] if g else {}
        return Fundamentals(
            ticker=ticker,
            period=period,
            revenue_growth=_to_float(g.get("growthRevenue")),
            eps_growth=_to_float(g.get("growthEPS")),
            eps=_to_float(m.get("eps") or r.get("eps")),
            gross_margin=_to_float(
                r.get("grossProfitMargin") or m.get("grossProfitMargin")
            ),
            debt_equity=_to_float(
                r.get("debtEquityRatio") or m.get("debtToEquity")
            ),
            pe=_to_float(r.get("priceEarningsRatio") or m.get("peRatio")),
            forward_pe=_to_float(m.get("forwardPE")),
            source=self.source,
            fetched_at=_now(),
        )

    async def get_earnings(self, ticker: str) -> Earnings:
        data = await self._get_json("/stable/earnings", params={"symbol": ticker})
        rows = _as_rows(data)
        next_date = _next_earnings_date(rows)
        return Earnings(
            ticker=ticker,
            next_earnings_date=next_date,
            source=self.source,
            fetched_at=_now(),
        )

    async def get_news(self, ticker: str, limit: int = 20) -> list[NewsItem]:
        data = await self._get_json(
            "/stable/news/stock", params={"symbols": ticker, "limit": limit}
        )
        fetched = _now()
        items: list[NewsItem] = []
        for row in _as_rows(data):
            title = row.get("title")
            if not title:
                continue
            published = row.get("publishedDate") or row.get("date")
            items.append(
                NewsItem(
                    ticker=ticker,
                    title=str(title),
                    published_at=_parse_dt(published),
                    url=row.get("url"),
                    source=self.source,
                    fetched_at=fetched,
                )
            )
        return items


def _as_rows(data: Any) -> list[dict[str, Any]]:
    """Приводит ответ FMP к списку словарей (API отдаёт list или объект)."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first_row(data: Any, *, source: str, ticker: str, what: str) -> dict[str, Any]:
    rows = _as_rows(data)
    if not rows:
        raise SourceError(f"{source}: пустой ответ {what} для {ticker}", source=source)
    return rows[0]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text)
        except ValueError:
            continue
    d = _to_date(value)
    return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None


def _next_earnings_date(rows: list[dict[str, Any]]) -> date | None:
    """Ближайшая будущая дата отчётности из списка earnings."""
    today = datetime.now(UTC).date()
    future = sorted(
        d
        for row in rows
        if (d := _to_date(row.get("date") or row.get("epsReportedDate"))) is not None
        and d >= today
    )
    return future[0] if future else None
