"""Централизованная конфигурация приложения (Pydantic Settings, читает .env).

Единая точка доступа к секретам и параметрам окружения. Бизнес-веса и пороги
живут отдельно в config/thresholds.yaml и версионируются через formula_versions
(ТЗ раздел 8, 13) — здесь только инфраструктурные настройки.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Data sources ----
    fmp_api_key: str = ""
    fmp_base_url: str = "https://financialmodelingprep.com"

    sec_user_agent: str = "switch-trading-mvp andrey.detinkin@gmail.com"
    sec_base_url: str = "https://data.sec.gov"

    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_base_url: str = "https://data.alpaca.markets"

    finnhub_api_key: str = ""
    finnhub_base_url: str = "https://finnhub.io/api/v1"

    # ---- Telegram ----
    telegram_bot_token: str = ""
    telegram_allowed_ids: list[int] = Field(default_factory=list)

    # ---- LLM ----
    llm_provider: str = "claude"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ---- Database ----
    postgres_user: str = "switch"
    postgres_password: str = "switch"
    postgres_db: str = "switch_trading"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    database_url: str = ""

    # ---- Runtime ----
    market_timezone: str = "America/New_York"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3
    log_level: str = "INFO"

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        """Разрешает список Telegram ID как строку "1,2,3" из .env."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    @property
    def async_database_url(self) -> str:
        """Async DSN для SQLAlchemy/asyncpg."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный singleton настроек."""
    return Settings()
