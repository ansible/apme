"""Database URL helpers for Gateway persistence."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.engine import make_url

_INVALID_URL_MSG = "APME_DATABASE_URL must be a SQLAlchemy URL"
_EXPLICIT_URL_MSG = "database_url must be a SQLAlchemy URL"
_SUPPORTED_ASYNC_DRIVERS = frozenset({"postgresql+asyncpg", "sqlite+aiosqlite"})
_SENSITIVE_QUERY_KEYS = frozenset({"password", "passwd", "pass", "secret", "token", "api_key", "access_token"})


def is_database_url(target: str) -> bool:
    """Return True when *target* looks like a SQLAlchemy database URL.

    Args:
        target: Filesystem path or database URL.

    Returns:
        True if the value contains a URL scheme.
    """
    return "://" in target


def _validate_async_database_url(url: str, *, error_msg: str) -> str:
    """Return *url* when it uses a supported async SQLAlchemy driver.

    Args:
        url: Candidate SQLAlchemy database URL.
        error_msg: Message for invalid or unsupported URLs.

    Returns:
        The validated URL unchanged.

    Raises:
        ValueError: When *url* is malformed or uses an unsupported driver.
    """
    if "://" not in url:
        raise ValueError(error_msg)
    try:
        parsed = make_url(url)
    except Exception:
        raise ValueError(error_msg) from None
    if parsed.drivername not in _SUPPORTED_ASYNC_DRIVERS:
        raise ValueError(error_msg)
    return url


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
        ValueError: When a configured database URL is not a valid SQLAlchemy URL.
    """  # noqa: DOC502
    if database_url:
        return _validate_async_database_url(database_url, error_msg=_EXPLICIT_URL_MSG)
    env_url = os.environ.get("APME_DATABASE_URL", "").strip()
    if env_url:
        return _validate_async_database_url(env_url, error_msg=_INVALID_URL_MSG)
    path = db_path if db_path is not None else os.environ.get("APME_DB_PATH", "/data/apme.db")
    return sqlite_url_from_path(path)


def _redact_query_credentials(query: str) -> str:
    """Return *query* with sensitive parameter values replaced by ``[REDACTED]``.

    Args:
        query: URL query string without a leading ``?``.

    Returns:
        Sanitized query string, or the original when no sensitive keys are present.
    """
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return query
    redacted = [(key, "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_KEYS else value) for key, value in pairs]
    if redacted == pairs:
        return query
    return urlencode(redacted, safe="[]")


def sanitize_database_url(url: str) -> str:
    """Redact credentials from a database URL for logging.

    Args:
        url: SQLAlchemy database URL.

    Returns:
        URL with netloc and query credentials replaced by ``[REDACTED]`` when present.
    """
    if not is_database_url(url):
        return url
    parts = urlsplit(url)
    if parts.password:
        netloc = parts.hostname or ""
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        if parts.username:
            netloc = f"{parts.username}:[REDACTED]@{netloc}"
    else:
        netloc = parts.netloc
    query = _redact_query_credentials(parts.query)
    if netloc == parts.netloc and query == parts.query:
        return url
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


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
    try:
        db_path = make_url(url).database
    except Exception:
        return None
    if not db_path or db_path == ":memory:":
        return None
    parent = os.path.dirname(db_path)
    return parent or None
