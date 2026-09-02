"""Database URL helpers for Gateway persistence."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.engine import make_url

_INVALID_URL_MSG = "APME_DATABASE_URL must be a SQLAlchemy URL"
_EXPLICIT_URL_MSG = "database_url must be a SQLAlchemy URL"
_MISSING_URL_MSG = "APME_DATABASE_URL is required (postgresql+asyncpg://user:pass@host:5432/dbname)"
_SUPPORTED_ASYNC_DRIVERS = frozenset({"postgresql+asyncpg"})
_SENSITIVE_QUERY_KEYS = frozenset({"password", "passwd", "pass", "secret", "token", "api_key", "access_token"})


def is_database_url(target: str) -> bool:
    """Return True when *target* looks like a SQLAlchemy database URL.

    Args:
        target: Database URL.

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


def resolve_database_url(*, database_url: str | None = None) -> str:
    """Resolve the SQLAlchemy URL from explicit config or environment.

    ``APME_DATABASE_URL`` is required when *database_url* is not passed.

    Args:
        database_url: Optional explicit SQLAlchemy URL (e.g. ``postgresql+asyncpg://...``).

    Returns:
        SQLAlchemy async database URL.

    Raises:
        ValueError: When no database URL is configured or the URL is invalid.
    """  # noqa: DOC502
    if database_url:
        return _validate_async_database_url(database_url, error_msg=_EXPLICIT_URL_MSG)
    env_url = os.environ.get("APME_DATABASE_URL", "").strip()
    if env_url:
        return _validate_async_database_url(env_url, error_msg=_INVALID_URL_MSG)
    raise ValueError(_MISSING_URL_MSG)


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
