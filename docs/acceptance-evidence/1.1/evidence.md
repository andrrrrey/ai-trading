# Подэтап 1.1 — Источники данных

- Сформировано: 2026-10-01 20:58 UTC (`scripts/collect_evidence.py`)
- Commit: `793d52c` · версия формул: `v1.4`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- 1–3: FMP (котировка, история, fundamentals, earnings, profile, новости), SEC EDGAR (10-K/10-Q/8-K/NT) — live smoke ниже
- 4, 7: резерв Alpaca (цены с той же корректировкой на сплиты, что у FMP; котировка) / Finnhub (новости); общий режим расчёта primary / reserve / degraded / unavailable, SEC учитывается явно; переключение при таймауте, сетевой и HTTP-ошибке — тесты source_router и e2e
- 5–6: источник, режим (primary/reserve) и fetched_at каждого набора — raw_input_snapshot.sources

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_fmp_client.py tests/test_sec_edgar_client.py tests/test_alpaca_finnhub.py tests/test_source_router.py
..................                                                       [100%]
18 passed in 1.43s
[exit code 0]
```

## Live smoke (AAPL)
```
не выполнялось: запустить на сервере `python scripts/collect_evidence.py --live`
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] ответ бота по реальному тикеру со строками «Источники», «Режим данных», «Цена»
- [ ] кнопка «Подробнее» с SEC-событиями и новостями (источник, дата)
