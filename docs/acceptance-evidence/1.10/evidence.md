# Подэтап 1.10 — Сквозная проверка (Демонстрация Этапа 1)

Дата повторной проверки: 2026-09-30

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
133 passed in 6.38s
```

## ruff
```
All checks passed!
```

## Демонстрация полной цепочки (HTTP замокан, компоненты настоящие)
```
=== Ответ бота (Telegram) ===
📊 AAPL — Final Score: 62/100
Расчёт: <timestamp UTC> · формула v1.0
Источники: fmp, sec_edgar
Цена: $199.23

Momentum … · Growth … · Fundamentals … · Rel.Strength …
Volume … · Valuation … · Catalysts …

✅ Risk: без флагов

⚠️ Не является индивидуальной инвестиционной рекомендацией.

=== Backend (БД) ===
signal_id=1 | final_score=62 | status=BUY | version=v1.0
price_source=fmp | bars=1254 | incomplete=False
news_count=20 | filings_count=146 | sources=fmp,sec_edgar

Telegram render: True
Воспроизводимость (пересчёт из snapshot): 62 == 62 → True
```

## Статус DoD Этапа 1 (1.10)
- [x] Один тикер проходит всю цепочку: источник → обработка → метрики → 7 факторов → Final Score → Risk → БД → Telegram.
- [x] Сценарий отказа основного источника (FMP down) обрабатывается резервом (Alpaca) — test_primary_source_failover_to_reserve.
- [x] Полная недоступность → честное сообщение, частичный/ложный расчёт не сохраняется.
- [x] Оба прогона сохранены в истории (append-only).
- [x] Значения в backend и Telegram совпадают.
- [x] Воспроизводимость подтверждена повторным расчётом (идентичный Final Score).
- [x] Read-only прогон AAPL на реальных FMP и SEC API выполнен 2026-09-30;
  ключи в вывод и git не записывались.
- [ ] Скриншот ответа живого Telegram-бота и PostgreSQL/Compose-проверка будут
  добавлены после предоставления SSH-логина и установки временного публичного ключа.

## Исправленные дефекты повторной проверки

- News и SEC включены в основной pipeline; Catalysts сохраняет источники и evidence.
- Поля stable FMP (`netIncomePerShare`, `debtToEquityRatio`,
  `priceToEarningsRatio`) отображаются корректно.
- При отсутствии фактора Final Score не рассчитывается; скрытой ренормировки нет.
- Мониторинг источников читается из общей БД между процессами API и бота.
- Compose запускает миграции и бот; PostgreSQL не опубликован наружу.
- Telegram-ответ содержит timestamp, источники и версию формулы.
