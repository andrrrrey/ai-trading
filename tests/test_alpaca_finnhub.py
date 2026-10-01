"""Контрактные тесты резервных клиентов Alpaca и Finnhub (ТЗ 6.3)."""
from __future__ import annotations

import re
from datetime import date

import httpx
import respx

from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.finnhub_client import FinnhubClient
from tests.conftest import FakeHealthRecorder


@respx.mock
async def test_alpaca_price_history():
    client = AlpacaClient(
        api_key_id="ID", api_secret_key="SECRET", health_recorder=FakeHealthRecorder()
    )
    respx.get(re.compile(r".*/v2/stocks/AAPL/bars")).mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "AAPL",
                "bars": [
                    {"t": "2026-09-17T04:00:00Z", "o": 1, "h": 2, "l": 0.5,
                     "c": 1.5, "v": 1000}
                ],
            },
        )
    )
    history = await client.get_price_history("AAPL")
    await client.aclose()

    assert len(history.bars) == 1
    assert history.bars[0].date == date(2026, 9, 17)
    assert history.bars[0].source == "alpaca"


@respx.mock
async def test_finnhub_company_news():
    client = FinnhubClient(api_key="TOKEN", health_recorder=FakeHealthRecorder())
    respx.get(re.compile(r".*/company-news")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"headline": "Beats estimates", "datetime": 1758067200,
                 "url": "http://x"},
                {"headline": None},
            ],
        )
    )
    news = await client.get_company_news("AAPL")
    await client.aclose()

    assert len(news) == 1
    assert news[0].title == "Beats estimates"
    assert news[0].source == "finnhub"
    assert news[0].published_at is not None


@respx.mock
async def test_alpaca_requests_history_depth_and_paginates():
    client = AlpacaClient(
        api_key_id="ID", api_secret_key="SECRET", health_recorder=FakeHealthRecorder()
    )
    bar = {"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1000}
    route = respx.get(re.compile(r".*/v2/stocks/AAPL/bars")).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"bars": [{"t": "2026-09-16T04:00:00Z", **bar}], "next_page_token": "p2"},
            ),
            httpx.Response(200, json={"bars": [{"t": "2026-09-17T04:00:00Z", **bar}]}),
        ]
    )
    history = await client.get_price_history("AAPL")
    await client.aclose()

    first = route.calls[0].request.url.params
    assert "start" in first  # без start Alpaca отдаёт только текущий день
    assert route.calls[1].request.url.params["page_token"] == "p2"
    assert [b.date for b in history.bars] == [date(2026, 9, 16), date(2026, 9, 17)]
