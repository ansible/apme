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
        database_url="postgresql+asyncpg://user:pass@127.0.0.1:5432/apme",
    )
    assert url == "postgresql+asyncpg://user:pass@127.0.0.1:5432/apme"


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


def test_resolve_database_url_rejects_remote_without_tls() -> None:
    """Remote PostgreSQL URLs must declare TLS."""
    with pytest.raises(ValueError, match="must use TLS"):
        resolve_database_url(database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme")


def test_resolve_database_url_allows_loopback_without_tls() -> None:
    """Loopback PostgreSQL URLs do not require explicit TLS."""
    url = resolve_database_url(database_url="postgresql+asyncpg://apme:apme@127.0.0.1:5432/apme")
    assert url == "postgresql+asyncpg://apme:apme@127.0.0.1:5432/apme"


def test_resolve_database_url_allows_remote_with_sslmode_require() -> None:
    """Remote PostgreSQL URLs map sslmode=require to asyncpg ssl=require."""
    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?sslmode=require",
    )
    assert "ssl=require" in url
    assert "sslmode=" not in url


def test_resolve_database_url_allows_remote_with_ssl_true() -> None:
    """Remote PostgreSQL URLs map ssl=true to asyncpg ssl=require."""
    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?ssl=true",
    )
    assert "ssl=require" in url
    assert "ssl=true" not in url


def test_resolve_database_url_maps_sslmode_verify_full() -> None:
    """Remote PostgreSQL URLs preserve verify-full as asyncpg ssl mode."""
    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?sslmode=verify-full",
    )
    assert "ssl=verify-full" in url
    assert "sslmode=" not in url


def test_resolve_database_url_asyncpg_connect_args_accept_sslmode_require() -> None:
    """Resolved URLs must not pass unsupported sslmode to asyncpg.connect."""
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.engine import make_url

    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?sslmode=require",
    )
    dialect = PGDialect_asyncpg()
    _, connect_args = dialect.create_connect_args(make_url(url))
    assert connect_args.get("ssl") == "require"
    assert "sslmode" not in connect_args


def test_resolve_database_url_asyncpg_connect_args_accept_ssl_true() -> None:
    """Resolved URLs must map boolean ssl=true to asyncpg ssl=require."""
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.engine import make_url

    url = resolve_database_url(
        database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?ssl=true",
    )
    dialect = PGDialect_asyncpg()
    _, connect_args = dialect.create_connect_args(make_url(url))
    assert connect_args.get("ssl") == "require"
    assert connect_args.get("ssl") != "true"


def test_resolve_database_url_rejects_conflicting_tls_parameters() -> None:
    """Conflicting sslmode and ssl query parameters are rejected."""
    with pytest.raises(ValueError, match="Conflicting TLS parameters"):
        resolve_database_url(
            database_url="postgresql+asyncpg://user:pass@db.example.com:5432/apme?sslmode=disable&ssl=true",
        )


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
