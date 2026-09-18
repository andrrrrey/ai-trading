"""End-to-end сценарии слоя ingestion (DoD подэтапа 1.2).

Проверяются контрольные сценарии из ТЗ: некорректный ответ API, rate limit,
недоступный источник, отсутствующее значение — проходят без падения и без
ложного (выдуманного) расчёта.
"""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.ingestion.alpaca_client import AlpacaClient
from app.ingestion.base_client import SourceError
from app.ingestion.fmp_client import FMPClient
from app.ingestion.service import IngestionService
from app.ingestion.source_router import AllSourcesUnavailableError, SourceRouter
from tests.conftest import FakeHealthRecorder

FMP_HISTORY = re.compile(r".*/stable/historical-price-eod/full")
ALPACA_BARS = re.compile(r".*/v2/stocks/AAPL/bars")


def make_service(*, with_alpaca: bool, min_bars: int = 0) -> IngestionService:
    rec = FakeHealthRecorder()
    alpaca = (
        AlpacaClient(api_key_id="I", api_secret_key="S", max_retries=1,
                     health_recorder=rec)
        if with_alpaca
        else None
    )
    router = SourceRouter(
        fmp=FMPClient(api_key="K", max_retries=2, health_recorder=rec),
        alpaca=alpaca,
    )
    return IngestionService(router, min_history_bars=min_bars)


@respx.mock
async def test_malformed_response_falls_back_to_reserve():
    service = make_service(with_alpaca=True)
    respx.get(FMP_HISTORY).mock(return_value=httpx.Response(200, text="<<broken>>"))
    respx.get(ALPACA_BARS).mock(
        return_value=httpx.Response(
            200,
            json={"bars": [{"t": "2026-09-17T04:00:00Z", "o": 10, "h": 12,
                            "l": 9, "c": 11, "v": 1000}]},
        )
    )
    history, report = await service.get_price_history("AAPL")

    assert history.source == "alpaca"  # честно переключились на резерв
    assert len(history.bars) == 1
    assert report.dropped_invalid == 0


@respx.mock
async def test_missing_value_is_dropped_end_to_end():
    service = make_service(with_alpaca=False)
    respx.get(FMP_HISTORY).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": "2026-09-17", "open": 10, "high": 12, "low": 9,
                 "close": 11, "volume": 1000},
                {"date": "2026-09-16", "open": 10, "high": 12, "low": 9,
                 "close": None, "volume": 1000},  # пропуск close
            ],
        )
    )
    history, report = await service.get_price_history("AAPL")

    assert len(history.bars) == 1        # неполный бар отброшен
    assert report.dropped_invalid == 1
    assert report.total_rows == 2


@respx.mock
async def test_rate_limit_retried_then_normalized():
    service = make_service(with_alpaca=False)
    respx.get(FMP_HISTORY).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(
                200,
                json=[{"date": "2026-09-17", "open": 10, "high": 12, "low": 9,
                       "close": 11, "volume": 1000}],
            ),
        ]
    )
    history, _ = await service.get_price_history("AAPL")

    assert history.source == "fmp"
    assert len(history.bars) == 1


@respx.mock
async def test_source_unavailable_no_reserve_raises_honestly():
    service = make_service(with_alpaca=False)
    respx.get(FMP_HISTORY).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError):  # честная ошибка, а не пустой/выдуманный расчёт
        await service.get_price_history("AAPL")


@respx.mock
async def test_all_sources_unavailable_raises():
    service = make_service(with_alpaca=True)
    respx.get(FMP_HISTORY).mock(return_value=httpx.Response(503))
    respx.get(ALPACA_BARS).mock(return_value=httpx.Response(500))
    with pytest.raises(AllSourcesUnavailableError):
        await service.get_price_history("AAPL")


@respx.mock
async def test_market_data_flags_incomplete_fundamentals():
    service = make_service(with_alpaca=False)
    respx.get(FMP_HISTORY).mock(
        return_value=httpx.Response(
            200,
            json=[{"date": "2026-09-17", "open": 10, "high": 12, "low": 9,
                   "close": 11, "volume": 1000}],
        )
    )
    respx.get(re.compile(r".*/stable/ratios")).mock(
        return_value=httpx.Response(200, json=[{"priceEarningsRatio": 30.0}])
    )
    respx.get(re.compile(r".*/stable/key-metrics")).mock(
        return_value=httpx.Response(200, json=[{}])
    )
    respx.get(re.compile(r".*/stable/income-statement-growth")).mock(
        return_value=httpx.Response(200, json=[{}])
    )
    respx.get(re.compile(r".*/stable/earnings")).mock(
        return_value=httpx.Response(200, json=[{"date": "2099-01-01"}])
    )

    data = await service.get_market_data("AAPL")

    assert data.is_incomplete is True
    assert data.quality["fundamentals"].is_incomplete is True
    assert data.fundamentals.eps_growth is None  # пропуск сохранён
    assert len(data.price_history.bars) == 1
