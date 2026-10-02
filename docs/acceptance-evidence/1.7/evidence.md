# Подэтап 1.7 — База данных и история

- Сформировано: 2026-10-02 07:35 UTC (`scripts/collect_evidence.py`)
- Commit: `5cf7617` · версия формул: `v1.5`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- профильные таблицы: tickers, price_history, fundamentals_snapshot, market_context (со ссылкой на signal_id), telegram_users
- 1–7: тикер, время, исходные данные (raw_inputs), метрики, 7 факторов, Final Score, флаги, источники и версия формул
- 8: два расчёта = две записи (append-only)
- 9: восстановление цепочки — scripts/replay_signal.py (параметры и пороги Risk Filter берутся из сохранённой версии формул)

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_db.py tests/test_migrations.py tests/test_e2e_stage1.py::test_profile_tables_are_filled tests/test_e2e_stage1.py::test_replay_uses_stored_risk_thresholds
........                                                                 [100%]
8 passed in 1.56s
[exit code 0]
```

## Воспроизведение сохранённого расчёта
```
не выполнялось: запустить на сервере `python scripts/collect_evidence.py --live --signal-id <id>`
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] /history по тикеру после двух расчётов
- [ ] вывод replay_signal.py
