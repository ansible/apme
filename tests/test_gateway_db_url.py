"""Unit tests for gateway database URL helpers."""

from __future__ import annotations

from apme_gateway.db.url import (
    is_database_url,
    is_sqlite_url,
    resolve_database_url,
    sanitize_database_url,
    sqlite_url_from_path,
)


def test_sqlite_url_from_path() -> None:
    """Filesystem paths map to async SQLite URLs."""
    assert sqlite_url_from_path("/data/apme.db") == "sqlite+aiosqlite:////data/apme.db"


def test_resolve_database_url_prefers_explicit_url() -> None:
    """Explicit database_url wins over db_path."""
    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db:5432/apme",
        db_path="/ignored.db",
    )
    assert url == "postgresql+asyncpg://user:pass@db:5432/apme"


def test_resolve_database_url_falls_back_to_sqlite_path() -> None:
    """Without a URL, db_path is used for SQLite."""
    assert resolve_database_url(db_path="/tmp/test.db") == "sqlite+aiosqlite:////tmp/test.db"


def test_sanitize_database_url_redacts_password() -> None:
    """Logged URLs must not include credentials."""
    raw = "postgresql+asyncpg://apme:secret@postgres:5432/apme"
    assert sanitize_database_url(raw) == "postgresql+asyncpg://apme:[REDACTED]@postgres:5432/apme"


def test_is_database_url_and_sqlite_detection() -> None:
    """URL helpers distinguish SQLite URLs from filesystem paths."""
    assert not is_database_url("/data/apme.db")
    assert is_sqlite_url("/data/apme.db")
    assert is_sqlite_url("sqlite+aiosqlite:////data/apme.db")
    assert not is_sqlite_url("postgresql+asyncpg://localhost/apme")
