"""Контрактные тесты FMPClient на замоканных ответах (ТЗ 6.1)."""
from __future__ import annotations

import re
from datetime import date

import httpx
import respx

from app.ingestion.fmp_client import FMPClient
from tests.conftest import FakeHealthRecorder


def make_client() -> tuple[FMPClient, FakeHealthRecorder]:
    recorder = FakeHealthRecorder()
    client = FMPClient(
        api_key="TEST", base_url="https://financialmodelingprep.com",
        health_recorder=recorder,
    )
    return client, recorder


@respx.mock
async def test_get_quote():
    client, _ = make_client()
    respx.get(re.compile(r".*/stable/quote")).mock(
        return_value=httpx.Response(200, json=[{"symbol": "AAPL", "price": 231.5}])
    )
    quote = await client.get_quote("AAPL")
    await client.aclose()

    assert quote.ticker == "AAPL"
    assert quote.price == 231.5
    assert quote.source == "fmp"
    assert quote.fetched_at is not None


@respx.mock
async def test_get_price_history_maps_bars():
    client, _ = make_client()
    respx.get(re.compile(r".*/stable/historical-price-eod/full")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": "2026-09-17", "open": 1, "high": 2, "low": 0.5,
                 "close": 1.5, "volume": 1000},
                {"date": "2026-09-16", "open": 1, "high": 2, "low": 0.5,
                 "close": 1.4, "volume": 900},
                {"date": None, "open": 1, "high": 2, "low": 0.5,
                 "close": 1.4, "volume": 900},  # некорректная строка отбрасывается
            ],
        )
    )
    history = await client.get_price_history("AAPL")
    await client.aclose()

    assert len(history.bars) == 2
    assert history.bars[0].date == date(2026, 9, 17)
    assert history.bars[0].close == 1.5
    assert all(bar.source == "fmp" for bar in history.bars)


@respx.mock
async def test_get_fundamentals_uses_single_period():
    client, _ = make_client()
    ratios = respx.get(re.compile(r".*/stable/ratios")).mock(
        return_value=httpx.Response(
            200,
            json=[{"grossProfitMargin": 0.44, "debtEquityRatio": 1.2,
                   "priceEarningsRatio": 30.0, "eps": 6.1}],
        )
    )
    metrics = respx.get(re.compile(r".*/stable/key-metrics")).mock(
        return_value=httpx.Response(200, json=[{"eps": 6.1, "forwardPE": 25.0}])
    )
    growth = respx.get(re.compile(r".*/stable/income-statement-growth")).mock(
        return_value=httpx.Response(
            200, json=[{"growthRevenue": 0.08, "growthEPS": 0.12}]
        )
    )

    fundamentals = await client.get_fundamentals("AAPL", period="annual")
    await client.aclose()

    assert fundamentals.period == "annual"
    assert fundamentals.revenue_growth == 0.08
    assert fundamentals.eps_growth == 0.12
    assert fundamentals.gross_margin == 0.44
    assert fundamentals.debt_equity == 1.2
    assert fundamentals.pe == 30.0
    assert fundamentals.forward_pe == 25.0
    # все три запроса ушли с одним period=annual
    for route in (ratios, metrics, growth):
        assert route.calls.last.request.url.params["period"] == "annual"


@respx.mock
async def test_get_earnings_picks_next_future_date():
    client, _ = make_client()
    respx.get(re.compile(r".*/stable/earnings")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": "2020-01-01"},
                {"date": "2099-05-10"},
                {"date": "2099-02-01"},
            ],
        )
    )
    earnings = await client.get_earnings("AAPL")
    await client.aclose()

    assert earnings.next_earnings_date == date(2099, 2, 1)


@respx.mock
async def test_get_news_skips_untitled():
    client, _ = make_client()
    respx.get(re.compile(r".*/stable/news/stock")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"title": "Good quarter", "publishedDate": "2026-09-17T10:00:00Z",
                 "url": "http://x"},
                {"title": None},
            ],
        )
    )
    news = await client.get_news("AAPL")
    await client.aclose()

    assert len(news) == 1
    assert news[0].title == "Good quarter"
    assert news[0].source == "fmp"
