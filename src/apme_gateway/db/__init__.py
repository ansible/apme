"""Database engine and session factory for the gateway."""

from __future__ import annotations

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _set_sqlite_pragma(
    dbapi_connection: object,
    _connection_record: object,
) -> None:
    """Enable SQLite foreign key enforcement on every new connection.

    Args:
        dbapi_connection: Raw DBAPI connection from the pool.
        _connection_record: SQLAlchemy connection record (unused).
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def init_db(db_path: str) -> None:
    """Create the async engine, run DDL, and configure the session factory.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    global _engine, _session_factory  # noqa: PLW0603
    url = f"sqlite+aiosqlite:///{db_path}"
    _engine = create_async_engine(url, echo=False)
    event.listen(_engine.sync_engine, "connect", _set_sqlite_pragma)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_violations_table)
        await conn.run_sync(_migrate_proposals_table)


def _migrate_violations_table(conn: object) -> None:
    """Add columns introduced after the initial schema.

    ``create_all`` only creates missing *tables* — it does not add columns
    to existing tables.  This function inspects the ``violations`` table
    and issues ``ALTER TABLE ADD COLUMN`` for any that are missing.

    Args:
        conn: Synchronous SQLAlchemy connection (from ``run_sync``).
    """
    from sqlalchemy.engine import Connection  # noqa: PLC0415

    if not isinstance(conn, Connection):
        return
    insp = inspect(conn)
    if not insp.has_table("violations"):
        return
    existing = {c["name"] for c in insp.get_columns("violations")}

    migrations: list[str] = []
    if "original_yaml" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN original_yaml TEXT NOT NULL DEFAULT ''")
    if "fixed_yaml" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN fixed_yaml TEXT NOT NULL DEFAULT ''")
    if "co_fixes" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN co_fixes TEXT NOT NULL DEFAULT ''")
    if "node_line_start" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN node_line_start INTEGER NOT NULL DEFAULT 0")
    if "remediation_resolution" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN remediation_resolution INTEGER NOT NULL DEFAULT 0")
    if "ai_reason" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN ai_reason TEXT NOT NULL DEFAULT ''")
    if "ai_suggestion" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN ai_suggestion TEXT NOT NULL DEFAULT ''")

    for stmt in migrations:
        conn.execute(text(stmt))


def _migrate_proposals_table(conn: object) -> None:
    """Add ADR-062 columns to ``proposals`` and ``violations``.

    Args:
        conn: Synchronous SQLAlchemy connection (from ``run_sync``).
    """
    from sqlalchemy.engine import Connection  # noqa: PLC0415

    if not isinstance(conn, Connection):
        return
    insp = inspect(conn)

    if insp.has_table("violations"):
        existing_v = {c["name"] for c in insp.get_columns("violations")}
        if "review_status" not in existing_v:
            conn.execute(text("ALTER TABLE violations ADD COLUMN review_status TEXT DEFAULT NULL"))

    if not insp.has_table("proposals"):
        return
    existing = {c["name"] for c in insp.get_columns("proposals")}
    migrations: list[str] = []
    if "path" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN path TEXT NOT NULL DEFAULT ''")
    if "source" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN source TEXT NOT NULL DEFAULT 'outcome'")
    if "gate" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN gate TEXT NOT NULL DEFAULT ''")
    if "rule_ids_json" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN rule_ids_json TEXT NOT NULL DEFAULT '[]'")
    if "violation_ids_json" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN violation_ids_json TEXT NOT NULL DEFAULT '[]'")
    if "line_start" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN line_start INTEGER NOT NULL DEFAULT 0")
    if "diff_hunk" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN diff_hunk TEXT NOT NULL DEFAULT ''")
    if "explanation" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN explanation TEXT NOT NULL DEFAULT ''")
    if "suggestion" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN suggestion TEXT NOT NULL DEFAULT ''")
    if "analytics_flushed" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN analytics_flushed INTEGER NOT NULL DEFAULT 0")

    for stmt in migrations:
        conn.execute(text(stmt))


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
