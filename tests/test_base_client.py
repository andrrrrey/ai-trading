"""Контрактные тесты BaseSourceClient: retry/backoff, rate-limit, ошибки, health."""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.ingestion.base_client import (
    BaseSourceClient,
    RetryableSourceError,
    SourceError,
)
from tests.conftest import FakeHealthRecorder

URL = re.compile(r"https://api\.example\.com/data")


def make_client(recorder: FakeHealthRecorder, max_retries: int = 3) -> BaseSourceClient:
    return BaseSourceClient(
        source="example",
        base_url="https://api.example.com",
        timeout=5.0,
        max_retries=max_retries,
        health_recorder=recorder,
    )


@respx.mock
async def test_success_records_ok():
    recorder = FakeHealthRecorder()
    respx.get(URL).mock(return_value=httpx.Response(200, json={"value": 1}))
    client = make_client(recorder)
    result = await client._get_json("/data")
    await client.aclose()

    assert result == {"value": 1}
    assert recorder.statuses == ["ok"]


@respx.mock
async def test_retry_on_429_then_success():
    recorder = FakeHealthRecorder()
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = make_client(recorder)
    result = await client._get_json("/data")
    await client.aclose()

    assert result == {"ok": True}
    assert route.call_count == 2
    # первое обращение — error (429), второе — ok
    assert recorder.statuses == ["error", "ok"]


@respx.mock
async def test_retry_on_503_exhausted_raises_retryable():
    recorder = FakeHealthRecorder()
    respx.get(URL).mock(return_value=httpx.Response(503))
    client = make_client(recorder, max_retries=2)
    with pytest.raises(RetryableSourceError):
        await client._get_json("/data")
    await client.aclose()

    assert recorder.statuses == ["error", "error"]


@respx.mock
async def test_permanent_404_not_retried():
    recorder = FakeHealthRecorder()
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    client = make_client(recorder)
    with pytest.raises(SourceError) as exc:
        await client._get_json("/data")
    await client.aclose()

    assert exc.value.status_code == 404
    assert route.call_count == 1  # не ретраится


@respx.mock
async def test_invalid_json_raises_source_error():
    recorder = FakeHealthRecorder()
    respx.get(URL).mock(return_value=httpx.Response(200, text="<<not json>>"))
    client = make_client(recorder)
    with pytest.raises(SourceError):
        await client._get_json("/data")
    await client.aclose()

    assert recorder.statuses == ["error"]


@respx.mock
async def test_timeout_is_retried_and_recorded():
    recorder = FakeHealthRecorder()
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    client = make_client(recorder, max_retries=2)
    with pytest.raises(httpx.TimeoutException):
        await client._get_json("/data")
    await client.aclose()

    assert recorder.statuses == ["error", "error"]
