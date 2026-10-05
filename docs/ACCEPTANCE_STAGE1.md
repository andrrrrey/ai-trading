# Отчёт о готовности к приёмке — Этап 1 (Data & Score Engine)

Проект: **MVP AI-системы для свитч-трейдинга**. Этап 1 по Договору: Data & Score
Engine, 95 000 ₽, 10 рабочих дней. Проверка ведётся по чек-листу заказчика
(подэтапы 1.1–1.10).

Дата обновления: 2026-10-05. Версия формул: **v1.5.1** (корректирующий выпуск;
коэффициенты согласованной v1.5 не изменены).

---

## 1. Резюме

| Что | Статус |
|---|---|
| Функциональность подэтапов 1.1–1.10 в коде | ✅ реализована, все замечания ревью закрыты (разделы 3–3д) |
| Правила формул v1.5: Catalysts, уровни неполноты данных, уточнения v1.5 (D/E < 0, P/E TTM, актуальность истории тикера и SPY, `isActivelyTrading`, выравнивание с SPY по датам, отклонение ниже EMA20) | ✅ согласованы Заказчиком без изменений: `docs/APPROVAL_STAGE1_DECISIONS.md` (версия формул v1.5); подписанный экземпляр — приложить |
| Автотесты | ✅ 258, все проходят; ruff — без замечаний |
| Миграции на PostgreSQL | ✅ проверены локально на PostgreSQL 16: 0001 → 0002 → 0003 (индексы `source_health`), откат и повторный накат, заполнение всех профильных таблиц |
| Расчёт, история и replay на PostgreSQL | ✅ проверены локально (данные API подменены) |
| Backup / restore | ✅ скрипты проверены локально на PostgreSQL 16; ⏳ повторить на сервере с Docker |
| Развёртывание на сервере (Compose, healthcheck) | ⏳ не выполнено — нужен доступ к серверу |
| Готовность `app` с проверкой БД (`/ready`, healthcheck Compose) | ✅ проверено локально на PostgreSQL 16: 200 → остановка БД → 503 (`/health` по-прежнему 200) → запуск БД → 200 |
| Живой прогон на реальных API по версии v1.5 | ⏳ выполнить на сервере (`scripts/collect_evidence.py --live`) |
| Скриншоты / запись демонстрации | ⏳ после серверного запуска |
| Повторная приёмка по чек-листу | ⏳ после серверного запуска |

Итоговые числа живого расчёта (Final Score и т. п.) в документах не фиксируются
вручную. Их формирует `scripts/collect_evidence.py` на конкретном commit.
Файлы в репозитории сгенерированы по commit с кодом (merge-commit в `main` его
содержит без изменений). Для сдачи их нужно пересобрать на сервере на
развёрнутом commit, вместе с живыми прогонами.
Предыдущий живой результат AAPL (Final Score 64, формула v1.0) устарел: в v1.1
и v1.3 изменились правило Catalysts и обработка неполных данных, в v1.4 — сохранение порогов Risk Filter,
в v1.5 — правила раздела 3г. Живой прогон нужно выполнить заново на v1.5.

## 2. Решения, согласованные с Заказчиком

Протокол — `docs/APPROVAL_STAGE1_DECISIONS.md`: все три решения согласованы без
изменений, действующая версия согласованных коэффициентов — v1.5; исправления
обработки нейтрального SEC и неположительной базы EPS выпущены как v1.5.1. Все числа находятся в
`config/thresholds.yaml`; любое будущее изменение оформляется новой версией
формул (иначе сервисы не запустятся, см. раздел 3г).

1. **Правило Catalysts** (v1.3, без изменений в v1.4 и v1.5) — базовое детерминированное правило Этапа 1:
   - SEC-события: негативные пункты 8-K и NT 10-K/10-Q дают −1, неоднозначные
     позитивные (1.01, 2.01) дают +0.5;
   - окно 30 дней с затуханием по давности;
   - SEC весит вдвое больше новостей.

   Новостная часть заменяется полноценной оценкой на Этапе 2.
2. **Неполные данные — два уровня** (ТЗ, раздел 8: «снижает confidence либо не
   формирует сигнал»):
   - критично — Final Score не выдаётся;
   - некритично — «достоверность: пониженная» с причинами.
3. **Уточнения v1.5** — шесть правил, каждое включается параметром версии формул
   (расчёты v1.0–v1.4 воспроизводятся по своим правилам):
   - D/E < 0 (отрицательный капитал) → худший балл D/E (0 вместо 100);
   - Valuation: forward P/E → P/E TTM → годовой P/E;
   - последний бар тикера или SPY старше 3 рабочих дней → критично;
   - `isActivelyTrading = false` в профиле → критично;
   - Relative Strength — на общих датах тикера и SPY;
   - отклонение ниже EMA20 больше 15% → флаг `overextension`.

## 3. Исправления по ревью от 2026-10-01

| Замечание | Что сделано | Проверка |
|---|---|---|
| P1: неполные данные не влияют на расчёт (249 баров → обычный Score) | v1.2: оценка полноты по двум уровням. Критично (< 200 баров, нет SPY, ключевых метрик, ≥ 3 fundamentals, обоих новостных источников, любого фактора) — Final Score не выдаётся. Некритично (200–249 баров, 1–2 fundamentals, SEC или новости, дата отчётности) — «достоверность: пониженная». Причины — во флаге `missing_data`, оценка сохраняется и воспроизводится | `test_data_quality.py`, `test_short_history_reduces_confidence`, `test_very_short_history_blocks_final_score`, `test_missing_fundamentals_levels` |
| P1: SEC filings не влияют на Catalysts | правило v1.2: 8-K по пунктам (негатив −1, неоднозначный позитив +0.5), NT 10-K/10-Q, вес по давности, приоритет SEC над СМИ; пункты 8-K берутся из SEC submissions | `test_catalysts.py`, `test_sec_negative_8k_lowers_catalysts` |
| P1: ошибка SEC скрывается | `filings_available` и `news_available` в расчёте и истории; причина («SEC EDGAR недоступен (sec_edgar: timeout)») — во флаге `missing_data` | `test_sec_unavailable_is_flagged` |
| P1: нет current quote и corporate profile | `/stable/quote` (резерв — Alpaca latest trade) и `/stable/profile` подключены; в Telegram: котировка с временем ET и отдельно закрытие EOD с датой (индикаторы считаются по EOD); устаревшая котировка помечается; `signals.price` = котировка | `test_full_chain_and_reproducibility` |
| P2: Telegram HTML не экранируется | `html.escape` для всего внешнего текста: заголовки, источники, причины флагов («<» в ликвидности), профиль | `test_external_text_is_html_escaped` |
| P2: мониторинг не автономный | фоновый probe всех источников в процессе `app`; состояния ok / degraded / unavailable / unknown, возраст, `errors_24h`, класс причины (в т. ч. `no_data`), общий `mode`; агрегация SQL-запросами | `test_probe.py`, `test_source_health.py`, проверено через uvicorn на PostgreSQL |
| P2: нет backup/restore | `scripts/backup.sh`, `scripts/restore.sh`, ротация 14 дней, cron и внешняя копия — `docs/OPERATIONS.md` | backup → удаление → restore → replay совпадает (PostgreSQL 16) |
| P2: deployment hardening | `.dockerignore`; healthcheck для app (`/health`) и бота (heartbeat через Telegram API); ротация логов 5×10 МБ; обязательные `FMP_API_KEY`, `SEC_USER_AGENT`, `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` проверяются при старте | `test_config.py`, `test_api_refuses_to_start_without_required_config` |
| Документы противоречат коду | артефакты `docs/acceptance-evidence` генерируются из кода (`scripts/collect_evidence.py`): commit, версия формул, тесты; живые разделы — на сервере | — |

## 3а. Исправления по повторному ревью от 2026-10-01 (версия формул v1.3)

| Замечание | Что сделано | Проверка |
|---|---|---|
| P1: новости не ограничены по возрасту (новость 2020 г. → Catalysts 100) | окно актуальности новостей 14 дней (`news_lookback_days`), вес `1 − возраст/окно`; будущие и недатированные отбрасываются; число устаревших, будущих и недатированных сохраняется | `test_old_positive_news_does_not_create_catalyst` (сценарий ревью → signal 0, Catalysts 50), `test_future_and_undated_news_are_excluded_and_counted`, `test_news_weight_decays_with_age` |
| P1: режим расчёта `primary` при недоступных новостях, котировке, профиле | `aggregate_mode`: нет критичного набора → `unavailable`; нет некритичного (дата отчётности, новости, SEC, котировка, профиль; `no_data` тоже) → `degraded`; есть резерв → `reserve`; иначе `primary`. SEC и новости — отдельные наборы со своим режимом. Telegram: «ЧАСТИЧНЫЙ (недоступно: …)» | `test_aggregate_mode_priority` (комбинация ревью → `degraded`), `test_missing_noncritical_sources_mark_mode_degraded`, `test_sec_down_marks_mode_degraded` |
| P2: после восстановления `/health` показывает старую ошибку | при успешной проверке `last_error` и `last_error_kind` очищаются; последний сбой хранится в `last_failure_at` / `last_failure_error` (в памяти и в БД) | `test_recovery_clears_current_error_but_keeps_last_failure` |
| P2: Alpaca `adjustment=raw` несовместима с FMP | `adjustment=split`, как у FMP `historical-price-eod/full` | `test_alpaca_fallback_uses_split_adjusted_prices`: искусственный сплит 4:1. С `split` отклонение от EMA50 1.7%, с `raw` было бы −45.9% |
| P2: конфликт webhook и polling | перед `start_polling` вызывается `delete_webhook(drop_pending_updates=False)` | `test_webhook_is_removed_before_polling` |
| Evidence 1.10: «блокирующих дефектов нет» без live-проверки | формулировка заменена: пункт подтверждается только на live-демонстрации | — |

## 3б. Исправления по ревью aae5c40 (версия формул v1.4)

| Замечание | Что сделано | Проверка |
|---|---|---|
| P1: Risk Filter не воспроизводится по исторической версии | пороги Risk Filter — часть версии формул: сохраняются в `formula_config.risk_filter` и `formula_versions`, передаются в `compute_risk`; при повторе берутся сохранённые | `test_replay_uses_stored_risk_thresholds`: после ужесточения текущего порога ATR повтор старого сигнала даёт те же флаги |
| P1: неизвестная дата отчётности считалась доступной | «доступна» = известна будущая дата; API без будущей даты → `no_data` и причина «fmp не вернул будущую дату отчётности»; доступность API — отдельно (`api_available`); достоверность пониженная | `test_unknown_earnings_date_reduces_confidence` |
| P1: нулевые fundamentals превращались в `None` | `_first_number` вместо `or`: 0 сохраняется, пропуск — только `null`, пустая строка или нечисловое значение | `test_zero_fundamentals_are_data_not_missing`, `test_missing_and_empty_values_fall_back_to_alternative_fields` |
| P2: устаревшая успешная проверка оставалась `ok` | проверка старше 30 мин → `state: unknown`, `stale: true` | `test_stale_success_is_not_reported_as_ok` |
| P2: фиктивный источник `news` / `filings` при пустом ответе | роутер возвращает ответивший источник и время ответа; в `sources` — `source: fmp` / `sec_edgar`, `fetched_at`, `mode: no_data` | `test_empty_feeds_keep_real_source_and_time` |
| Профильные таблицы БД пустые | каждый расчёт заполняет `tickers`, `price_history` (тикер и SPY, upsert одним запросом), `fundamentals_snapshot` и `market_context` (со ссылкой на `signal_id`, миграция 0002); бот ведёт `telegram_users` | `test_profile_tables_are_filled`; проверено на PostgreSQL 16 |
| Найдено попутно: `telegram_users.chat_id` типа int4, а Telegram ID бывают больше 2³¹ | BigInteger (миграция 0002) | тест с ID 7 000 000 001; PostgreSQL |
| README ссылался на протокол v1.2 | исправлено | — |

## 3в. Исправления по ревью 24293a0 (версия формул без изменений — v1.4)

| Замечание | Что сделано | Проверка |
|---|---|---|
| Высокий: сохранение расчёта неатомарно | версия формул, сигнал, `factor_scores`, `tickers`, `price_history`, `fundamentals_snapshot`, `market_context` пишутся в **одной транзакции**. При любой ошибке — откат всего, бот сообщает «расчёт выполнен, но не сохранён в базу» и результат не выдаёт, чтобы ответ не расходился с историей | `test_signal_and_context_are_saved_atomically`: сбой после записи контекста → в БД нет ни сигнала, ни строк контекста |
| Средний: частичный сбой fundamentals останавливал расчёт | три запроса FMP (ratios, key-metrics, income-statement-growth) выполняются независимо и параллельно; недоступные запросы сохраняются в `fundamentals.unavailable_parts` и применяется согласованное правило полноты. Отказ — только если не ответил ни один из трёх | `test_fundamentals_requests_are_independent`, `test_partial_fundamentals_failure_follows_quality_rule` |
| Низкий: рабочие дни без биржевых праздников | по согласованной v1.4 оставлено «будни пн–пт»; правило явно описано в конфиге, коде и `FORMULAS_AND_API.md` | — |
| Документы: согласование не отражено | протокол отмечен «согласовано без изменений» (v1.4), раздел 2 и резюме обновлены | — |

Нюанс правила полноты (согласован в v1.4, без изменений в v1.5): при сбое `income-statement-growth`
пропадают оба показателя роста. Сами по себе 2 пропуска дают «пониженную
достоверность», но без них не рассчитывается фактор Growth, а «любой фактор не
рассчитан» — критично. Поэтому в этом случае Final Score не выдаётся, а причины
показываются полностью. Сбой `key-metrics` (теряется только forward P/E) даёт
Score с пониженной достоверностью.

## 3г. Исправления по ревью Этапа 1 от 2026-10-02 (версия формул v1.5)

| Замечание | Что сделано | Проверка |
|---|---|---|
| Ключ FMP попадал в логи (httpx на INFO пишет URL с `apikey`) | логгеры `httpx` / `httpcore` — WARNING | `test_api_key_is_not_written_to_logs` |
| `Retry-After: 3600` подвешивал бот | ожидание ограничено 30 с | `test_retry_after_wait_is_capped` |
| Номер версии не привязан к параметрам: правка `thresholds.yaml` без смены `version` молча перезаписывала `formula_versions` | та же версия с другими параметрами отклоняется, API и бот не запускаются с понятной ошибкой | `test_same_formula_version_with_other_params_is_rejected`; проверено на PostgreSQL (сравнение с JSONB) |
| Пороги были «зашиты» в образ | `./config` подключён в `app` и `bot`; после правки — `docker compose restart app bot` | `test_config_is_mounted_into_app_and_bot` |
| `source_health` рос без ограничений | очистка старше `SOURCE_HEALTH_RETENTION_DAYS` (30 дней) в фоновом probe; индексы (миграция 0003) | `test_source_health_purge_removes_only_old_rows`, `test_probe_loop_purges_old_health_rows`, `test_migration_creates_source_health_indexes` |
| D/E < 0 давал лучший балл | правило 3.1 (v1.5) | `test_negative_equity_gets_worst_debt_score` |
| Relative Strength сравнивал разные окна | правило 3.5 (v1.5) | `test_relative_strength_is_aligned_by_date`, `test_relative_strength_uses_alignment_from_formula_version` |
| Устаревшая история / бумага не торгуется → «высокая достоверность» | правила 3.3–3.4 (v1.5) | `test_stale_history_blocks_final_score`, `test_not_actively_trading_is_critical`, `test_actively_trading_is_replayed_from_stored_profile` |
| Годовой P/E отстаёт на год | правило 3.2 (v1.5); сбой `ratios-ttm` не делает fundamentals неполными | `test_valuation_prefers_ttm_pe_over_annual`, `test_ratios_ttm_failure_does_not_make_fundamentals_incomplete` |
| Отклонение вниз от EMA20 не проверялось | правило 3.6 (v1.5) | `test_strong_deviation_below_ema20_is_flagged` |
| Незавершённый бар сессии искажал Volume Ratio | до 16:30 ET бар текущего дня исключается, пользователь видит предупреждение | `test_unfinished_session_bar_is_dropped`, `test_pipeline_excludes_unfinished_bar_and_says_so` |
| Alpaca free (IEX) — объёмы одной биржи | `ALPACA_DATA_FEED`; на IEX порог ликвидности не проверяется, достоверность пониженная | `test_alpaca_iex_feed_marks_partial_volume`, `test_partial_volume_skips_liquidity_flag_and_reduces_confidence` |
| FMP HTTP 200 с `Error Message` → «тикер не найден» | распознаётся как ошибка API (`rate_limit` / `auth` / `invalid_response`) | `test_fmp_error_in_200_body_is_an_api_error` |
| Дата отчётности по UTC; Finnhub — 7 дней новостей | «сегодня» по Нью-Йорку; окно Finnhub 14 дней | `test_next_earnings_date_uses_new_york_today`, `test_finnhub_reserve_news_window_matches_formula` |
| Тикеры классов акций не принимались | `BRK.B` / `BRK-B` / `BF/B`; нотация FMP и SEC — `BRK-B` | `test_share_class_tickers_are_parsed`, `test_sec_finds_cik_for_share_class` |
| Нет индикации расчёта, возможен двойной запуск (п. 1.9.1) | «⏳ Считаю …»; один расчёт на чат и тикер | `test_same_ticker_in_same_chat_runs_once`, `test_ticker_handler_shows_progress_and_splits_long_result` |
| Сообщение длиннее 4096 символов не отправлялось | отправка частями | `test_split_message_respects_telegram_limit` |
| Найдено попутно: `TELEGRAM_ALLOWED_IDS` из `.env` в формате «через запятую», одним ID или пустым ронял запуск | значение разбирается как строка | `test_allowed_ids_from_environment`, `test_allowed_ids_from_env_file` |

## 3д. Замечания приёмки к v1.5 от 2026-10-02

| Замечание | Что сделано | Проверка |
|---|---|---|
| Блокирующее: документы на v1.4, код на v1.5 | протокол согласования оформлен на v1.5 (решение 3 — уточнения 3.1–3.6), отчёт обновлён: версия, число тестов, правила v1.5, живой прогон v1.5 | — |
| Не проверялась актуальность SPY | тот же порог `max_last_bar_age_business_days` применяется к бенчмарку; устаревший SPY → критично, Final Score не выдаётся (часть правила 3.3) | `test_stale_benchmark_blocks_final_score`, `test_stale_benchmark_in_full_calculation`, `test_fresh_benchmark_and_legacy_rule_are_unaffected` |
| Нарезка длинной строки могла разрезать тег или `&amp;` | строка делится по токенам HTML: теги и сущности не разрываются, открытые теги закрываются и открываются снова, текст режется по пробелам | `test_long_line_does_not_split_entities`, `test_long_line_reopens_tags_across_chunks`, `test_random_markup_is_split_safely` (баланс тегов, целостность сущностей и текста) |
| Healthcheck `app` проверял только liveness | `GET /ready` — `SELECT 1` в PostgreSQL, HTTP 503 при недоступности; healthcheck Compose переведён на `/ready`; `/health` остаётся liveness | `test_ready_ok_when_database_answers`, `test_ready_503_when_database_is_down`, `test_compose_healthcheck_uses_ready`; на PostgreSQL 16: 200 → 503 → 200 |

## 4. Готовность по подэтапам чек-листа

| № | Подэтап | Код и автотесты | Требуется на сервере |
|---|---|---|---|
| 1.1 | Источники данных | ✅ | live smoke на реальных ключах, скриншот ответа бота |
| 1.2 | Получение и качество данных | ✅ | скриншоты сообщений об ошибках и неполных данных |
| 1.3 | Расчёт исходных метрик | ✅ | ручная сверка ≥3 метрик с TradingView (таблица в evidence 1.3) |
| 1.4 | Семь факторных оценок | ✅ | скриншот «Подробнее» (правило Catalysts согласовано) |
| 1.5 | Итоговый Score | ✅ | вывод `replay_signal.py` по сигналу из демонстрации |
| 1.6 | Базовый Risk Filter | ✅ | скриншот «Risk» |
| 1.7 | База данных и история | ✅ (PostgreSQL проверен локально) | миграции в Compose, `/history`, replay |
| 1.8 | Мониторинг источников и ошибок | ✅ | `/health` в норме, при сбое FMP и после восстановления |
| 1.9 | Базовый Telegram интерфейс | ✅ | скриншоты сценариев 1–9 |
| 1.10 | Сквозная контрольная проверка | ✅ | запись демонстрации, отключение API, протокол |

## 5. Порядок закрытия на сервере

1. Доступ по SSH, `.env` с обязательными параметрами (`docs/SETUP.md`).
2. `docker compose up -d --build` и `docker compose ps`: `app` и `bot` в
   состоянии healthy (`app` — по `/ready`, т. е. с работающей связью с БД).
3. Два расчёта реального тикера в боте, `/history`, кнопки.
4. Сбор артефактов:
   `python scripts/collect_evidence.py --live --docker --ticker AAPL --signal-id <id>`.
5. Принудительный сбой FMP и восстановление (`docs/OPERATIONS.md`, раздел 3).
6. `scripts/backup.sh` и `scripts/restore.sh` на сервере, вывод — в артефакты.
7. Скриншоты в папки `docs/acceptance-evidence/<подэтап>/` по спискам из
   `evidence.md`.
8. Приложить подписанный протокол согласования версии формул v1.5
   (`docs/APPROVAL_STAGE1_DECISIONS.md`).
9. Повторная приёмка по чек-листу.

## 6. Документы

- `docs/APPROVAL_STAGE1_DECISIONS.md` — протокол согласования решений (v1.5).
- `docs/FORMULAS_AND_API.md` — формулы v1.5, источники, ошибки, `/health`,
  `/ready`, бот, конфигурация.
- `docs/OPERATIONS.md` — развёртывание, мониторинг, логи, резервное копирование
  и восстановление.
- `docs/SETUP.md` — ключи и `.env`.
- `docs/THIRD_PARTY_LICENSES.md` — сторонние компоненты и лицензии.
- `docs/acceptance-evidence/` — артефакты по подэтапам (генерируются).
