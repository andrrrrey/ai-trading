# Подэтап 1.2 — артефакт проверки DoD

Дата: 2026-09-18T16:50:50Z

## pytest (нормализация, качество, сценарии ingestion)
```
....................................                                     [100%]
36 passed in 5.97s
```

## ruff
```
All checks passed!
```

## Контрольные сценарии DoD 1.2 (ТЗ раздел 6.6)
- [x] Некорректный ответ API → честное переключение на резерв / ошибка (test_malformed_response_falls_back_to_reserve, test_invalid_json_raises_source_error).
- [x] Timeout → retry, затем честная ошибка (test_timeout_is_retried_and_recorded).
- [x] Rate limit (429/Retry-After) → retry, затем нормализованный результат (test_rate_limit_retried_then_normalized).
- [x] Недоступный источник → fallback или AllSourcesUnavailableError, без выдуманного расчёта (test_source_unavailable_*, test_all_sources_unavailable_raises).
- [x] Отсутствующее значение → бар отброшен, null сохранён, is_incomplete; НИКОГДА не 0 (test_missing_value_*, test_fundamentals_incomplete_keeps_none).
- [x] Дедупликация по (ticker,date) + сортировка + нормализация даты в America/New_York (test_sorted_and_deduplicated, Alpaca NY-дата).
- [x] Единый period для fundamentals (test_get_fundamentals_uses_single_period).
