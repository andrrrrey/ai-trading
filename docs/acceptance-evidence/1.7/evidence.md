# Подэтап 1.7 — артефакт проверки DoD

Дата: 2026-09-19T05:18:37Z

## pytest — БД и миграции
```
......                                                                   [100%]
6 passed in 0.89s
```

## pytest — весь набор
```
........................................................................ [ 70%]
..............................                                           [100%]
102 passed in 7.34s
```

## ruff
```
All checks passed!
```

## Демонстрация: два расчёта → две записи + восстановление цепочки
```
Записей в истории по NVDA: 2
Сигнал #2: Final=79 status=BUY version=v1.0
Факторы в БД: {'momentum': 93, 'growth': 95, 'valuation': 47}
Пересчёт из raw_input_snapshot: Final=79 → совпадает: True
```

## Статус DoD 1.7
- [x] Все таблицы раздела 5 (SQLAlchemy 2.0 async) + Alembic-миграция; схема применяется на чистой БД (test_migrations).
- [x] signals — append-only: два расчёта одного тикера дают две записи (test_two_calculations_create_two_rows).
- [x] Сохраняются: тикер, timestamp, price, raw_input_snapshot, 7 Factor Scores, Final Score, Risk Flags с причинами, formula_version.
- [x] По любой записи восстанавливается цепочка вход → Final Score, результат идентичен (test_chain_reconstruction_from_snapshot).
- [x] price_history — дедупликация по (ticker, date) через upsert.
- [x] formula_versions — ровно одна активная версия.
- [ ] ОТЛОЖЕНО до инфраструктуры: применение миграций к реальному PostgreSQL (docker/VPS).
