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


def _complete(**overrides) -> Settings:
    values = dict(
        fmp_api_key="k",
        sec_user_agent="switch-trading-mvp ops@company.ru",
        postgres_password="pw",
        telegram_bot_token="t",
        telegram_allowed_ids="42",
    )
    values.update(overrides)
    return Settings(**values)


def test_complete_config_has_no_problems():
    assert _complete().missing_required("bot") == []
    assert _complete().missing_required("api") == []


def test_required_settings_are_reported():
    import pytest

    from app.config import ConfigError

    settings = _complete(
        fmp_api_key="",
        sec_user_agent="switch-trading-mvp your-email@example.com",
        telegram_allowed_ids="",
    )
    problems = " | ".join(settings.missing_required("bot"))
    assert "FMP_API_KEY" in problems
    assert "SEC_USER_AGENT" in problems
    assert "TELEGRAM_ALLOWED_IDS" in problems
    with pytest.raises(ConfigError):
        settings.require("bot")


def test_public_bot_must_be_explicit():
    settings = _complete(telegram_allowed_ids="", telegram_allow_public=True)
    assert settings.missing_required("bot") == []
