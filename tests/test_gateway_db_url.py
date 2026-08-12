"""Unit tests for gateway database URL helpers."""

from __future__ import annotations

import pytest

from apme_gateway.db.url import DatabaseUrlError, resolve_database_url, sanitize_database_url


def test_resolve_database_url_prefers_explicit_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit database_url wins over environment.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_DATABASE_URL", raising=False)
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
    """Missing URL raises a clear error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("APME_DATABASE_URL", raising=False)
    with pytest.raises(DatabaseUrlError, match="APME_DATABASE_URL is required"):
        resolve_database_url()


def test_sanitize_database_url_redacts_password() -> None:
    """Logged URLs must not include credentials."""
    raw = "postgresql+asyncpg://apme:secret@postgres:5432/apme"
    assert sanitize_database_url(raw) == "postgresql+asyncpg://apme:[REDACTED]@postgres:5432/apme"
