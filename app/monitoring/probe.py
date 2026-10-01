"""Фоновая проверка доступности источников (ТЗ 5.1, чек-лист 1.8).

Без probe состояние источника обновлялось бы только при запросах пользователей,
и резервные Alpaca/Finnhub при нормальной работе FMP оставались бы «unknown».
Probe раз в ``HEALTH_PROBE_INTERVAL_SECONDS`` делает по одному лёгкому запросу к
каждому подключённому источнику. Результат пишется тем же HealthRecorder, что и
рабочие запросы (таблица source_health), поэтому /health показывает актуальные
статус, задержку, время последнего успеха и причину сбоя по каждому источнику.

Пустой ответ (no_data) тоже фиксируется как сбой с причиной «no_data».
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.ingestion.base_client import ERROR_NO_DATA, HealthRecord, HealthRecorder, SourceError
from app.ingestion.source_router import SourceRouter

logger = logging.getLogger(__name__)

PROBE_TICKER = "SPY"
PROBE_SEC_TICKER = "AAPL"  # у SPY (траст) нет обычных 10-K/10-Q/8-K


def _probes(router: SourceRouter) -> dict[str, Callable[[], Awaitable[object]]]:
    clients = router.configured_clients()
    probes: dict[str, Callable[[], Awaitable[object]]] = {}
    if "fmp" in clients:
        probes["fmp"] = lambda: clients["fmp"].get_quote(PROBE_TICKER)
    if "alpaca" in clients:
        probes["alpaca"] = lambda: clients["alpaca"].get_latest_quote(PROBE_TICKER)
    if "finnhub" in clients:
        probes["finnhub"] = lambda: clients["finnhub"].get_quote_price(PROBE_TICKER)
    if "sec_edgar" in clients:
        probes["sec_edgar"] = lambda: clients["sec_edgar"].get_recent_filings(PROBE_SEC_TICKER)
    return probes


async def probe_once(router: SourceRouter, recorder: HealthRecorder) -> dict[str, str]:
    """Один проход проверки; возвращает {источник: ok | класс ошибки}."""
    results: dict[str, str] = {}
    for name, probe in _probes(router).items():
        try:
            value = await probe()
        except SourceError as exc:
            if exc.kind == ERROR_NO_DATA:
                # HTTP-ответ был успешным и записан как ok — фиксируем отсутствие данных.
                await recorder.record(HealthRecord(name, "error", 0.0, f"no_data: {exc}"))
            results[name] = exc.kind
            continue
        except Exception as exc:  # noqa: BLE001 — probe не должен останавливаться
            logger.exception("probe %s: непредвиденная ошибка", name)
            await recorder.record(HealthRecord(name, "error", 0.0, f"http_error: {exc!r}"))
            results[name] = "http_error"
            continue
        if value in (None, [], {}):
            await recorder.record(
                HealthRecord(name, "error", 0.0, f"no_data: пустой ответ на {PROBE_TICKER}")
            )
            results[name] = ERROR_NO_DATA
        else:
            results[name] = "ok"
    logger.info("source probe: %s", results)
    return results


async def run_probe_loop(
    router: SourceRouter, recorder: HealthRecorder, interval_seconds: float
) -> None:
    """Бесконечный цикл probe (запускается в lifespan FastAPI)."""
    while True:
        try:
            await probe_once(router, recorder)
        except Exception:  # noqa: BLE001
            logger.exception("source probe: сбой прохода")
        await asyncio.sleep(interval_seconds)
