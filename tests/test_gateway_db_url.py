"""Unit tests for gateway database URL helpers."""

from __future__ import annotations

import pytest

from apme_gateway.db.url import (
    is_database_url,
    resolve_database_url,
    sanitize_database_url,
)


def test_resolve_database_url_prefers_explicit_url() -> None:
    """Explicit database_url wins over environment."""
    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db:5432/apme",
    )
    assert url == "postgresql+asyncpg://user:pass@db:5432/apme"


def test_resolve_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """APME_DATABASE_URL is used when no explicit URL is passed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_DATABASE_URL", "postgresql+asyncpg://apme:apme@localhost:5432/apme")
    assert resolve_database_url() == "postgresql+asyncpg://apme:apme@localhost:5432/apme"


def test_resolve_database_url_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing APME_DATABASE_URL raises a clear error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="APME_DATABASE_URL is required"):
        resolve_database_url()


def test_resolve_database_url_rejects_malformed_explicit_url() -> None:
    """Malformed explicit database_url raises without echoing the value."""
    with pytest.raises(ValueError, match="database_url must be a SQLAlchemy URL"):
        resolve_database_url(database_url="not-a-url")


def test_resolve_database_url_rejects_sync_postgresql_driver() -> None:
    """Synchronous postgresql:// URLs are rejected at the config boundary."""
    with pytest.raises(ValueError, match="database_url must be a SQLAlchemy URL"):
        resolve_database_url(database_url="postgresql://user:pass@db:5432/apme")


def test_resolve_database_url_rejects_unsupported_async_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only postgresql+asyncpg driver is accepted.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_DATABASE_URL", "mysql+aiomysql://user:pass@db/apme")
    with pytest.raises(ValueError, match="APME_DATABASE_URL must be a SQLAlchemy URL"):
        resolve_database_url()


def test_resolve_database_url_rejects_malformed_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed APME_DATABASE_URL raises without echoing the value.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_DATABASE_URL", "not-a-url")
    with pytest.raises(ValueError, match="APME_DATABASE_URL must be a SQLAlchemy URL"):
        resolve_database_url()


def test_sanitize_database_url_redacts_password() -> None:
    """Logged URLs must not include credentials."""
    raw = "postgresql+asyncpg://apme:secret@postgres:5432/apme"
    assert sanitize_database_url(raw) == "postgresql+asyncpg://apme:[REDACTED]@postgres:5432/apme"


def test_sanitize_database_url_redacts_query_password() -> None:
    """Query-string credentials must not appear in logged URLs."""
    raw = "postgresql+asyncpg://db/apme?password=secret"
    assert sanitize_database_url(raw) == "postgresql+asyncpg://db/apme?password=[REDACTED]"


def test_is_database_url() -> None:
    """URL helper recognizes SQLAlchemy URLs."""
    assert is_database_url("postgresql+asyncpg://localhost/apme")
    assert not is_database_url("/data/apme.db")
