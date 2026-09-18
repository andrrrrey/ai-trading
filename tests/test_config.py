"""Тесты конфигурации приложения."""
from __future__ import annotations

from app.config import Settings


def test_allowed_ids_parsed_from_csv_string():
    settings = Settings(telegram_allowed_ids="1, 2 ,3")
    assert settings.telegram_allowed_ids == [1, 2, 3]


def test_allowed_ids_empty_string_is_empty_list():
    settings = Settings(telegram_allowed_ids="")
    assert settings.telegram_allowed_ids == []


def test_async_database_url_composed_from_parts():
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_host="db",
        postgres_port=5432,
        postgres_db="mydb",
        database_url="",
    )
    assert settings.async_database_url == "postgresql+asyncpg://u:p@db:5432/mydb"


def test_explicit_database_url_wins():
    settings = Settings(database_url="postgresql+asyncpg://x/y")
    assert settings.async_database_url == "postgresql+asyncpg://x/y"
