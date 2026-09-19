# Подэтап 1.5 — артефакт проверки DoD

Дата: 2026-09-19T05:04:24Z

## pytest — Final Score
```
...........                                                              [100%]
11 passed in 0.32s
```

## pytest — весь набор
```
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 6.58s
```

## ruff
```
All checks passed!
```

## Демонстрация: факторы → Final Score (тестовый тикер NVDA)
```
Округлённые факторы: {'momentum': 93, 'growth': 95, 'fundamentals': 75, 'relative_strength': 85, 'volume': 70, 'valuation': 47, 'catalysts': 70}
Final Score       : 79 /100
Версия формул     : v1.0
is_incomplete     : False
Правило           : взвешенная сумма факторов 0.20·momentum + 0.15·growth + 0.15·fundamentals + 0.15·relative_strength + 0.10·volume + 0.10·valuation + 0.15·catalysts
Повторный расчёт идентичен: True
```

## Статус DoD 1.5
- [x] Final Score = взвешенная сумма 7 факторов (веса из thresholds.yaml).
- [x] Округление ОДИН раз на выходе, «половина вверх» (не банковское) — test_round_half_up_not_bankers.
- [x] Final Score всегда в диапазоне 0–100 (clamp).
- [x] Воспроизводимость: повторный расчёт идентичен (test_deterministic + демо).
- [x] Тест чувствительности пройден (изменение фактора меняет Final Score).
- [x] formula_version записывается; active_formula_version даёт снимок весов/параметров (задел под БД 1.7).
- [x] Неполнота (нет фактора, напр. Catalysts в Этапе 1) → ренормировка весов + is_incomplete, без выдуманных значений.
