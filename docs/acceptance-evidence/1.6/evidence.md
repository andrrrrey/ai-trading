# Подэтап 1.6 — артефакт проверки DoD

Дата: 2026-09-19T05:10:07Z

## pytest — Risk Filter
```
..............                                                           [100%]
14 passed in 0.32s
```

## pytest — весь набор
```
........................................................................ [ 75%]
........................                                                 [100%]
96 passed in 6.64s
```

## ruff
```
All checks passed!
```

## Демонстрация флагов (причина + значение + допустимый статус)
```
[здоровый] level=low allowed_max=BUY
[высокая волатильность] level=medium allowed_max=WATCH
    - high_volatility: ATR/close = 6.2%, порог 5%
[перегрет (overextension)] level=medium allowed_max=BUY_ON_DIP
    - overextension: Отклонение от EMA20 18.0%, порог 15%
[низкая ликвидность] level=high allowed_max=WATCH
    - liquidity_risk: Средний объём 20д 300,000 < 500,000
[отчётность близко] level=medium allowed_max=BUY_ON_DIP
    - event_risk: Отчётность через 2 раб. дн. (порог 3)
[нет данных (объём)] level=high allowed_max=WATCH
    - missing_data: Нет ключевых данных: avg_volume_20d
[3 флага] level=high allowed_max=WATCH
    - high_volatility: ATR/close = 7.0%, порог 5%
    - gap_risk: Gap 8.0%, порог ±5%
    - overextension: Отклонение от EMA20 20.0%, порог 15%
```

## Статус DoD 1.6
- [x] 6 флагов: high_volatility, event_risk, gap_risk, overextension, liquidity_risk, missing_data (news_risk → Этап 2.1).
- [x] Каждый флаг возвращает причину и конкретное значение.
- [x] Каждый флаг проверен на граничных значениях (14 тестов).
- [x] missing_data всегда даёт risk_level=high; максимум WATCH.
- [x] risk_level: 0→low, 1–2→medium, >=3 или liquidity_risk→high.
- [x] allowed_max_status передаётся в Rule Engine (2.2) для понижения статуса — риск не игнорируется.
