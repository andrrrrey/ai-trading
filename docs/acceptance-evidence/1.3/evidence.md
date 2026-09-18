# Подэтап 1.3 — артефакт проверки DoD

Дата: 2026-09-18T17:23:58Z

## pytest — индикаторы (Feature Engine)
```
....................                                                     [100%]
20 passed in 0.42s
```

## pytest — весь набор
```
........................................................                 [100%]
56 passed in 6.43s
```

## ruff
```
All checks passed!
```

## Покрытие формул ТЗ раздела 7
- EMA 20/50/200 — seed=SMA, k=2/(N+1) (test_ema_*).
- RSI 14 Wilder — рекуррента сверена вручную (test_rsi_wilder_hand_computed), границы 0/100.
- MACD 12/26/9 — константа→0, согласованность с EMA (test_macd_*).
- ATR 14 Wilder — TR и сглаживание сверены вручную (test_atr_wilder_hand_computed).
- Volume Ratio 20, price change 1/5/20, Relative Strength vs SPY 63d, Gap%, Distance to EMA.
- compute_features: полнота на фикстуре (260 баров), нехватка истории→None (не выдумано),
  идемпотентность (воспроизводимость) и чувствительность к входу.

## Статус DoD 1.3
- [x] Все формулы раздела 7 реализованы вручную (pandas/numpy, без pandas-ta).
- [x] Unit-тесты на эталонном ряде (±0.01) — 20 тестов, зелёные.
- [x] Тест чувствительности и идемпотентности пройдены.
- [x] Fundamentals с единым period обеспечены в 1.2 (get_fundamentals).
- [ ] ОТЛОЖЕНО до ключей (Stage 0): ручная сверка >=3 метрик с TradingView на реальном тикере.
