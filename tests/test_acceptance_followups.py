"""Замечания приёмки после v1.5: актуальность SPY, HTML-нарезка, /ready."""

from __future__ import annotations

import asyncio
import random
import re
import tomllib
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.bot.templates import split_message
from app.calculation import calculate
from app.config import get_settings
from app.db.session import create_all
from app.main import app
from app.scoring.thresholds import DataQualityConfig
from tests.test_source_health import _app_env
from tests.test_v15_fixes import FUND, V15, _assess, _history

REPO_ROOT = Path(__file__).resolve().parents[1]
CALC = date(2026, 10, 5)  # понедельник
FRESH = date(2026, 10, 2)  # пятница: 1 рабочий день до расчёта
STALE = date(2026, 9, 1)


def test_acceptance_smoke_dependencies_are_in_production_image():
    """Live smoke запускается в app-контейнере без dev extra."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    dependencies = project["dependencies"]
    assert any(item.startswith("aiosqlite") for item in dependencies)


# --------------------------------------------------------------------------- #
# Актуальность бенчмарка SPY
# --------------------------------------------------------------------------- #
def test_stale_benchmark_blocks_final_score():
    """По устаревшему SPY Relative Strength считался бы за старое окно."""
    quality = _assess(
        V15.data_quality,
        _history(260, last=FRESH),
        calc_date=CALC,
        benchmark=_history(260, last=STALE, ticker="SPY"),
    )
    assert quality.confidence == "insufficient"
    assert quality.critical == [
        "история бенчмарка SPY устарела: последний бар 2026-09-01 — 24 раб. дн. "
        "до даты расчёта (допустимо 3) — Relative Strength не рассчитать"
    ]


def test_fresh_benchmark_and_legacy_rule_are_unaffected():
    fresh = _assess(
        V15.data_quality,
        _history(260, last=FRESH),
        calc_date=CALC,
        benchmark=_history(260, last=FRESH, ticker="SPY"),
    )
    assert fresh.confidence == "high"
    # записи до v1.5: возраст истории (в т. ч. SPY) не проверялся
    legacy = _assess(
        DataQualityConfig(),
        _history(260, last=FRESH),
        calc_date=CALC,
        benchmark=_history(260, last=STALE, ticker="SPY"),
    )
    assert legacy.confidence == "high"


def test_stale_benchmark_in_full_calculation():
    result = calculate(
        price_history=_history(260, last=FRESH),
        benchmark=_history(260, last=STALE, ticker="SPY"),
        fundamentals=FUND,
        next_earnings_date=date(2099, 1, 1),
        earnings_available=True,
        news=[],
        filings=[],
        calc_date=CALC,
        config=V15,
    )
    assert result.final.final_score is None
    missing = next(f for f in result.risk.active_flags if f.flag == "missing_data")
    assert "история бенчмарка SPY устарела" in missing.reason


# --------------------------------------------------------------------------- #
# Нарезка длинных сообщений без разрыва тегов и сущностей
# --------------------------------------------------------------------------- #
_ENTITY_RE = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z]\w*;)")


class _Markup(HTMLParser):
    """Проверяет баланс тегов и собирает видимый текст."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.balanced = True
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.balanced = False
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def _parse(markup: str) -> _Markup:
    parser = _Markup()
    parser.feed(markup)
    parser.close()
    return parser


def _assert_valid_chunks(text: str, chunks: list[str], limit: int) -> None:
    for chunk in chunks:
        assert len(chunk) <= limit
        parsed = _parse(chunk)
        assert parsed.balanced and not parsed.stack, chunk  # теги закрыты в каждой части
        assert not _ENTITY_RE.search(chunk), chunk  # сущности не разрезаны
    visible = "".join("".join(_parse(c).text) for c in chunks).replace("\n", "")
    assert visible == "".join(_parse(text).text).replace("\n", "")  # текст не потерян


def test_long_line_does_not_split_entities():
    line = "• " + "&amp;" * 500  # 2502 символа, сущность через каждые 5
    chunks = split_message(line, limit=1000)
    assert len(chunks) == 3
    _assert_valid_chunks(line, chunks, 1000)


def test_long_line_reopens_tags_across_chunks():
    line = '<b>Заголовок</b> · <a href="https://e.x/?a=1&amp;b=2">' + "слово " * 400 + "</a>"
    chunks = split_message(line, limit=500)
    assert len(chunks) > 1
    _assert_valid_chunks(line, chunks, 500)
    assert all(c.startswith('<a href="https://e.x/?a=1&amp;b=2">') for c in chunks[1:])
    assert all(c.endswith("</a>") for c in chunks)
    # текст режется по пробелам, а не посреди слова
    assert all(re.search(r"слово\s*</a>$", c) for c in chunks)


def test_random_markup_is_split_safely():
    rng = random.Random(7)
    pieces = ["слово ", "&amp;", "&lt;", "&#39;", "x" * 40, " ", "<b>", "<i>", "<a href=\"u\">"]
    for _ in range(300):
        tokens: list[str] = []
        stack: list[str] = []
        for _ in range(rng.randint(1, 300)):
            piece = rng.choice(pieces)
            if piece.startswith("<"):
                name = piece[1]
                if name in stack:
                    tokens.append(f"</{stack.pop()}>")
                    continue
                stack.append(name)
            tokens.append(piece)
        tokens += [f"</{name}>" for name in reversed(stack)]
        text = "строка\n" + "".join(tokens) + "\nконец"
        limit = rng.choice([80, 200, 1000])
        _assert_valid_chunks(text, split_message(text, limit=limit), limit)


# --------------------------------------------------------------------------- #
# /ready: готовность с проверкой БД
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _restore_app_state():
    """lifespan и тесты меняют app.state — возвращаем как было для других модулей."""
    saved = dict(app.state._state)
    yield
    app.state._state.clear()
    app.state._state.update(saved)


def test_ready_ok_when_database_answers(monkeypatch, tmp_path):
    _app_env(monkeypatch, tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    asyncio.run(create_all(engine))
    asyncio.run(engine.dispose())
    with TestClient(app) as client:  # с lifespan: настоящее подключение приложения
        response = client.get("/ready")
    get_settings.cache_clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_ready_503_when_database_is_down(monkeypatch):
    # порт 1 на localhost: соединение отклоняется сразу
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x")
    monkeypatch.setattr(app.state, "db_engine", engine, raising=False)
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    # /health остаётся liveness: процесс жив, HTTP 200
    assert client.get("/health").status_code == 200


def test_ready_503_without_engine(monkeypatch):
    monkeypatch.setattr(app.state, "db_engine", None, raising=False)
    assert TestClient(app).get("/ready").status_code == 503


def test_compose_healthcheck_uses_ready():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    test = " ".join(compose["services"]["app"]["healthcheck"]["test"])
    assert "/ready" in test and "/health" not in test
