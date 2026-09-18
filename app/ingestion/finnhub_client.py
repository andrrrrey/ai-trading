"""Клиент Finnhub — второй резерв и источник новостного сентимента (ТЗ 6.3).

Авторизация через заголовок X-Finnhub-Token. Используется как резерв новостей;
доступность news-sentiment endpoint на free-тарифе — открытый вопрос Stage 0
(ТЗ раздел 16).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.ingestion.base_client import BaseSourceClient, HealthRecorder
from app.ingestion.schemas import NewsItem


def _now() -> datetime:
    return datetime.now(UTC)


def _from_unix(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


class FinnhubClient(BaseSourceClient):
    source = "finnhub"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://finnhub.io/api/v1",
        timeout: float = 15.0,
        max_retries: int = 3,
        health_recorder: HealthRecorder | None = None,
    ):
        super().__init__(
            source="finnhub",
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={"X-Finnhub-Token": api_key},
            health_recorder=health_recorder,
        )

    async def get_company_news(self, ticker: str, *, days: int = 7) -> list[NewsItem]:
        today = _now().date()
        start = today - timedelta(days=days)
        data = await self._get_json(
            "/company-news",
            params={
                "symbol": ticker,
                "from": start.isoformat(),
                "to": today.isoformat(),
            },
        )
        fetched = _now()
        rows = data if isinstance(data, list) else []
        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            headline = row.get("headline")
            if not headline:
                continue
            items.append(
                NewsItem(
                    ticker=ticker,
                    title=str(headline),
                    published_at=_from_unix(row.get("datetime")),
                    url=row.get("url"),
                    source=self.source,
                    fetched_at=fetched,
                )
            )
        return items

    async def get_news_sentiment(self, ticker: str) -> dict[str, Any]:
        """Новостной сентимент по тикеру (доступность — открытый вопрос Stage 0)."""
        data = await self._get_json("/news-sentiment", params={"symbol": ticker})
        return data if isinstance(data, dict) else {}
