# Перечень сторонних компонентов и лицензий

Документ подготовлен во исполнение п. 4.6 Договора: перечень всех сторонних
компонентов, библиотек, фреймворков, SDK и внешних сервисов, использованных в
Продукте, с указанием лицензии, версии и ссылки на условия.

Дата формирования: 2026-09-30. Версии соответствуют `requirements.lock` — именно
они устанавливаются при сборке Docker-образа (`pip install -c requirements.lock .`).

## 1. Итог проверки лицензий

- Компонентов под copyleft-лицензиями GPL / AGPL / LGPL **нет**.
- Все библиотеки распространяются под пермиссивными лицензиями (MIT, BSD, Apache-2.0,
  PSF, Zlib, CC0, MPL-2.0 — см. ниже).
- Единственное исключение из «чисто пермиссивных» — `certifi` (MPL-2.0, слабый
  файловый copyleft): набор корневых сертификатов используется без изменений,
  поэтому обязанности раскрывать исходный код Продукта не возникает.
- `anthropic` и `openai` — SDK для AI-объяснения (Этап 2); на Этапе 1 не вызываются.

## 2. Инфраструктурные компоненты

| Компонент | Версия | Лицензия | Назначение | Условия |
|---|---|---|---|---|
| Python (образ `python:3.12-slim`) | 3.12 | PSF-2.0 | среда выполнения | https://docs.python.org/3/license.html |
| PostgreSQL (образ `postgres:15`) | 15 | PostgreSQL License (пермиссивная) | база данных | https://www.postgresql.org/about/licence/ |
| Docker / Docker Compose | — | Apache-2.0 | развёртывание | https://github.com/docker/compose/blob/main/LICENSE |

## 3. Внешние сервисы (API)

Используются по условиям тарифов, которые выбирает и оплачивает Заказчик (п. 3.4
Договора, п. 4.2 ТЗ). Полные тексты материалов не перепубликуются; сохраняются
метаданные, заголовки, URL и собственные расчёты.

| Сервис | Роль | Условия использования |
|---|---|---|
| Financial Modeling Prep (FMP) | основной источник | https://site.financialmodelingprep.com/terms-of-service |
| SEC EDGAR | официальный источник filings | https://www.sec.gov/privacy#security (fair access: ≤10 запросов/с, обязателен User-Agent) |
| Alpaca Market Data | резерв цен | https://alpaca.markets/disclosures |
| Finnhub | резерв новостей | https://finnhub.io/terms-of-service |
| Telegram Bot API | интерфейс пользователя | https://telegram.org/tos/bot-developers |

## 4. Python-библиотеки

«Прямая» — указана в `pyproject.toml`; «транзитивная» — устанавливается как
зависимость прямой.

| Пакет | Версия | Лицензия | Тип | Ссылка |
|---|---|---|---|---|
| aiogram | 3.31.0 | MIT | прямая | https://aiogram.dev/ |
| alembic | 1.20.0 | MIT | прямая | https://alembic.sqlalchemy.org |
| anthropic | 1.10.0 | MIT License | прямая | https://github.com/anthropics/anthropic-sdk-python |
| apscheduler | 3.11.3 | MIT License | прямая | https://github.com/agronholm/apscheduler |
| asyncpg | 0.31.0 | Apache-2.0 | прямая | https://pypi.org/project/asyncpg/ |
| fastapi | 0.142.2 | MIT | прямая | https://github.com/fastapi/fastapi |
| httpx | 0.28.1 | BSD License | прямая | https://github.com/encode/httpx/blob/master/CHANGELOG.md |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | прямая | https://numpy.org |
| openai | 3.22.1 | Apache-2.0 | прямая | https://github.com/openai/openai-python |
| pandas | 3.0.6 | BSD License | прямая | https://pandas.pydata.org |
| pydantic | 2.13.5 | MIT | прямая | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.15.0 | MIT | прямая | https://github.com/pydantic/pydantic-settings |
| pyyaml | 6.0.3 | MIT License | прямая | https://pyyaml.org/ |
| sqlalchemy | 2.1.1 | MIT | прямая | https://www.sqlalchemy.org |
| tenacity | 9.1.4 | Apache Software License | прямая | https://github.com/jd/tenacity |
| uvicorn | 0.54.0 | BSD-3-Clause | прямая | https://uvicorn.dev/ |
| aiofiles | 25.1.0 | Apache Software License | транзитивная | https://github.com/Tinche/aiofiles |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License | транзитивная | https://github.com/aio-libs/aiohappyeyeballs |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT | транзитивная | https://github.com/aio-libs/aiohttp |
| aiosignal | 1.4.0 | Apache Software License | транзитивная | https://github.com/aio-libs/aiosignal |
| annotated-doc | 0.0.5 | MIT | транзитивная | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | транзитивная | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | транзитивная | https://github.com/agronholm/anyio |
| attrs | 26.1.0 | MIT | транзитивная | https://tidelift.com/subscription/pkg/pypi-attrs?utm_source=pypi-attrs&utm_medium=pypi |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | транзитивная | https://github.com/certifi/python-certifi |
| click | 8.5.0 | BSD-3-Clause | транзитивная | https://github.com/pallets/click/ |
| docstring_parser | 0.18.0 | MIT License | транзитивная | https://github.com/rr-/docstring_parser |
| frozenlist | 1.8.0 | Apache-2.0 | транзитивная | https://github.com/aio-libs/frozenlist |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | транзитивная | https://greenlet.readthedocs.io |
| h11 | 0.16.0 | MIT License | транзитивная | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | транзитивная | https://www.encode.io/httpcore |
| httpcore2 | 2.13.1 | BSD-3-Clause | транзитивная | https://github.com/pydantic/httpx2 |
| httptools | 0.8.0 | MIT | транзитивная | https://github.com/MagicStack/httptools |
| httpx2 | 2.13.1 | BSD-3-Clause | транзитивная | https://github.com/pydantic/httpx2 |
| idna | 3.20 | BSD-3-Clause | транзитивная | https://github.com/kjd/idna |
| jiter | 0.17.0 | MIT | транзитивная | https://github.com/pydantic/jiter/ |
| magic-filter | 1.0.12 | MIT | транзитивная | https://github.com/aiogram/magic-filter |
| mako | 1.4.3 | MIT | транзитивная | https://www.makotemplates.org/ |
| markupsafe | 3.0.3 | BSD-3-Clause | транзитивная | https://github.com/pallets/markupsafe/ |
| multidict | 6.9.1 | Apache License 2.0 | транзитивная | https://github.com/aio-libs/multidict |
| opentelemetry-api | 1.45.0 | Apache-2.0 | транзитивная | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| propcache | 0.5.4 | Apache-2.0 | транзитивная | https://github.com/aio-libs/propcache |
| pydantic_core | 2.46.5 | MIT | транзитивная | https://github.com/pydantic/pydantic |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License | транзитивная | https://github.com/dateutil/dateutil |
| python-dotenv | 1.2.3 | BSD-3-Clause | транзитивная | https://github.com/theskumar/python-dotenv |
| six | 1.17.0 | MIT License | транзитивная | https://github.com/benjaminp/six |
| sniffio | 1.3.1 | MIT License; Apache Software License | транзитивная | https://github.com/python-trio/sniffio |
| starlette | 1.7.0 | BSD-3-Clause | транзитивная | https://github.com/Kludex/starlette |
| truststore | 0.10.4 | MIT | транзитивная | https://github.com/sethmlarson/truststore |
| typing-inspection | 0.4.4 | MIT | транзитивная | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | транзитивная | https://github.com/python/typing_extensions |
| tzlocal | 5.4.4 | MIT | транзитивная | https://github.com/regebro/tzlocal |
| uvloop | 0.22.1 | Apache Software License; MIT License | транзитивная | https://pypi.org/project/uvloop/ |
| watchfiles | 1.3.0 | MIT License | транзитивная | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.1 | BSD-3-Clause | транзитивная | https://github.com/python-websockets/websockets |
| yarl | 1.25.1 | Apache-2.0 | транзитивная | https://github.com/aio-libs/yarl |

Библиотеки только для разработки и тестов (`pip install -e ".[dev]"`, в образ не
входят): pytest (MIT), pytest-asyncio (Apache-2.0), respx (BSD-3-Clause),
aiosqlite (MIT), ruff (MIT).

## 5. Как обновить перечень

```bash
python3.12 -m venv /tmp/rt && /tmp/rt/bin/pip install .
/tmp/rt/bin/pip freeze --exclude-editable | grep -v switch-trading > requirements.lock
```
После обновления `requirements.lock` таблицу из раздела 4 нужно пересобрать и
заново проверить на отсутствие GPL/AGPL.
