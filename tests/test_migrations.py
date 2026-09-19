"""Проверка миграций Alembic на временной БД (DoD 1.7: схема через Alembic).

Прогоняет `alembic upgrade head` на временном SQLite и убеждается, что созданы
все таблицы раздела 5 — валидирует цепочку миграций без необходимости Postgres.
Синхронный тест: env.py использует asyncio.run внутри, поэтому запускающий поток
не должен иметь активного event loop.
"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

EXPECTED_TABLES = {
    "tickers",
    "price_history",
    "fundamentals_snapshot",
    "market_context",
    "formula_versions",
    "signals",
    "factor_scores",
    "source_health",
    "telegram_users",
}

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_creates_all_tables(tmp_path):
    db_path = tmp_path / "migrations_test.db"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert EXPECTED_TABLES.issubset(tables)
    assert "alembic_version" in tables  # версия миграции зафиксирована
