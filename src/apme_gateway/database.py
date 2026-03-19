"""Async database engine and session factory.

Defaults to SQLite (aiosqlite) for single-pod/dev use.
Set APME_DB_URL to a PostgreSQL DSN for multi-pod/enterprise deployments.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.models.database import Base

_DEFAULT_DB_URL = "sqlite+aiosqlite:///apme_gateway.db"

engine = create_async_engine(
    os.getenv("APME_DB_URL", _DEFAULT_DB_URL),
    echo=False,
    future=True,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async session."""
    async with async_session() as session:
        yield session  # type: ignore[misc]
