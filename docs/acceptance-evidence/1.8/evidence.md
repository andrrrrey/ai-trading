# Подэтап 1.8 — Мониторинг источников и ошибок

- Сформировано: 2026-10-01 05:38 UTC (`scripts/collect_evidence.py`)
- Commit: `a339b9a` · версия формул: `v1.2`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- 1–4: /health — состояние, последний успех, latency по FMP, SEC, Alpaca, Finnhub
- 5: errors_24h, total_errors, last_error с классом причины
- 6, 8: принудительный сбой и восстановление без ручной правки истории
- 7: mode (primary/reserve/degraded/unavailable) и data_mode последнего расчёта

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_source_health.py tests/test_probe.py
.............                                                            [100%]
13 passed in 1.46s
[exit code 0]
```

## /health
```json
не выполнялось: запустить на сервере `python scripts/collect_evidence.py --live`
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] /health в норме
- [ ] /health при искусственном сбое (неверный FMP_API_KEY) и после восстановления
