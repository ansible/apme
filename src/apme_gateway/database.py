"""Async SQLAlchemy engine and session factory for the gateway."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.config import GatewayConfig

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(config: GatewayConfig) -> None:
    """Create the async engine and run DDL for all models.

    Args:
        config: Gateway configuration with db_url.
    """
    global _engine, _session_factory  # noqa: PLW0603

    if config.db_url.startswith("sqlite"):
        import os

        db_path = config.db_url.split("///")[-1]
        if db_path:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    _engine = create_async_engine(config.db_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    from apme_gateway.models.tables import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if config.db_url.startswith("sqlite"):
        await _migrate_sqlite(_engine)


async def _migrate_sqlite(engine: AsyncEngine) -> None:
    """Add columns that may be missing from an older schema."""
    import contextlib

    import sqlalchemy

    migrations = [
        ("scans", "source", "TEXT NOT NULL DEFAULT 'engine'"),
        ("scans", "summary_json", "TEXT"),
    ]

    async with engine.begin() as conn:
        for table, column, col_type in migrations:
            with contextlib.suppress(Exception):
                await conn.execute(sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, raising if the DB is not initialised.

    Returns:
        The async session factory.

    Raises:
        RuntimeError: If init_db has not been called.
    """
    if _session_factory is None:
        msg = "Database not initialised — call init_db() first"
        raise RuntimeError(msg)
    return _session_factory
