"""Dialect-specific SQLAlchemy helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.sql.dml import Insert

_SQLITE_IN_CLAUSE_CHUNK = 900
_POSTGRESQL_IN_CLAUSE_CHUNK = 30_000


def dialect_insert(engine: Engine, table: Any) -> Insert:
    """Return the dialect-appropriate ``INSERT`` construct for *table*.

    Args:
        engine: Bound SQLAlchemy engine.
        table: ORM model class or table object.

    Returns:
        Dialect-specific insert statement builder.
    """
    if engine.dialect.name == "postgresql":
        return postgresql_insert(table)
    return sqlite_insert(table)


def in_clause_chunk_size(engine: Engine) -> int:
    """Return a safe ``IN`` clause size for the engine dialect.

    Args:
        engine: Bound SQLAlchemy engine.

    Returns:
        Maximum number of bind parameters per ``IN`` chunk.
    """
    if engine.dialect.name == "postgresql":
        return _POSTGRESQL_IN_CLAUSE_CHUNK
    return _SQLITE_IN_CLAUSE_CHUNK
