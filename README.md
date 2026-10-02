# switch-trading-mvp

MVP AI-системы для свитч-трейдинга: Telegram-бот анализирует акцию US-рынка по
тикеру — собирает данные, считает 7 факторных Score и Final Score (0–100),
применяет Risk Filter и сохраняет историю сигналов.

> Текущий статус: **Этап 1 (1.1–1.10) реализован и готов к серверной
> демонстрации**. Rule Engine и LLM-объяснение относятся к Этапу 2; до их
> реализации поле статуса является консервативным потолком Risk Filter.

## Архитектура (9 слоёв)

```
Telegram → FastAPI/orchestrator → Ingestion (FMP / SEC EDGAR / Alpaca / Finnhub)
  → Storage (PostgreSQL) → Feature Engine → Score Engine (7 → Final)
  → Risk Filter → Rule Engine → AI Explanation → история сигналов
```

Ключевой принцип: внешние API дают факты, все числа и статус считаются
программно и **воспроизводимо**; LLM только формулирует объяснение над готовыми
фактами (не влияет на Score и статус).

## Стек

Python 3.12 · FastAPI · asyncio · PostgreSQL 15 + SQLAlchemy 2.0 (async) +
Alembic · aiogram 3.x (long polling) · APScheduler · pandas/numpy · httpx +
tenacity · Docker/docker-compose.

## Структура проекта

```
app/
  config.py            # Pydantic Settings (.env)
  main.py              # FastAPI + /health
  ingestion/           # клиенты источников, fallback и контроль качества
  features/            # индикаторы и детерминированные Catalysts
  scoring/             # 7 Factor Scores и Final Score
  risk/                # Risk Filter
  calculation.py       # расчётное ядро (общее для живого расчёта и повтора)
config/thresholds.yaml # веса Score, пороги Risk/Rule Engine (v1)
scripts/               # smoke, replay, backup/restore, сборка артефактов приёмки
tests/                 # pytest
docs/APPROVAL_STAGE1_DECISIONS.md  # протокол согласования правил с Заказчиком
docs/FORMULAS_AND_API.md       # формулы, источники, ошибки, /health, бот
docs/OPERATIONS.md             # развёртывание, healthcheck, логи, backup/restore
docs/THIRD_PARTY_LICENSES.md   # сторонние компоненты и лицензии
docs/acceptance-evidence/      # артефакты приёмки по подэтапам
```

## Запуск

### Локально (тесты)

```bash
pip install -e ".[dev]"
pytest
```

### Docker

```bash
cp .env.example .env   # заполнить ключи (см. ниже)
docker compose up --build
# health-check: http://localhost:8000/health
```

Compose автоматически применяет миграции, запускает API и Telegram-бота. База
данных наружу не публикуется, API слушает только loopback сервера.

## Конфигурация

Все секреты и параметры — через `.env` (шаблон в `.env.example`). Бизнес-веса и
пороги — в `config/thresholds.yaml`, версионируются отдельно от кода.

Источники данных (ключи предоставляет заказчик, Stage 0):

| Источник | Роль | Авторизация |
|---|---|---|
| FMP | основной | `FMP_API_KEY` (query `apikey`) |
| SEC EDGAR | филинги | без ключа, обязателен `SEC_USER_AGENT` |
| Alpaca | резерв цен | `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` |
| Finnhub | резерв новостей | `FINNHUB_API_KEY` |

Резервные источники (Alpaca/Finnhub) подключаются автоматически, если заданы
ключи; иначе `source_router` работает только на основном источнике. Резерв
включается при любом сбое основного (таймаут, сеть, HTTP-ошибка); режим
(основной/резервный) виден в ответе бота и в `/health`.

Воспроизведение сохранённого расчёта: `python scripts/replay_signal.py <signal_id>`.
