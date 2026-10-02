"""Сборка артефактов приёмки Этапа 1 по чек-листу заказчика (docs/acceptance-evidence).

Артефакты не пишутся вручную: каждый файл генерируется из текущего кода — номер
commit, версия формул, вывод автотестов по подэтапу и (на сервере, с --live)
живые прогоны: smoke по тикеру, /health, воспроизведение сохранённого расчёта.
Скриншоты Telegram и записи демонстрации добавляются вручную в папку подэтапа;
в каждом файле перечислено, какие именно снимки нужны.

Локально (без внешних API):
    pip install -e ".[dev]" && python scripts/collect_evidence.py
На сервере после `docker compose up -d` (из корня проекта на хосте; автотесты —
в локальном venv, живые прогоны — внутри работающего контейнера app):
    python scripts/collect_evidence.py --live --docker --ticker AAPL --signal-id <id>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "acceptance-evidence"
sys.path.insert(0, str(ROOT))

# Подэтап → (название, тесты, проверки чек-листа, скриншоты, живые артефакты)
STAGES: dict[str, dict] = {
    "1.1": {
        "title": "Источники данных",
        "tests": [
            "tests/test_fmp_client.py",
            "tests/test_sec_edgar_client.py",
            "tests/test_alpaca_finnhub.py",
            "tests/test_source_router.py",
        ],
        "checks": [
            "1–3: FMP (котировка, история, fundamentals, earnings, profile, новости), SEC EDGAR "
            "(10-K/10-Q/8-K/NT) — live smoke ниже",
            "4, 7: резерв Alpaca (цены с той же корректировкой на сплиты, что у FMP; "
            "котировка) / Finnhub (новости); общий режим расчёта primary / reserve / "
            "degraded / unavailable, SEC учитывается явно; переключение при "
            "таймауте, сетевой и HTTP-ошибке — тесты source_router и e2e",
            "5–6: источник, режим (primary/reserve) и fetched_at каждого набора — "
            "raw_input_snapshot.sources",
        ],
        "screens": [
            "ответ бота по реальному тикеру со строками «Источники», «Режим данных», «Цена»",
            "кнопка «Подробнее» с SEC-событиями и новостями (источник, дата)",
        ],
        "live": ["smoke"],
    },
    "1.2": {
        "title": "Получение и качество данных",
        "tests": [
            "tests/test_normalization.py",
            "tests/test_base_client.py",
            "tests/test_ingestion_service.py",
            "tests/test_data_quality.py",
        ],
        "checks": [
            "1–3: нормализация OHLCV (NY-дата, USD), валидация, дедупликация",
            "2, 8: пропуски остаются null; полнота оценивается по двум уровням "
            "(test_data_quality.py): критично — Score не выдаётся, некритично — "
            "достоверность пониженная; причины — во флаге missing_data",
            "4–6: некорректный ответ, timeout, rate limit — классы ошибок и сообщения",
            "7: недоступность источника → резерв или честное сообщение",
        ],
        "screens": [
            "сообщение бота при недоступном источнике (таймаут / ключ / лимит)",
            "сообщение «Расчёт неполный» с причинами (кнопка «Risk»)",
        ],
        "live": [],
    },
    "1.3": {
        "title": "Расчёт исходных метрик",
        "tests": ["tests/test_indicators.py"],
        "checks": [
            "1–7: RSI14, MACD 12/26/9, EMA20/50/200, Δ1/5/20д, Volume Ratio 20, ATR14, "
            "Rel.Strength 63д vs SPY — эталонные значения в тестах",
            "8: fundamentals одного периода (annual) во всех запросах FMP",
            "9: ручная сверка ≥3 метрик с TradingView/ручным расчётом — заполнить таблицу ниже",
        ],
        "screens": [
            "кнопка «Метрики» в боте",
            "TradingView: RSI14, EMA20, ATR14 на ту же дату закрытия EOD",
        ],
        "manual": (
            "| Метрика | Система | Независимый источник | Расхождение |\n"
            "|---|---|---|---|\n| RSI14 |  |  |  |\n| EMA20 |  |  |  |\n| ATR14 |  |  |  |"
        ),
        "live": [],
    },
    "1.4": {
        "title": "Семь факторных оценок",
        "tests": ["tests/test_factor_scores.py", "tests/test_catalysts.py"],
        "checks": [
            "1–7: по каждому фактору — входы, правило, оценка (кнопка «Подробнее»)",
            "7: Catalysts = новости за 14 дней (будущие, без даты и устаревшие отброшены) "
            "+ события SEC (8-K по пунктам, NT 10-K/10-Q), "
            "вес по давности, SEC приоритетнее СМИ",
            "8–9: шкала 0–100, веса 20/15/15/15/10/10/15 из config/thresholds.yaml",
        ],
        "screens": ["кнопка «Подробнее» (все 7 факторов, правило Final Score)"],
        "live": [],
    },
    "1.5": {
        "title": "Итоговый Score",
        "tests": ["tests/test_final_score.py"],
        "checks": [
            "1–3: формула, веса (сумма = 1.0 проверяется), диапазон 0–100",
            "4: округление «половина вверх» один раз на выходе",
            "5–6: ручная сверка и повтор — scripts/replay_signal.py",
            "7: чувствительность — тесты; 8: версия формул в каждой записи",
            "при отсутствии фактора Final Score не рассчитывается (веса не перераспределяются)",
        ],
        "screens": ["вывод scripts/replay_signal.py для сигнала из демонстрации"],
        "live": ["replay"],
    },
    "1.6": {
        "title": "Базовый Risk Filter",
        "tests": ["tests/test_risk_filter.py"],
        "checks": [
            "1–5: high_volatility, event_risk, gap_risk, liquidity_risk, overextension",
            "6: missing_data — короткая история, нет fundamentals/earnings, SEC или новости "
            "недоступны; два уровня: критично — Score не выдаётся, некритично — "
            "«достоверность: пониженная»",
            "7–8: причина и значение каждого флага сохраняются в signals.risk_flags",
            "9: потолок статуса (allowed_max_status) сохраняется для Rule Engine Этапа 2",
        ],
        "screens": ["кнопка «Risk» с причинами флагов"],
        "live": [],
    },
    "1.7": {
        "title": "База данных и история",
        "tests": [
            "tests/test_db.py",
            "tests/test_migrations.py",
            "tests/test_e2e_stage1.py::test_profile_tables_are_filled",
            "tests/test_e2e_stage1.py::test_replay_uses_stored_risk_thresholds",
        ],
        "checks": [
            "профильные таблицы: tickers, price_history, fundamentals_snapshot, "
            "market_context (со ссылкой на signal_id), telegram_users",
            "1–7: тикер, время, исходные данные (raw_inputs), метрики, 7 факторов, "
            "Final Score, флаги, источники и версия формул",
            "8: два расчёта = две записи (append-only)",
            "9: восстановление цепочки — scripts/replay_signal.py (параметры и пороги "
            "Risk Filter берутся из сохранённой версии формул)",
        ],
        "screens": ["/history по тикеру после двух расчётов", "вывод replay_signal.py"],
        "live": ["replay"],
    },
    "1.8": {
        "title": "Мониторинг источников и ошибок",
        "tests": ["tests/test_source_health.py", "tests/test_probe.py"],
        "checks": [
            "1–4: /health — состояние, последний успех, latency по FMP, SEC, Alpaca, Finnhub",
            "5: errors_24h, total_errors; текущая ошибка (last_error, класс) очищается "
            "при восстановлении, последний сбой — last_failure_at / last_failure_error",
            "6, 8: принудительный сбой и восстановление без ручной правки истории",
            "7: mode (primary/reserve/degraded/unavailable) и data_mode последнего расчёта",
            "устаревшая (> 30 мин) успешная проверка не выдаётся за ok: state=unknown, stale",
        ],
        "screens": [
            "/health в норме",
            "/health при искусственном сбое (неверный FMP_API_KEY) и после восстановления",
        ],
        "live": ["health"],
    },
    "1.9": {
        "title": "Базовый Telegram интерфейс",
        "tests": ["tests/test_bot.py"],
        "checks": [
            "1–4: тикер → время, Final Score, 7 факторов, Risk с причинами",
            "5: история не стирается; торговые статусы до Этапа 2 не показываются",
            "6–8: неизвестный тикер, неполные данные, ошибка API — понятные сообщения",
            "9: кнопки привязаны к signal_id — результаты не смешиваются",
            "внешний текст экранируется (Telegram HTML)",
        ],
        "screens": [
            "основной ответ по реальному тикеру",
            "неизвестный тикер (например, ZZZZQ)",
            "/history",
            "последовательные запросы двух тикеров и их кнопки",
        ],
        "live": [],
    },
    "1.10": {
        "title": "Сквозная контрольная проверка",
        "tests": [
            "tests/test_e2e_stage1.py",
            "tests/test_v15_fixes.py",
            "tests/test_acceptance_followups.py",
        ],
        "checks": [
            "1: тикер проходит всю цепочку до Telegram (live smoke ниже)",
            "2–4: backend = история = Telegram; повтор на сохранённых данных совпадает",
            "5: отключение API → резерв или честное сообщение",
            "6: оба прогона сохранены (автотесты; на сервере — live ниже)",
            "7: отсутствие блокирующих дефектов подтверждается только на live-демонстрации "
            "(заполняется приёмщиком)",
        ],
        "screens": [
            "запись экрана: запрос тикера → кнопки → /history",
            "отключение FMP (неверный ключ) → ответ бота и /health",
        ],
        "live": ["smoke", "replay", "health"],
    },
}


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    shown = ["python" if part == sys.executable else part for part in cmd]
    return f"$ {' '.join(shown)}\n{output}\n[exit code {result.returncode}]"


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip() or "н/д"
    except OSError:
        return "н/д"


def _health(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.dumps(json.load(response), ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return f"не удалось получить {url}: {exc!r}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="живые прогоны на сервере")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--signal-id", type=int)
    parser.add_argument(
        "--docker", action="store_true", help="живые прогоны внутри контейнера app"
    )
    args = parser.parse_args()
    in_app = ["docker", "compose", "exec", "-T", "app", "python"] if args.docker else [
        sys.executable
    ]

    from app.scoring import load_scoring_config

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    commit = _commit()
    formula = load_scoring_config().version
    full_suite = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    lint = _run([sys.executable, "-m", "ruff", "check", "app", "tests", "scripts"])

    live: dict[str, str] = {}
    if args.live:
        live["smoke"] = _run([*in_app, "scripts/stage1_acceptance_smoke.py", args.ticker])
        live["health"] = _health(args.health_url)
        if args.signal_id:
            live["replay"] = _run([*in_app, "scripts/replay_signal.py", str(args.signal_id)])

    for stage, spec in STAGES.items():
        lines = [
            f"# Подэтап {stage} — {spec['title']}",
            "",
            f"- Сформировано: {now} (`scripts/collect_evidence.py`)",
            f"- Commit: `{commit}` · версия формул: `{formula}`",
            f"- Живые прогоны: {'да' if args.live else 'нет — выполнить на сервере с --live'}",
            "",
            "## Что подтверждает (пункты чек-листа)",
            *[f"- {item}" for item in spec["checks"]],
            "",
            "## Автотесты подэтапа",
            "```",
            _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *spec["tests"]]),
            "```",
        ]
        for kind in spec["live"]:
            title = {
                "smoke": f"Live smoke ({args.ticker})",
                "replay": "Воспроизведение сохранённого расчёта",
                "health": "/health",
            }[kind]
            body = live.get(kind)
            if body is None:
                body = (
                    "не выполнялось: запустить на сервере "
                    "`python scripts/collect_evidence.py --live"
                    + (" --signal-id <id>" if kind == "replay" else "")
                    + "`"
                )
            lines += ["", f"## {title}", "```" + ("json" if kind == "health" else ""), body, "```"]
        if spec.get("manual"):
            lines += ["", "## Ручная сверка (заполнить на демонстрации)", spec["manual"]]
        lines += [
            "",
            "## Скриншоты / запись (добавить в эту папку после серверного запуска)",
            *[f"- [ ] {item}" for item in spec["screens"]],
        ]
        if stage == "1.10":
            lines += [
                "",
                "## Весь набор автотестов",
                "```",
                full_suite,
                "```",
                "",
                "## ruff",
                "```",
                lint,
                "```",
            ]
        target = OUT / stage / "evidence.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"записан {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
