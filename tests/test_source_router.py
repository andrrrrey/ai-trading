"""Тесты SourceRouter — переключение основного источника на резерв (ТЗ 6.5).

Это ключевой DoD подэтапа 1.1: при отключённом/сломанном FMP цены берутся из
Alpaca, новости — из Finnhub, а полная недоступность даёт явную ошибку, а не
частичный/ложный результат.
"""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.finnhub_client import FinnhubClient
from app.ingestion.fmp_client import FMPClient
from app.ingestion.source_router import AllSourcesUnavailableError, SourceRouter
from tests.conftest import FakeHealthRecorder


def make_router() -> SourceRouter:
    rec = FakeHealthRecorder()
    return SourceRouter(
        fmp=FMPClient(api_key="K", max_retries=1, health_recorder=rec),
        alpaca=AlpacaClient(
            api_key_id="I", api_secret_key="S", max_retries=1, health_recorder=rec
        ),
        finnhub=FinnhubClient(api_key="T", max_retries=1, health_recorder=rec),
    )


@respx.mock
async def test_price_history_falls_back_to_alpaca_when_fmp_fails():
    router = make_router()
    respx.get(re.compile(r".*/stable/historical-price-eod/full")).mock(
        return_value=httpx.Response(503)  # FMP недоступен
    )
    respx.get(re.compile(r".*/v2/stocks/AAPL/bars")).mock(
        return_value=httpx.Response(
            200,
            json={"bars": [{"t": "2026-09-17T04:00:00Z", "o": 1, "h": 2,
                            "l": 0.5, "c": 1.5, "v": 10}]},
        )
    )
    history = await router.get_price_history("AAPL")
    await router.aclose()

    assert history.source == "alpaca"  # источник виден в данных
    assert len(history.bars) == 1


@respx.mock
async def test_price_history_uses_fmp_when_available():
    router = make_router()
    respx.get(re.compile(r".*/stable/historical-price-eod/full")).mock(
        return_value=httpx.Response(
            200,
            json=[{"date": "2026-09-17", "open": 1, "high": 2, "low": 0.5,
                   "close": 1.5, "volume": 10}],
        )
    )
    history = await router.get_price_history("AAPL")
    await router.aclose()

    assert history.source == "fmp"


@respx.mock
async def test_news_falls_back_to_finnhub():
    router = make_router()
    respx.get(re.compile(r".*/stable/news/stock")).mock(
        return_value=httpx.Response(503)
    )
    respx.get(re.compile(r".*/company-news")).mock(
        return_value=httpx.Response(
            200, json=[{"headline": "x", "datetime": 1758067200, "url": "u"}]
        )
    )
    news = await router.get_news("AAPL")
    await router.aclose()

    assert news and news[0].source == "finnhub"


@respx.mock
async def test_all_sources_unavailable_raises():
    router = make_router()
    respx.get(re.compile(r".*/stable/historical-price-eod/full")).mock(
        return_value=httpx.Response(503)
    )
    respx.get(re.compile(r".*/v2/stocks/AAPL/bars")).mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(AllSourcesUnavailableError):
        await router.get_price_history("AAPL")
    await router.aclose()
