# Подэтап 1.6 — Базовый Risk Filter

- Сформировано: 2026-10-02 07:35 UTC (`scripts/collect_evidence.py`)
- Commit: `5cf7617` · версия формул: `v1.5`
- Живые прогоны: нет — выполнить на сервере с --live

## Что подтверждает (пункты чек-листа)
- 1–5: high_volatility, event_risk, gap_risk, liquidity_risk, overextension
- 6: missing_data — короткая история, нет fundamentals/earnings, SEC или новости недоступны; два уровня: критично — Score не выдаётся, некритично — «достоверность: пониженная»
- 7–8: причина и значение каждого флага сохраняются в signals.risk_flags
- 9: потолок статуса (allowed_max_status) сохраняется для Rule Engine Этапа 2

## Автотесты подэтапа
```
$ python -m pytest -q -p no:cacheprovider tests/test_risk_filter.py
................                                                         [100%]
16 passed in 0.30s
[exit code 0]
```

## Скриншоты / запись (добавить в эту папку после серверного запуска)
- [ ] кнопка «Risk» с причинами флагов
