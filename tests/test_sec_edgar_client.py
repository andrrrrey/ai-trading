"""Контрактные тесты SECEdgarClient (ТЗ 6.2)."""
from __future__ import annotations

import re
from datetime import date

import httpx
import pytest
import respx

from app.ingestion.base_client import SourceError
from app.ingestion.sec_edgar_client import SECEdgarClient
from tests.conftest import FakeHealthRecorder

USER_AGENT = "switch-trading-mvp test@example.com"


def make_client() -> SECEdgarClient:
    return SECEdgarClient(user_agent=USER_AGENT, health_recorder=FakeHealthRecorder())


def test_requires_valid_user_agent():
    with pytest.raises(ValueError):
        SECEdgarClient(user_agent="no-email-here")


@respx.mock
async def test_cik_map_and_filings():
    client = make_client()
    respx.get(re.compile(r".*/files/company_tickers\.json")).mock(
        return_value=httpx.Response(
            200,
            json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
        )
    )
    respx.get(re.compile(r".*/submissions/CIK0000320193\.json")).mock(
        return_value=httpx.Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-K", "4", "8-K"],
                        "filingDate": ["2026-08-01", "2026-07-15", "2026-06-01"],
                        "accessionNumber": ["a-1", "a-2", "a-3"],
                    }
                }
            },
        )
    )

    filings = await client.get_recent_filings("AAPL")
    await client.aclose()

    forms = [f.form for f in filings]
    assert forms == ["10-K", "8-K"]  # форма "4" отфильтрована
    assert filings[0].filed_date == date(2026, 8, 1)
    assert filings[0].source == "sec_edgar"


@respx.mock
async def test_unknown_ticker_raises():
    client = make_client()
    respx.get(re.compile(r".*/files/company_tickers\.json")).mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(SourceError):
        await client.get_recent_filings("ZZZZ")
    await client.aclose()
