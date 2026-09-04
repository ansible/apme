"""Database engine and session factory for the gateway."""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.db.dialect import in_clause_chunk_size
from apme_gateway.db.models import Base
from apme_gateway.db.url import resolve_database_url

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_database_url: str | None = None
_DB_INIT_MAX_ATTEMPTS = 30
_DB_INIT_RETRY_DELAY_S = 1.0


async def init_db(database_url: str) -> None:
    """Create the async engine, run DDL, and configure the session factory.

    Retries transient connection failures while PostgreSQL starts (for example
    during pod bring-up).

    Args:
        database_url: SQLAlchemy database URL (``postgresql+asyncpg://...``).

    Raises:
        OperationalError: When the database is unreachable after all retries.
        OSError: When a connection attempt fails after all retries.
    """
    global _engine, _session_factory, _database_url  # noqa: PLW0603
    url = resolve_database_url(database_url=database_url)
    _database_url = url
    for attempt in range(1, _DB_INIT_MAX_ATTEMPTS + 1):
        engine: AsyncEngine | None = None
        try:
            engine = create_async_engine(url, echo=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_migrate_violations_table)
                await conn.run_sync(_migrate_proposals_table)
                await conn.run_sync(_migrate_scans_table)
        except (OperationalError, OSError):
            if engine is not None:
                await engine.dispose()
            if attempt < _DB_INIT_MAX_ATTEMPTS:
                await asyncio.sleep(_DB_INIT_RETRY_DELAY_S)
                continue
            raise
        else:
            _engine = engine
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
            return


async def init_db_from_config(*, database_url: str | None = None) -> str:
    """Initialize the database from gateway configuration values.

    Args:
        database_url: Optional explicit SQLAlchemy URL.

    Returns:
        Resolved database URL used for the engine.
    """
    url = resolve_database_url(database_url=database_url)
    await init_db(url)
    return url


async def reset_db() -> None:
    """Drop all tables and recreate schema (test helper).

    Raises:
        RuntimeError: If init_db has not been called.
    """
    if _engine is None:
        msg = "Database not initialised — call init_db() first"
        raise RuntimeError(msg)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_violations_table)
        await conn.run_sync(_migrate_proposals_table)
        await conn.run_sync(_migrate_scans_table)


def get_engine() -> AsyncEngine:
    """Return the active async engine.

    Returns:
        Current AsyncEngine instance.

    Raises:
        RuntimeError: If init_db has not been called.
    """
    if _engine is None:
        msg = "Database not initialised — call init_db() first"
        raise RuntimeError(msg)
    return _engine


def get_in_clause_chunk_size() -> int:
    """Return the safe ``IN`` clause chunk size for the active database.

    Returns:
        Chunk size for the current engine dialect, or PostgreSQL default before init.
    """
    if _engine is None:
        return 30_000
    return in_clause_chunk_size(_engine.sync_engine)


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
    if "node_type" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN node_type TEXT NOT NULL DEFAULT ''")
    if "remediation_resolution" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN remediation_resolution INTEGER NOT NULL DEFAULT 0")
    if "ai_reason" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN ai_reason TEXT NOT NULL DEFAULT ''")
    if "ai_suggestion" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN ai_suggestion TEXT NOT NULL DEFAULT ''")
    if "audit_metadata" not in existing:
        migrations.append("ALTER TABLE violations ADD COLUMN audit_metadata TEXT NOT NULL DEFAULT ''")

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
    if "node_type" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN node_type TEXT NOT NULL DEFAULT ''")
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
    if "engine_proposal_id" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN engine_proposal_id TEXT DEFAULT NULL")
    if "draft" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN draft INTEGER NOT NULL DEFAULT 0")
    if "stamp_rule_ids_json" not in existing:
        migrations.append("ALTER TABLE proposals ADD COLUMN stamp_rule_ids_json TEXT NOT NULL DEFAULT '[]'")

    for stmt in migrations:
        conn.execute(text(stmt))


def _migrate_scans_table(conn: object) -> None:
    """Add SCM publish columns to ``scans`` (ADR-050).

    ``create_all`` only creates missing *tables* — it does not add columns
    to existing tables.  This function inspects the ``scans`` table and
    issues ``ALTER TABLE ADD COLUMN`` for any that are missing.

    Args:
        conn: Synchronous SQLAlchemy connection (from ``run_sync``).
    """
    from sqlalchemy.engine import Connection  # noqa: PLC0415

    if not isinstance(conn, Connection):
        return
    insp = inspect(conn)
    if not insp.has_table("scans"):
        return
    existing = {c["name"] for c in insp.get_columns("scans")}

    migrations: list[str] = []
    if "pr_url" not in existing:
        migrations.append("ALTER TABLE scans ADD COLUMN pr_url TEXT DEFAULT NULL")
    if "branch_name" not in existing:
        migrations.append("ALTER TABLE scans ADD COLUMN branch_name TEXT DEFAULT NULL")
    if "commit_sha" not in existing:
        migrations.append("ALTER TABLE scans ADD COLUMN commit_sha TEXT DEFAULT NULL")

    for stmt in migrations:
        conn.execute(text(stmt))


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory, _database_url  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _database_url = None


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
