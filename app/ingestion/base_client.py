"""Базовый HTTP-клиент источников данных (ТЗ раздел 6.5).

Все клиенты источников наследуют BaseSourceClient: общий httpx.AsyncClient с
таймаутом, retry с экспоненциальным backoff (учёт 429/Retry-After), единая
обработка ошибок и запись состояния источника через HealthRecorder.

Запись в таблицу source_health (ТЗ раздел 13) появляется на подэтапе 1.8 —
здесь определён абстрактный HealthRecorder, чтобы клиенты уже сообщали о
каждом обращении. По умолчанию используется LoggingHealthRecorder.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# HTTP-статусы, при которых имеет смысл повторить запрос.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


# Классы причин сбоя источника (ТЗ 5.1: timeout, API error, rate limit, no data).
ERROR_TIMEOUT = "timeout"
ERROR_NETWORK = "network"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_AUTH = "auth"  # 401/402/403: ключ недействителен или не хватает тарифа
ERROR_NOT_FOUND = "not_found"
ERROR_HTTP = "http_error"
ERROR_INVALID_RESPONSE = "invalid_response"
ERROR_NO_DATA = "no_data"


def _kind_for_status(status_code: int | None) -> str:
    if status_code == 429:
        return ERROR_RATE_LIMIT
    if status_code in (401, 402, 403):
        return ERROR_AUTH
    if status_code == 404:
        return ERROR_NOT_FOUND
    return ERROR_HTTP


class SourceError(Exception):
    """Базовая ошибка обращения к источнику данных.

    ``kind`` — класс причины (см. ERROR_*): по нему source_router решает про
    переключение на резерв, а бот формирует понятное пользователю сообщение.
    """

    def __init__(
        self,
        message: str,
        *,
        source: str,
        status_code: int | None = None,
        kind: str | None = None,
    ):
        super().__init__(message)
        self.source = source
        self.status_code = status_code
        self.kind = kind or (_kind_for_status(status_code) if status_code else ERROR_HTTP)


class RetryableSourceError(SourceError):
    """Временная ошибка источника — запрос будет повторён."""

    def __init__(
        self,
        message: str,
        *,
        source: str,
        status_code: int | None = None,
        retry_after: float | None = None,
        kind: str | None = None,
    ):
        super().__init__(message, source=source, status_code=status_code, kind=kind)
        self.retry_after = retry_after


class HealthRecord:
    """Результат одного обращения к источнику (для мониторинга, ТЗ 13)."""

    __slots__ = ("source", "status", "latency_ms", "error")

    def __init__(self, source: str, status: str, latency_ms: float, error: str | None):
        self.source = source
        self.status = status  # "ok" | "error"
        self.latency_ms = latency_ms
        self.error = error


class HealthRecorder(Protocol):
    """Приёмник событий мониторинга источников.

    Реализация с записью в БД (source_health) добавляется на подэтапе 1.8.
    """

    async def record(self, record: HealthRecord) -> None: ...


class LoggingHealthRecorder:
    """Дефолтный рекордер: пишет состояние источника в лог."""

    async def record(self, record: HealthRecord) -> None:
        if record.status == "ok":
            logger.info(
                "source=%s ok latency=%.0fms", record.source, record.latency_ms
            )
        else:
            logger.warning(
                "source=%s error latency=%.0fms: %s",
                record.source,
                record.latency_ms,
                record.error,
            )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Извлекает задержку из заголовка Retry-After (только числовой формат секунд)."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class BaseSourceClient:
    """Общий асинхронный клиент источника данных.

    Параметры:
        source: имя источника (fmp / sec_edgar / alpaca / finnhub) — пишется в
            source_health и в сохранённые данные.
        base_url: базовый URL API.
        timeout: тайм-аут запроса, сек.
        max_retries: число попыток (включая первую).
        default_headers: заголовки по умолчанию (напр. User-Agent для SEC).
        health_recorder: приёмник событий мониторинга.
    """

    source: str = "base"

    def __init__(
        self,
        *,
        source: str | None = None,
        base_url: str,
        timeout: float = 15.0,
        max_retries: int = 3,
        default_headers: dict[str, str] | None = None,
        default_params: dict[str, str] | None = None,
        health_recorder: HealthRecorder | None = None,
    ):
        if source is not None:
            self.source = source
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._default_params = default_params or {}
        self._health = health_recorder or LoggingHealthRecorder()
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers=default_headers or {},
        )

    async def __aenter__(self) -> BaseSourceClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _wait_strategy(self):
        """Экспоненциальный backoff, при этом Retry-After имеет приоритет."""
        exponential = wait_exponential(multiplier=1, min=1, max=30)

        def _wait(retry_state) -> float:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if isinstance(exc, RetryableSourceError) and exc.retry_after is not None:
                return exc.retry_after
            return exponential(retry_state)

        return _wait

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET с retry/backoff, разбором JSON и записью состояния источника.

        Бросает RetryableSourceError на временных сбоях (после исчерпания попыток
        она тоже долетает наружу — вызывающий source_router переключается на резерв)
        и SourceError на постоянных (4xx кроме 429, ошибка JSON). Таймаут и сетевой
        сбой после исчерпания попыток тоже превращаются в SourceError (kind
        timeout/network), чтобы router переключился на резерв, а бот показал
        понятную причину, а не «внутреннюю ошибку».
        """
        merged_params = {**self._default_params, **(params or {})}
        retryer = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=self._wait_strategy(),
            retry=retry_if_exception_type(
                (RetryableSourceError, httpx.TransportError, httpx.TimeoutException)
            ),
            reraise=True,
        )
        try:
            async for attempt in retryer:
                with attempt:
                    return await self._attempt_get_json(path, merged_params, headers)
        except httpx.TimeoutException as exc:
            raise SourceError(
                f"{self.source}: превышено время ожидания ответа",
                source=self.source,
                kind=ERROR_TIMEOUT,
            ) from exc
        except httpx.TransportError as exc:
            raise SourceError(
                f"{self.source}: источник недоступен (сетевая ошибка)",
                source=self.source,
                kind=ERROR_NETWORK,
            ) from exc

    async def _attempt_get_json(
        self,
        path: str,
        params: dict[str, Any],
        headers: dict[str, str] | None,
    ) -> Any:
        started = time.perf_counter()
        try:
            response = await self._client.get(path, params=params, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            kind = ERROR_TIMEOUT if isinstance(exc, httpx.TimeoutException) else ERROR_NETWORK
            await self._health.record(
                HealthRecord(self.source, "error", latency_ms, f"{kind}: {exc!r}")
            )
            raise

        latency_ms = (time.perf_counter() - started) * 1000

        if response.status_code in RETRYABLE_STATUS_CODES:
            retry_after = _parse_retry_after(response)
            await self._health.record(
                HealthRecord(
                    self.source,
                    "error",
                    latency_ms,
                    f"{_kind_for_status(response.status_code)}: HTTP {response.status_code}",
                )
            )
            raise RetryableSourceError(
                f"{self.source}: временная ошибка HTTP {response.status_code}",
                source=self.source,
                status_code=response.status_code,
                retry_after=retry_after,
            )

        if response.status_code >= 400:
            await self._health.record(
                HealthRecord(
                    self.source,
                    "error",
                    latency_ms,
                    f"{_kind_for_status(response.status_code)}: HTTP {response.status_code}",
                )
            )
            raise SourceError(
                f"{self.source}: ошибка HTTP {response.status_code}",
                source=self.source,
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            await self._health.record(
                HealthRecord(self.source, "error", latency_ms, f"{ERROR_INVALID_RESPONSE}: {exc}")
            )
            raise SourceError(
                f"{self.source}: некорректный JSON в ответе",
                source=self.source,
                kind=ERROR_INVALID_RESPONSE,
            ) from exc

        await self._health.record(HealthRecord(self.source, "ok", latency_ms, None))
        return payload
