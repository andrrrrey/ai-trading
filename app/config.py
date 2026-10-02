"""Централизованная конфигурация приложения (Pydantic Settings, читает .env).

Единая точка доступа к секретам и параметрам окружения. Бизнес-веса и пороги
живут отдельно в config/thresholds.yaml и версионируются через formula_versions
(ТЗ раздел 8, 13) — здесь только инфраструктурные настройки.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Обязателен: "<проект> <контактный email>" (требование SEC fair access).
    sec_user_agent: str = ""
    sec_base_url: str = "https://data.sec.gov"

    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_base_url: str = "https://data.alpaca.markets"
    # iex — бесплатный тариф (только биржа IEX); sip — все биржи США (платный).
    alpaca_data_feed: str = "iex"

    finnhub_api_key: str = ""
    finnhub_base_url: str = "https://finnhub.io/api/v1"

    # ---- Telegram ----
    telegram_bot_token: str = ""
    # NoDecode: значение из .env/окружения не разбирается как JSON, а приходит
    # строкой "1,2,3" в _parse_ids (иначе "123" или "1,2" роняли запуск).
    telegram_allowed_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Открытый доступ к боту (без whitelist) — только осознанным решением.
    telegram_allow_public: bool = False

    # ---- LLM ----
    llm_provider: str = "claude"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ---- Database ----
    postgres_user: str = "switch"
    postgres_password: str = ""
    postgres_db: str = "switch_trading"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    database_url: str = ""

    # ---- Runtime ----
    market_timezone: str = "America/New_York"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3
    log_level: str = "INFO"
    # Фоновая проверка источников для /health, сек; 0 — выключена.
    health_probe_interval_seconds: float = 600.0
    # Срок хранения ленты проверок source_health, дней (очистка — в фоновом probe).
    source_health_retention_days: int = 30

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        """Разрешает список Telegram ID как строку "1,2,3" из .env."""
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            value = value.strip().strip("[]")
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

    def missing_required(self, component: Literal["api", "bot", "smoke"]) -> list[str]:
        """Обязательные параметры .env, которые не заданы или заданы неверно."""
        problems: list[str] = []
        if not self.fmp_api_key:
            problems.append("FMP_API_KEY не задан")
        agent = self.sec_user_agent.strip()
        if "@" not in agent or "example.com" in agent:
            problems.append(
                "SEC_USER_AGENT должен быть вида '<проект> <реальный контактный email>'"
            )
        if component in ("api", "bot") and not self.database_url and not self.postgres_password:
            problems.append("POSTGRES_PASSWORD (или DATABASE_URL) не задан")
        if component == "bot":
            if not self.telegram_bot_token:
                problems.append("TELEGRAM_BOT_TOKEN не задан")
            if not self.telegram_allowed_ids and not self.telegram_allow_public:
                problems.append(
                    "TELEGRAM_ALLOWED_IDS пуст: укажите Telegram ID через запятую "
                    "(или явно TELEGRAM_ALLOW_PUBLIC=true для открытого доступа)"
                )
        return problems

    def require(self, component: Literal["api", "bot", "smoke"]) -> None:
        """Останавливает запуск с понятным перечнем ошибок конфигурации."""
        problems = self.missing_required(component)
        if problems:
            raise ConfigError(
                f"Конфигурация ({component}) неполная, см. .env:\n- " + "\n- ".join(problems)
            )


class ConfigError(RuntimeError):
    """Не заданы обязательные параметры окружения."""


@lru_cache
def get_settings() -> Settings:
    """Кэшированный singleton настроек."""
    return Settings()
