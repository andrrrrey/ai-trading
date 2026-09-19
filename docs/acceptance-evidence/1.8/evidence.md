# Подэтап 1.8 — артефакт проверки DoD

Дата: 2026-09-19T05:23:27Z

## pytest — мониторинг источников
```

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
6 passed, 2 warnings in 0.47s
```

## pytest — весь набор
```
108 passed, 2 warnings in 7.41s
```

## ruff
```
All checks passed!
```

## Демонстрация /health: искусственный сбой FMP → восстановление
```
/usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
INFO:httpx:HTTP Request: GET http://testserver/health "HTTP/1.1 200 OK"
После сбоя FMP:
  sources_ok: False
  fmp: {"status": "error", "consecutive_errors": 3, "last_error": "HTTP 401 invalid api key", "last_success_at": "2026-09-19T05:23:36.799678Z"}
INFO:httpx:HTTP Request: GET http://testserver/health "HTTP/1.1 200 OK"
После восстановления FMP:
  sources_ok: True
  fmp: {"status": "ok", "consecutive_errors": 0}
```

## Статус DoD 1.8
- [x] SourceHealthMonitor реализует HealthRecorder — состояние обновляется при каждом обращении любого клиента.
- [x] /health отдаёт по каждому источнику: статус, last_success_at, latency, счётчик подряд ошибок, текст последней ошибки.
- [x] Искусственный сбой (невалидный ключ) виден в /health (status=error, consecutive_errors, last_error).
- [x] После восстановления статус обновляется автоматически (consecutive_errors → 0), без ручной правки истории.
- [x] source_health — append-only лента проверок (запись при наличии session_factory).
