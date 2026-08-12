"""Database URL helpers for Gateway persistence."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def is_database_url(target: str) -> bool:
    """Return True when *target* looks like a SQLAlchemy database URL.

    Args:
        target: Filesystem path or database URL.

    Returns:
        True if the value contains a URL scheme.
    """
    return "://" in target


def sqlite_url_from_path(db_path: str) -> str:
    """Build a SQLAlchemy async SQLite URL from a filesystem path.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        ``sqlite+aiosqlite:///{db_path}`` URL.
    """
    return f"sqlite+aiosqlite:///{db_path}"


def resolve_database_url(*, database_url: str | None = None, db_path: str | None = None) -> str:
    """Resolve the SQLAlchemy URL from explicit config or environment defaults.

    ``APME_DATABASE_URL`` takes precedence when *database_url* is not passed.
    Otherwise falls back to ``APME_DB_PATH`` (or */data/apme.db*) as SQLite.

    Args:
        database_url: Optional explicit SQLAlchemy URL (e.g. ``postgresql+asyncpg://...``).
        db_path: Optional SQLite file path when no URL is configured.

    Returns:
        SQLAlchemy async database URL.

    Raises:
        ValueError: When ``APME_DATABASE_URL`` is set but not a valid URL.
    """
    if database_url:
        return database_url
    env_url = os.environ.get("APME_DATABASE_URL", "").strip()
    if env_url:
        if "://" not in env_url:
            msg = "APME_DATABASE_URL must be a SQLAlchemy URL"
            raise ValueError(msg)
        return env_url
    path = db_path if db_path is not None else os.environ.get("APME_DB_PATH", "/data/apme.db")
    return sqlite_url_from_path(path)


def sanitize_database_url(url: str) -> str:
    """Redact credentials from a database URL for logging.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        URL with password replaced by ``[REDACTED]`` when present.
    """
    if not is_database_url(url):
        return url
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}:[REDACTED]@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def is_sqlite_url(url: str) -> bool:
    """Return True when *url* targets SQLite.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        True for ``sqlite`` dialect URLs.
    """
    if not is_database_url(url):
        return True
    return urlsplit(url).scheme.startswith("sqlite")


def sqlite_parent_dir(url: str) -> str | None:
    """Return the parent directory for a file-backed SQLite URL.

    Args:
        url: Resolved SQLAlchemy database URL.

    Returns:
        Parent directory path, or ``None`` for in-memory SQLite or non-SQLite URLs.
    """
    if not is_sqlite_url(url) or not is_database_url(url):
        return None
    if ":memory:" in url:
        return None
    path = urlsplit(url).path
    if not path or path.endswith(":memory:"):
        return None
    parent = os.path.dirname(path)
    return parent or None
