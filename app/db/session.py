"""Асинхронный движок и фабрика сессий SQLAlchemy (ТЗ раздел 4)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db.models import Base


def create_engine(url: str | None = None, **kwargs) -> AsyncEngine:
    """Создаёт async-движок из настроек (или переданного URL)."""
    return create_async_engine(url or get_settings().async_database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    """Создаёт все таблицы (для тестов/локальной разработки; в проде — Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
