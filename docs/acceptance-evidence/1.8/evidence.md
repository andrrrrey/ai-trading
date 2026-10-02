# Подэтап 1.8 — Мониторинг источников и ошибок

- Сформировано: 2026-10-01 20:58 UTC (`scripts/collect_evidence.py`)
- Commit: `793d52c` · версия формул: `v1.4`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- 1–4: /health — состояние, последний успех, latency по FMP, SEC, Alpaca, Finnhub
- 5: errors_24h, total_errors; текущая ошибка (last_error, класс) очищается при восстановлении, последний сбой — last_failure_at / last_failure_error
- 6, 8: принудительный сбой и восстановление без ручной правки истории
- 7: mode (primary/reserve/degraded/unavailable) и data_mode последнего расчёта
- устаревшая (> 30 мин) успешная проверка не выдаётся за ok: state=unknown, stale

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_source_health.py tests/test_probe.py
...............                                                          [100%]
15 passed in 1.12s
[exit code 0]
```

## /health
```json
не выполнялось: запустить на сервере `python scripts/collect_evidence.py --live`
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] /health в норме
- [ ] /health при искусственном сбое (неверный FMP_API_KEY) и после восстановления
