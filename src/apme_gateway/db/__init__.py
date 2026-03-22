"""Database engine and session factory for the gateway."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(db_path: str) -> None:
    """Create the async engine, run DDL, and configure the session factory.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    global _engine, _session_factory  # noqa: PLW0603
    url = f"sqlite+aiosqlite:///{db_path}"
    _engine = create_async_engine(url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_session() -> AsyncSession:
    """Return a new async session from the factory.

    Returns:
        An AsyncSession bound to the current engine.

    Raises:
        RuntimeError: If init_db has not been called.
    """
    if _session_factory is None:
        msg = "Database not initialised — call init_db() first"
        raise RuntimeError(msg)
    return _session_factory()
