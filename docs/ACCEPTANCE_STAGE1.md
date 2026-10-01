# Отчёт о готовности к приёмке — Этап 1 (Data & Score Engine)

Проект: **MVP AI-системы для свитч-трейдинга**. Этап 1 по Договору: Data & Score
Engine, 95 000 ₽, 10 рабочих дней. Проверка ведётся по чек-листу заказчика
(подэтапы 1.1–1.10).

Дата обновления: 2026-10-01. Версия формул: **v1.3**.

---

## 1. Резюме

| Что | Статус |
|---|---|
| Функциональность подэтапов 1.1–1.10 в коде | ✅ реализована, все замечания ревью от 2026-10-01 закрыты (раздел 3) |
| Правило Catalysts и уровни неполноты данных | ⏳ ждут согласования Заказчиком: `docs/APPROVAL_STAGE1_DECISIONS.md` |
| Автотесты | ✅ 188, все проходят; ruff — без замечаний |
| Миграции на PostgreSQL | ✅ проверены локально на PostgreSQL 16 (alembic upgrade head, 10 таблиц) |
| Расчёт, история и replay на PostgreSQL | ✅ проверены локально (данные API подменены) |
| Backup / restore | ✅ скрипты проверены локально на PostgreSQL 16; ⏳ повторить на сервере с Docker |
| Развёртывание на сервере (Compose, healthcheck) | ⏳ не выполнено — нужен доступ к серверу |
| Живой прогон на реальных API по версии v1.3 | ⏳ выполнить на сервере (`scripts/collect_evidence.py --live`) |
| Скриншоты / запись демонстрации | ⏳ после серверного запуска |
| Повторная приёмка по чек-листу | ⏳ после серверного запуска |

Итоговые числа живого расчёта (Final Score и т. п.) в документах не фиксируются
вручную. Их формирует `scripts/collect_evidence.py` на конкретном commit.
Файлы в репозитории сгенерированы по commit с кодом (merge-commit в `main` его
содержит без изменений). Для сдачи их нужно пересобрать на сервере на
развёрнутом commit, вместе с живыми прогонами.
Предыдущий живой результат AAPL (Final Score 64, формула v1.0) устарел: в v1.1
и v1.3 изменились правило Catalysts и обработка неполных данных.

## 2. Решения, которые нужно согласовать с Заказчиком

Протокол согласования — `docs/APPROVAL_STAGE1_DECISIONS.md`. Предложения уже
реализованы в версии формул v1.3; все числа вынесены в `config/thresholds.yaml`,
поэтому правки Заказчика вносятся без изменения кода.

1. **Правило Catalysts v1.3** — базовое детерминированное правило Этапа 1:
   - SEC-события: негативные пункты 8-K и NT 10-K/10-Q дают −1, неоднозначные
     позитивные (1.01, 2.01) дают +0.5;
   - окно 30 дней с затуханием по давности;
   - SEC весит вдвое больше новостей.

   Новостная часть заменяется полноценной оценкой на Этапе 2.
2. **Неполные данные — два уровня** (ТЗ, раздел 8: «снижает confidence либо не
   формирует сигнал»):
   - критично — Final Score не выдаётся;
   - некритично — «достоверность: пониженная» с причинами.

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

## 4. Готовность по подэтапам чек-листа

| № | Подэтап | Код и автотесты | Требуется на сервере |
|---|---|---|---|
| 1.1 | Источники данных | ✅ | live smoke на реальных ключах, скриншот ответа бота |
| 1.2 | Получение и качество данных | ✅ | скриншоты сообщений об ошибках и неполных данных |
| 1.3 | Расчёт исходных метрик | ✅ | ручная сверка ≥3 метрик с TradingView (таблица в evidence 1.3) |
| 1.4 | Семь факторных оценок | ✅ | согласование правила Catalysts v1.3, скриншот «Подробнее» |
| 1.5 | Итоговый Score | ✅ | вывод `replay_signal.py` по сигналу из демонстрации |
| 1.6 | Базовый Risk Filter | ✅ | скриншот «Risk» |
| 1.7 | База данных и история | ✅ (PostgreSQL проверен локально) | миграции в Compose, `/history`, replay |
| 1.8 | Мониторинг источников и ошибок | ✅ | `/health` в норме, при сбое FMP и после восстановления |
| 1.9 | Базовый Telegram интерфейс | ✅ | скриншоты сценариев 1–9 |
| 1.10 | Сквозная контрольная проверка | ✅ | запись демонстрации, отключение API, протокол |

## 5. Порядок закрытия на сервере

1. Доступ по SSH, `.env` с обязательными параметрами (`docs/SETUP.md`).
2. `docker compose up -d --build` и `docker compose ps`: `app` и `bot` в
   состоянии healthy.
3. Два расчёта реального тикера в боте, `/history`, кнопки.
4. Сбор артефактов:
   `python scripts/collect_evidence.py --live --docker --ticker AAPL --signal-id <id>`.
5. Принудительный сбой FMP и восстановление (`docs/OPERATIONS.md`, раздел 3).
6. `scripts/backup.sh` и `scripts/restore.sh` на сервере, вывод — в артефакты.
7. Скриншоты в папки `docs/acceptance-evidence/<подэтап>/` по спискам из
   `evidence.md`.
8. Повторная приёмка по чек-листу.

## 6. Документы

- `docs/APPROVAL_STAGE1_DECISIONS.md` — протокол согласования решений v1.3.
- `docs/FORMULAS_AND_API.md` — формулы v1.3, источники, ошибки, `/health`, бот,
  конфигурация.
- `docs/OPERATIONS.md` — развёртывание, мониторинг, логи, резервное копирование
  и восстановление.
- `docs/SETUP.md` — ключи и `.env`.
- `docs/THIRD_PARTY_LICENSES.md` — сторонние компоненты и лицензии.
- `docs/acceptance-evidence/` — артефакты по подэтапам (генерируются).
