# Описание формул, API и конфигурации — Этап 1

Документ передаётся в составе результата Этапа 1 (п. 14.2 ТЗ: «описание API и
формул»). Версия формул: **v1.0** (`config/thresholds.yaml`). Все числа считаются
программно и детерминированно; LLM в расчётах не участвует.

Содержание:
1. Цепочка расчёта
2. Источники данных, режимы и обработка ошибок
3. Качество данных
4. Feature Engine — исходные метрики
5. Семь факторных оценок
6. Final Score
7. Risk Filter
8. История и воспроизведение
9. Служебный API `/health`
10. Telegram-бот
11. Конфигурация

---

## 1. Цепочка расчёта

```
Тикер → Ingestion (FMP / SEC EDGAR, резерв Alpaca / Finnhub)
      → нормализация и контроль качества
      → Feature Engine (RSI, MACD, EMA, ATR, Volume Ratio, Rel.Strength …)
      → 7 Factor Scores → Final Score 0–100
      → Risk Filter (флаги + причины)
      → запись в signals (append-only) → Telegram
```

Код: `app/pipeline.py` (оркестрация), `app/calculation.py` (расчётное ядро). Одна и
та же функция `calculate()` используется и для живого расчёта, и для повтора по
записи истории, поэтому повтор идёт строго по тому же коду.

## 2. Источники данных, режимы и обработка ошибок

| Данные | Основной | Резерв | Когда включается резерв |
|---|---|---|---|
| История цен OHLCV (тикер и SPY) | FMP `/stable/historical-price-eod/full` | Alpaca `/v2/stocks/{t}/bars` (≈2 года дневных баров) | любой сбой FMP: таймаут, сетевая ошибка, HTTP 429/5xx/4xx, некорректный ответ |
| Fundamentals | FMP `ratios`, `key-metrics`, `income-statement-growth` | — | нет резерва: при сбое расчёт останавливается с понятным сообщением |
| Дата отчётности | FMP `/stable/earnings` | — | при сбое расчёт продолжается, ставится флаг `missing_data` |
| Новости | FMP `/stable/news/stock` | Finnhub `/company-news` | любой сбой FMP |
| Filings 10-K / 10-Q / 8-K | SEC EDGAR `/submissions/CIK….json` | — | при сбое новости/filings = пусто, это видно в «Подробнее» |

Резервные клиенты подключаются, только если в `.env` заданы их ключи.

**Режим источника.** В каждом расчёте для каждого вида данных сохраняются
`source`, `mode` (`primary` / `reserve` / `unavailable`) и `fetched_at`. Если хотя
бы часть данных пришла из резерва, общий режим расчёта — `reserve`. В Telegram он
показывается строкой «⚠️ Режим данных: РЕЗЕРВНЫЙ (цены — alpaca)», в `/health` —
в блоке `data_mode`.

**Повторы и лимиты.** Каждый запрос выполняется до `HTTP_MAX_RETRIES` раз
(по умолчанию 3) с экспоненциальной паузой 1–30 с. Если API прислал `Retry-After`,
используется эта пауза. Тайм-аут одного запроса — `HTTP_TIMEOUT_SECONDS` (15 с).

**Классы ошибок** (`app/ingestion/base_client.py`). Они же записываются в мониторинг
и определяют сообщение пользователю:

| kind | Причина | Сообщение в Telegram |
|---|---|---|
| `timeout` | нет ответа за отведённое время | «⏱ FMP: превышено время ожидания ответа» |
| `network` | соединение не установлено | «🔌 FMP: источник недоступен (сетевая ошибка)» |
| `rate_limit` | HTTP 429 после повторов | «⏳ FMP: превышен лимит запросов API» |
| `auth` | HTTP 401/402/403 | «🔑 FMP отклонил запрос (HTTP 402): ключ … или тариф …» |
| `not_found` / `no_data` | 404 или пустой ответ | для истории цен → «❓ Тикер … не найден» |
| `invalid_response` | ответ не JSON | «⚠️ FMP: получен некорректный ответ API» |
| `http_error` | прочие HTTP-ошибки | «🔌 FMP: ошибка API (HTTP …)» |

Если отказали и основной, и резервный источник, в сообщении указывается
«FMP и резервный источник». Во всех случаях расчёт **не сохраняется**, а сообщение
не содержит торгового вывода.

**Неизвестный тикер.** FMP на несуществующий символ отвечает HTTP 200 с пустым
списком. Если пусто и у основного, и у резервного источника, возникает
`TickerNotFoundError`: запись в историю не создаётся, пользователь видит
«Тикер … не найден».

## 3. Качество данных

`app/ingestion/normalization.py`:

- **Нормализация.** Единый формат OHLCV, торговая дата в зоне America/New_York,
  цены в USD, время получения — UTC.
- **Валидация бара.** O/H/L/C > 0, volume ≥ 0, low ≤ open/close ≤ high.
  Некорректный бар отбрасывается и учитывается в `QualityReport`.
- **Дубли.** Дедупликация по (тикер, дата), побеждает последний.
- **Полнота.** Меньше 250 баров → история помечается неполной. Отсутствие любой из
  ключевых fundamentals (revenue_growth, eps_growth, eps, gross_margin,
  debt_equity, pe) → fundamentals неполные.
- **Пропуски.** Отсутствующее значение остаётся `null` и **никогда не заменяется
  нулём**. Неполнота приводит к флагу `missing_data`.

`QualityReport` каждого набора сохраняется в `raw_inputs.quality`.

## 4. Feature Engine — исходные метрики

`app/features/indicators.py`. Все формулы реализованы вручную на pandas/numpy.
Значение берётся по последнему бару.

| Метрика | Формула | Параметры |
|---|---|---|
| EMA(N) | `EMA_t = close_t·k + EMA_{t−1}·(1−k)`, `k = 2/(N+1)`; старт — SMA первых N баров | N = 20, 50, 200 |
| RSI | по Уайлдеру: `RS = avgGain/avgLoss`, `RSI = 100 − 100/(1+RS)`; при avgLoss = 0 RSI = 100 | 14 |
| MACD | `MACD = EMA12 − EMA26`; `Signal = EMA9(MACD)`; `Hist = MACD − Signal` | 12/26/9 |
| ATR | по Уайлдеру от `TR = max(H−L, |H−C₋₁|, |L−C₋₁|)`; `ATR% = ATR/close` | 14 |
| Price change | `(close_t − close_{t−n}) / close_{t−n} · 100` | n = 1, 5, 20 баров |
| Volume Ratio | `volume_t / SMA20(volume)` | 20 |
| Avg volume | `SMA20(volume)` | 20 |
| Relative Strength | `(ret63(тикер) − ret63(SPY))·100`, `ret63 = close_t/close_{t−63} − 1` | бенчмарк SPY, 63 бара |
| Gap | `(open_t − close_{t−1}) / close_{t−1} · 100` | — |
| Distance to EMA | `(close − EMA)/EMA · 100` | EMA20, EMA50 |

Fundamentals берутся из FMP за **один и тот же период** (`annual`) во всех трёх
запросах, поэтому квартальные и годовые данные не смешиваются. EPS в stable API —
`netIncomePerShare`, P/E — `priceToEarningsRatio`, D/E — `debtToEquityRatio`,
рост — `growthRevenue` и `growthEPS`.

Если истории не хватает для метрики, её значение = `null`. Если отсутствует
критичная метрика (price, EMA20, RSI14, ATR14), набор помечается неполным.

## 5. Семь факторных оценок

`app/scoring/factor_scores.py`. Шкала каждого фактора — 0…100:
`clamp(x) = min(100, max(0, x))`. Если нет части компонентов фактора, веса
оставшихся перенормируются. Если нет ни одного компонента, фактор = `null`.
Параметры берутся из `config/thresholds.yaml → factor_scores`.

| Фактор (вес) | Входы | Правило v1.0 |
|---|---|---|
| **Momentum** (20%) | RSI14, MACD hist, close, EMA20/50/200 | `0.30·RSI_s + 0.30·MACD_s + 0.40·EMA_s`. RSI_s: 100 в зоне [60, 70]; ниже — `100 − 2·(60 − RSI)`; выше — `100 − 3·(RSI − 70)`, при RSI ≥ 80 — `100 − 5·(RSI − 70)`. MACD_s = `clamp(50 + Hist/close·100·50)`. EMA_s = доля выполненных условий цепочки close > EMA20 > EMA50 > EMA200, ×100 |
| **Growth** (15%) | Revenue growth, EPS growth (доли) | `0.5·clamp(50 + RevG·100·2.0) + 0.5·clamp(50 + EPSG·100·1.5)` |
| **Fundamentals** (15%) | EPS, Gross margin, Debt/Equity | среднее трёх: EPS > 0 → 70, иначе 30; `clamp(GM·100)`; `clamp(100 − D/E·50)` |
| **Relative Strength** (15%) | RelStrength63 (п.п.) | `clamp(50 + RS·5.0)` |
| **Volume** (10%) | Volume Ratio | `clamp(50 + (VR − 1)·50)` |
| **Valuation** (10%) | Forward P/E, иначе P/E | P/E ≤ 0 → 25; ≤ 10 → 100; ≥ 40 → 0; между ними линейно `100·(40 − PE)/(40 − 10)` |
| **Catalysts** (15%) | новости (FMP / Finnhub), SEC filings | каждый уникальный заголовок оценивается по словарю: +1 (beat, upgrade, record, raises …), −1 (miss, downgrade, lawsuit, probe …), иначе 0; `sentiment` = среднее; `score = clamp(50 + sentiment·50)`. Filings SEC сохраняются как подтверждённые события (evidence с датой и источником). Нет новостей → `null` |

Разбор каждого фактора («входы → правило → оценка») показывает кнопка
«Подробнее» в боте.

## 6. Final Score

`app/scoring/final_score.py`:

```
Final = 0.20·Momentum + 0.15·Growth + 0.15·Fundamentals + 0.15·RelStrength
      + 0.10·Volume + 0.10·Valuation + 0.15·Catalysts
```

- Веса лежат в `config/thresholds.yaml → final_score_weights`, их сумма = 1.0.
  Конфигурация проверяется при загрузке.
- **Округление.** Каждый фактор и Final Score округляются до целого один раз, на
  выходе Score Engine, «половина вверх» (`floor(x + 0.5)`). Взвешенная сумма
  считается по **неокруглённым** факторам. Все интерфейсы показывают уже
  округлённые значения из БД.
- **Диапазон.** Результат ограничивается [0, 100].
- **Неполные данные.** Если хотя бы один фактор = `null`, Final Score не
  рассчитывается и показывается как «неполный расчёт»: веса не перераспределяются,
  неполный набор не выдаётся за полный.
- **Версия.** С каждым расчётом сохраняются `formula_version` и полный снимок
  весов и параметров (`raw_input_snapshot.calculation.formula_config`).

## 7. Risk Filter

`app/risk/risk_filter.py`. Пороги берутся из `config/thresholds.yaml → risk_filter`.
Каждый флаг сохраняется с причиной (`reason`) и значением (`value`).

| Флаг | Условие (v1.0) | Потолок статуса для Rule Engine |
|---|---|---|
| `high_volatility` | ATR/close > 5% | WATCH |
| `event_risk` | отчётность через ≤ 3 рабочих дня от даты расчёта | BUY ON DIP |
| `gap_risk` | |Gap| > 5% | BUY ON DIP |
| `overextension` | Distance to EMA20 > 15% | BUY ON DIP |
| `liquidity_risk` | средний объём за 20 дней < 500 000 | WATCH |
| `news_risk` | sentiment < −0.3 | WATCH |
| `missing_data` | нет критичных метрик, fundamentals, фактора или earnings | WATCH |

Уровень риска: `high`, если есть `missing_data` или `liquidity_risk`, либо ≥ 3
флагов; `medium` — 1–2 флага; `low` — флагов нет.

Потолок статуса (`allowed_max_status`) сохраняется в `signals.status`. Это вход
для Rule Engine Этапа 2, а **не торговый статус**: до реализации Rule Engine он
пользователю не показывается ни в основном сообщении, ни в истории.

## 8. История и воспроизведение

Таблица `signals` работает только на добавление: каждый расчёт создаёт новую
строку, старые записи не перезаписываются. Для каждой записи хранятся:

| Поле | Содержимое |
|---|---|
| `ticker`, `timestamp` (UTC), `price` | тикер, точное время расчёта, цена |
| `factor_scores` (отдельная таблица) | 7 округлённых факторов |
| `final_score`, `formula_version` | итог и версия формул |
| `risk_flags` | флаги с причинами и значениями |
| `raw_input_snapshot.features` | все метрики Feature Engine |
| `raw_input_snapshot.fundamentals` | fundamentals, использованные в расчёте |
| `raw_input_snapshot.catalysts` | sentiment, счётчики, evidence (новости и filings с датами) |
| `raw_input_snapshot.sources` | источник, режим и время получения по каждому виду данных |
| `raw_input_snapshot.calculation` | веса, правило Final Score, полный снимок параметров формул |
| `raw_input_snapshot.raw_inputs` | **исходные данные**: все бары OHLCV тикера и SPY, fundamentals, дата отчётности, все новости, все filings, дата расчёта, отчёты качества |

Полный объём `raw_inputs` для тикера с историей ≈ 5 лет — порядка 150–200 КБ JSON
на расчёт.

**Воспроизведение** всей цепочки «исходные данные → метрики → факторы → Final
Score → флаги» по сохранённой записи:

```bash
docker compose run --rm app python scripts/replay_signal.py <signal_id>
```

Скрипт выводит таблицу «сохранено ↔ пересчитано» по каждому фактору, Final Score
и флагам, а в конце — «РЕЗУЛЬТАТ: совпадает». Программный интерфейс:
`app.db.repository.replay_signal(signal)`.

## 9. Служебный API `/health`

FastAPI слушает `127.0.0.1:8000` на сервере; снаружи он доступен через SSH-туннель:
`ssh -L 8000:127.0.0.1:8000 user@server`.

`GET /health` → `200 OK`:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "sources_ok": false,
  "sources": {
    "fmp": {
      "source": "fmp", "role": "primary", "status": "error",
      "last_checked_at": "2026-09-30T12:00:05Z",
      "last_success_at": "2026-09-30T11:58:10Z",
      "last_latency_ms": 15012.4,
      "consecutive_errors": 3, "total_errors": 7,
      "last_error": "timeout: ReadTimeout('…')"
    },
    "alpaca":    { "role": "reserve",  "status": "ok", "…": "…" },
    "sec_edgar": { "role": "official", "status": "ok", "…": "…" }
  },
  "data_mode": {
    "mode": "reserve",
    "signal_id": 42, "ticker": "AAPL", "timestamp": "2026-09-30T12:00:06Z",
    "price_history": { "source": "alpaca", "mode": "reserve", "fetched_at": "…" },
    "benchmark":     { "source": "alpaca", "mode": "reserve", "fetched_at": "…" },
    "fundamentals":  { "source": "fmp", "mode": "primary", "fetched_at": "…" },
    "earnings":      { "source": "fmp", "mode": "primary", "fetched_at": "…" },
    "news": { "sources": ["fmp"], "mode": "primary" },
    "filings": ["sec_edgar"]
  }
}
```

- Статус источника обновляется при каждом обращении. После восстановления API он
  сам возвращается в `ok`, историю править вручную не нужно. Все проверки
  пишутся в таблицу `source_health`: её используют и бот, и API.
- `last_error` начинается с класса причины (`timeout:`, `network:`,
  `rate_limit:`, `auth:`, `not_found:`, `http_error:`, `invalid_response:`).
- `data_mode` показывает, на каких источниках выполнен последний расчёт.

## 10. Telegram-бот

| Ввод | Результат |
|---|---|
| `AAPL` или `$AAPL` (1–5 латинских букв) | расчёт: Final Score, время расчёта (UTC), версия формулы, источники, режим данных, цена, 7 факторов, Risk-флаги, дисклеймер |
| кнопка «Подробнее» | разбор 7 факторов: входы → правило → оценка; новости и filings с датами и источниками |
| кнопка «Метрики» | все технические и фундаментальные метрики |
| кнопка «Risk» | каждый флаг с условием и значением |
| кнопка «История» или `/history AAPL` | последние 10 расчётов: время, цена, Score, риск. Новый расчёт не стирает старые |
| `/start` | подсказка |

Кнопки привязаны к `signal_id` конкретного расчёта, поэтому ответы по разным
тикерам не смешиваются. При сбое источника бот отвечает сообщением из раздела 2 и
не выдаёт торгового вывода. Доступ можно ограничить через
`TELEGRAM_ALLOWED_IDS`.

## 11. Конфигурация

- `.env` (шаблон — `.env.example`; порядок заполнения — `docs/SETUP.md`): ключи
  API, `SEC_USER_AGENT`, токен бота, доступ к БД, `HTTP_TIMEOUT_SECONDS`,
  `HTTP_MAX_RETRIES`. В коде ключей нет.
- `config/thresholds.yaml`: версия формул, веса Final Score, параметры факторов,
  пороги Risk Filter и Rule Engine. Если порог меняется, нужно поднять `version`.
  Новая версия будет записана в `formula_versions` и в каждый следующий расчёт,
  а старые записи сохранят свою версию и снимок параметров.
- `requirements.lock`: зафиксированные версии зависимостей для сборки образа.
  Лицензии перечислены в `docs/THIRD_PARTY_LICENSES.md`.
