"""Слой ingestion: клиенты источников данных и маршрутизатор (ТЗ раздел 6)."""

from app.ingestion.source_router import SourceRouter, build_source_router

__all__ = ["SourceRouter", "build_source_router"]
