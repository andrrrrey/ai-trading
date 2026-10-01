# Подэтап 1.7 — База данных и история

- Сформировано: 2026-10-01 04:29 UTC (`scripts/collect_evidence.py`)
- Commit: `2b95e21` · версия формул: `v1.1`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- 1–7: тикер, время, исходные данные (raw_inputs), метрики, 7 факторов, Final Score, флаги, источники и версия формул
- 8: два расчёта = две записи (append-only)
- 9: восстановление цепочки — scripts/replay_signal.py

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_db.py tests/test_migrations.py
......                                                                   [100%]
6 passed in 1.37s
[exit code 0]
```

## Воспроизведение сохранённого расчёта
```
не выполнялось: запустить на сервере `python scripts/collect_evidence.py --live --signal-id <id>`
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] /history по тикеру после двух расчётов
- [ ] вывод replay_signal.py
