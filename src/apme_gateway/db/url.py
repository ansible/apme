"""Database URL helpers for Gateway persistence."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


class DatabaseUrlError(ValueError):
    """Raised when the gateway database URL is missing or invalid."""


def resolve_database_url(*, database_url: str | None = None) -> str:
    """Resolve the SQLAlchemy URL from explicit config or ``APME_DATABASE_URL``.

    Args:
        database_url: Optional explicit SQLAlchemy URL (e.g. ``postgresql+asyncpg://...``).

    Returns:
        SQLAlchemy async database URL.

    Raises:
        DatabaseUrlError: When no database URL is configured.
    """
    url = (database_url or os.environ.get("APME_DATABASE_URL", "")).strip()
    if not url:
        msg = "APME_DATABASE_URL is required (postgresql+asyncpg://user:pass@host:5432/dbname)"
        raise DatabaseUrlError(msg)
    if "://" not in url:
        msg = f"APME_DATABASE_URL must be a SQLAlchemy URL, got: {url!r}"
        raise DatabaseUrlError(msg)
    return url


def sanitize_database_url(url: str) -> str:
    """Redact credentials from a database URL for logging.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        URL with password replaced by ``[REDACTED]`` when present.
    """
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}:[REDACTED]@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
