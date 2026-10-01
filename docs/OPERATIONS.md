# Эксплуатация: развёртывание, мониторинг, резервное копирование

Инструкция администратора (п. 10 ТЗ, п. 2.1.8 Договора). Ключи и порядок
заполнения `.env` описаны в `docs/SETUP.md`, формулы и API — в
`docs/FORMULAS_AND_API.md`.

## 1. Состав сервисов (`docker-compose.yml`)

| Сервис | Назначение | Healthcheck | Перезапуск |
|---|---|---|---|
| `postgres` | PostgreSQL 15, том `pgdata` | `pg_isready` | unless-stopped |
| `migrate` | `alembic upgrade head` перед стартом app и бота | — | одноразовый |
| `app` | FastAPI `/health` и фоновый probe источников; порт `127.0.0.1:8000` | `GET /health` раз в 30 с | unless-stopped |
| `bot` | Telegram-бот (long polling) | heartbeat-файл: обновляется раз в минуту, если Telegram API отвечает; контейнер unhealthy, если файл старше 3 минут | unless-stopped |

- База наружу не публикуется, API доступен только с самого сервера (или через
  SSH-туннель).
- Логи всех контейнеров ротируются: json-file, до 5 файлов по 10 МБ на контейнер.
- Образ собирается по `requirements.lock`. `.dockerignore` исключает из образа
  `.env`, `.git`, тесты, документацию и локальные артефакты, поэтому секреты
  передаются только через `env_file` во время работы.

## 2. Первое развёртывание

```bash
git clone https://github.com/andrrrrey/ai-trading.git && cd ai-trading
cp .env.example .env && chmod 600 .env
# заполнить ОБЯЗАТЕЛЬНЫЕ: FMP_API_KEY, SEC_USER_AGENT (реальный email),
# POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_IDS
docker compose up -d --build
docker compose ps                       # app и bot должны стать (healthy)
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Если обязательный параметр не задан, `app` и `bot` не стартуют. В
`docker compose logs app bot` выводится перечень ошибок конфигурации, например:

```
ConfigError: Конфигурация (bot) неполная, см. .env:
- TELEGRAM_ALLOWED_IDS пуст: укажите Telegram ID через запятую (или явно TELEGRAM_ALLOW_PUBLIC=true …)
```

Проверка цепочки на реальном тикере без бота (read-only, с временной SQLite):

```bash
docker compose exec app python scripts/stage1_acceptance_smoke.py AAPL
```

## 3. Мониторинг

| Что смотреть | Команда |
|---|---|
| Состояние контейнеров и healthcheck | `docker compose ps` |
| Источники данных, режим, ошибки | `curl -s http://127.0.0.1:8000/health` |
| Логи бота / API (с ротацией) | `docker compose logs --tail=200 -f bot` |
| История проверок источников | `docker compose exec postgres psql -U switch -d switch_trading -c "select source,status,last_error,checked_at from source_health order by id desc limit 20"` |

Поля `/health` описаны в `docs/FORMULAS_AND_API.md`, раздел 9. Ключевое:
`mode` (`primary` / `reserve` / `degraded` / `unavailable`) и `state` каждого
источника.

**Проверка принудительного сбоя** (чек-лист 1.8.6, 1.8.8):

1. Временно испортить `FMP_API_KEY` в `.env` и выполнить
   `docker compose up -d app bot`.
2. Через интервал probe (или после запроса тикера в боте) `/health` покажет
   `fmp.state = degraded/unavailable`, `last_error_kind = auth`.
3. Вернуть ключ и снова выполнить `docker compose up -d app bot`. После
   следующей проверки состояние станет `ok`; история сбоя останется в
   `source_health`.

## 4. Резервное копирование

Что копируется:

| Объект | Как |
|---|---|
| Исходный код | репозиторий GitHub (каждое изменение — commit) |
| База данных: история сигналов, исходные данные расчётов, версии формул, мониторинг | `scripts/backup.sh` → `backups/db_<UTC>.dump` (`pg_dump`, custom-формат) |
| Конфигурация: `.env` с секретами, `config/`, `docker-compose.yml` | `scripts/backup.sh` → `backups/config_<UTC>.tar.gz` (права 600) |

Запуск вручную из корня проекта:

```bash
scripts/backup.sh
# [backup] pg_dump switch_trading → ./backups/db_20261001T030000Z.dump
# [backup] готово: 52K ./backups/db_20261001T030000Z.dump
```

Скрипт:
- проверяет, что дамп читается (`pg_restore --list`); незавершённый дамп не
  сохраняется под итоговым именем;
- удаляет копии старше `BACKUP_RETENTION_DAYS` (по умолчанию 14 дней);
- каталог задаётся через `BACKUP_DIR` (по умолчанию `./backups`).

Ежедневно в 03:17 UTC (crontab пользователя, который управляет Docker):

```cron
17 3 * * * cd /opt/ai-trading && scripts/backup.sh >> backups/backup.log 2>&1
```

**Внешняя копия.** Копия, которая лежит только на том же сервере, не защищает
от потери сервера. Рекомендуется ежедневно отправлять `backups/` в хранилище
Заказчика (S3-совместимое хранилище, другой сервер), например через
`rclone sync backups/ remote:ai-trading-backups`. Хранилище выбирает и
предоставляет Заказчик.

## 5. Восстановление

```bash
scripts/restore.sh backups/db_20261001T030000Z.dump
# База switch_trading будет ЗАМЕНЕНА … Продолжить? [yes/N] yes
# [restore] остановка app и bot
# [restore] пересоздание базы switch_trading
# [restore] загрузка дампа
# [restore] signals: 128; схема alembic: 0001_initial
# [restore] запуск app и bot
```

Конфигурация восстанавливается так: `tar -xzf backups/config_<UTC>.tar.gz`
(перезапишет `.env`, `config/`, `docker-compose.yml`), затем
`docker compose up -d`.

После восстановления нужно проверить:
1. `curl -s http://127.0.0.1:8000/health` — `status: ok`;
2. `/history <тикер>` в боте показывает прежние расчёты;
3. `docker compose exec app python scripts/replay_signal.py <id>` →
   «РЕЗУЛЬТАТ: совпадает».

Процедура проверена 2026-10-01 на PostgreSQL 16 и локальных `pg_dump` /
`pg_restore` (переменная `PG_EXEC=` без Docker): backup → удаление всех
сигналов → restore → записи вернулись, `replay_signal.py` совпадает. На сервере
её нужно повторить с контейнерами и приложить вывод к артефактам приёмки.

## 6. Обновление и откат

```bash
scripts/backup.sh                      # всегда перед обновлением
git fetch && git checkout <tag|commit>
docker compose up -d --build           # migrate применит новые миграции
docker compose ps && curl -s http://127.0.0.1:8000/health
```

Откат: вернуться на предыдущий commit, выполнить `docker compose up -d --build`
и, если менялась схема БД, восстановить дамп, сделанный перед обновлением.

## 7. Артефакты приёмки

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/collect_evidence.py --live --docker --ticker AAPL --signal-id <id>
```

Скрипт пересобирает `docs/acceptance-evidence/<подэтап>/evidence.md`:
- commit и версию формул;
- вывод автотестов по подэтапу;
- live smoke, `/health` и воспроизведение расчёта.

Скриншоты Telegram и запись демонстрации кладутся в папки подэтапов; список
нужных снимков есть в каждом `evidence.md`.
