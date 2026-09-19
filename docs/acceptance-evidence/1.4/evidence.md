# Подэтап 1.4 — артефакт проверки DoD

Дата: 2026-09-19T04:57:15Z

## pytest — факторные Score
```
...............                                                          [100%]
15 passed in 0.44s
```

## pytest — весь набор
```
.......................................................................  [100%]
71 passed in 6.60s
```

## ruff
```
All checks passed!
```

## Демонстрация трассировки (метрики → правило → оценка) для тестового тикера
```
momentum           =   93 | правило: 30% RSI-скор (100.0) + 30% MACD-скор (75.2) + 40% EMA-структура (100.0)
growth             =   94 | правило: 0.5·clamp(50 + RevGrowth%·2.0) (94.0) + 0.5·clamp(50 + EPSGrowth%·1.5) (95.0)
fundamentals       =   75 | правило: avg(EPS-скор 70.0, GrossMargin-скор 75.0, D/E-скор 80.0 [обратная шкала])
relative_strength  =   85 | правило: clamp(50 + RelStrength%·5.0)
volume             =   70 | правило: clamp(50 + (VolumeRatio − 1)·50.0)
valuation          =   47 | правило: обратная шкала P/E в диапазоне [10.0, 40.0] (ниже P/E = выше балл)
catalysts          =   70 | правило: clamp(50 + sentiment·50.0), sentiment ∈ [-1, 1]
as_dict: {'momentum': 92.5706594885599, 'growth': 94.5, 'fundamentals': 75.0, 'relative_strength': 85.0, 'volume': 70.0, 'valuation': 46.666666666666664, 'catalysts': 70.0}
```

## Статус DoD 1.4
- [x] 7 факторных Score по формулам ТЗ раздела 8 (все конфигурируемо в thresholds.yaml).
- [x] По каждому фактору видна цепочка: входные метрики (inputs) → правило (rule) → оценка (score).
- [x] Веса Final Score = 20/15/15/15/10/10/15 (test_final_score_weights_match_kp).
- [x] Пропуск входных данных → score=None + is_incomplete (без выдуманных значений).
- [x] Детерминированность (воспроизводимость) подтверждена тестом.
