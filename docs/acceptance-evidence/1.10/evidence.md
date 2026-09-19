# Подэтап 1.10 — Сквозная проверка (Демонстрация Этапа 1)

Дата: 2026-09-19T05:46:55Z

## pytest — сквозные сценарии Этапа 1
```
cachedir: .pytest_cache
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
tests/test_e2e_stage1.py::test_full_chain_and_reproducibility PASSED     [ 33%]
tests/test_e2e_stage1.py::test_primary_source_failover_to_reserve PASSED [ 66%]
tests/test_e2e_stage1.py::test_all_sources_down_honest_message PASSED    [100%]
```

## pytest — весь набор
```
127 passed, 2 warnings in 12.47s
```

## ruff
```
All checks passed!
```

## Демонстрация полной цепочки (HTTP замокан, компоненты настоящие)
```
=== Ответ бота (Telegram) ===
📊 AAPL — Final Score: 63/100
Цена: $199.23

Momentum 84 · Growth 76 · Fundamentals 53 · Rel.Strength 50
Volume 50 · Valuation 53 · Catalysts н/д

✅ Risk: без флагов

⚠️ Не является индивидуальной инвестиционной рекомендацией.

=== Backend (БД) ===
записей в истории: 2 | signal_id=2 | final_score=63 | status=BUY | version=v1.0
источник FMP: ok

Совпадение Telegram vs backend: 63 == 63 → True
Воспроизводимость (пересчёт из snapshot): 63 == 63 → True
```

## Статус DoD Этапа 1 (1.10)
- [x] Один тикер проходит всю цепочку: источник → обработка → метрики → 7 факторов → Final Score → Risk → БД → Telegram.
- [x] Сценарий отказа основного источника (FMP down) обрабатывается резервом (Alpaca) — test_primary_source_failover_to_reserve.
- [x] Полная недоступность → честное сообщение, частичный/ложный расчёт не сохраняется.
- [x] Оба прогона сохранены в истории (append-only).
- [x] Значения в backend и Telegram совпадают.
- [x] Воспроизводимость подтверждена повторным расчётом (идентичный Final Score).
- [ ] ОТЛОЖЕНО до Stage 0: прогон на реальном API-ключе и живом тикере (сейчас HTTP замокан через respx, компоненты настоящие).
