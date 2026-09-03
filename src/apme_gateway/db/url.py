"""Database URL helpers for Gateway persistence."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.engine import URL, make_url

_INVALID_URL_MSG = "APME_DATABASE_URL must be a SQLAlchemy URL"
_EXPLICIT_URL_MSG = "database_url must be a SQLAlchemy URL"
_MISSING_URL_MSG = "APME_DATABASE_URL is required (postgresql+asyncpg://user:pass@host:5432/dbname)"
_SUPPORTED_ASYNC_DRIVERS = frozenset({"postgresql+asyncpg"})
_SENSITIVE_QUERY_KEYS = frozenset({"password", "passwd", "pass", "secret", "token", "api_key", "access_token"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_CERT_VALIDATED_SSLMODES = frozenset({"verify-full"})
_REMOTE_TLS_REQUIRED_MSG = (
    "APME_DATABASE_URL must use certificate-validated TLS (sslmode=verify-full) for non-loopback hosts"
)
_HOST_QUERY_OVERRIDE_MSG = "APME_DATABASE_URL must not use a host query parameter to override the authority host"
_UNSUPPORTED_SSLMODE_MSG = "Unsupported sslmode in APME_DATABASE_URL"
_UNSUPPORTED_SSL_MSG = "Unsupported ssl parameter in APME_DATABASE_URL"
_CONFLICTING_TLS_MSG = "Conflicting TLS parameters in APME_DATABASE_URL"
_SSLMODE_TO_ASYNCPG = {
    "disable": "disable",
    "allow": "allow",
    "prefer": "prefer",
    "require": "require",
    "verify-ca": "verify-ca",
    "verify-full": "verify-full",
}
_BOOLEAN_SSL_TO_ASYNCPG = {
    "1": "require",
    "true": "require",
    "yes": "require",
    "require": "require",
    "0": "disable",
    "false": "disable",
    "no": "disable",
    "disable": "disable",
}


def _is_loopback_host(host: str | None) -> bool:
    """Return True when *host* is a loopback address.

    Args:
        host: PostgreSQL hostname from the database URL.

    Returns:
        True for localhost and loopback IP literals.
    """
    if not host:
        return False
    return host.lower().strip("[]") in _LOOPBACK_HOSTS


def _normalize_asyncpg_ssl_query(query: dict[str, str]) -> dict[str, str]:
    """Map libpq-style sslmode/ssl query params to asyncpg-compatible ssl values.

    Args:
        query: URL query parameters from a SQLAlchemy database URL.

    Returns:
        Query parameters with ``sslmode`` removed and ``ssl`` set for asyncpg.

    Raises:
        ValueError: When sslmode or ssl values are unsupported.
    """
    if "sslmode" not in query and "ssl" not in query:
        return query
    normalized = dict(query)
    ssl_value: str | None = None
    if "sslmode" in normalized:
        mode = str(normalized.pop("sslmode")).lower()
        mapped = _SSLMODE_TO_ASYNCPG.get(mode)
        if mapped is None:
            raise ValueError(_UNSUPPORTED_SSLMODE_MSG)
        ssl_value = mapped
    if "ssl" in normalized:
        raw_ssl = str(normalized.pop("ssl")).lower()
        if raw_ssl in _BOOLEAN_SSL_TO_ASYNCPG:
            mapped_ssl = _BOOLEAN_SSL_TO_ASYNCPG[raw_ssl]
        elif raw_ssl in _SSLMODE_TO_ASYNCPG.values():
            mapped_ssl = raw_ssl
        else:
            raise ValueError(_UNSUPPORTED_SSL_MSG)
        if ssl_value is None:
            ssl_value = mapped_ssl
        elif ssl_value != mapped_ssl:
            raise ValueError(_CONFLICTING_TLS_MSG)
    if ssl_value is not None:
        normalized["ssl"] = ssl_value
    return normalized


def _normalize_database_url(url: str) -> str:
    """Return *url* with asyncpg-compatible SSL query parameters.

    Args:
        url: Validated SQLAlchemy database URL.

    Returns:
        URL with libpq ``sslmode`` and boolean ``ssl`` values mapped for asyncpg.
    """
    parsed = make_url(url)
    query = dict(parsed.query)
    if not query:
        return url
    normalized_query = _normalize_asyncpg_ssl_query(query)
    if normalized_query == query:
        return url
    return str(parsed.set(query=normalized_query))


def _reject_host_query_override(parsed: URL) -> None:
    """Reject libpq-style host query overrides that bypass TLS validation.

    Args:
        parsed: SQLAlchemy URL object from ``make_url``.

    Raises:
        ValueError: When the query string overrides the authority host.
    """
    query = dict(parsed.query)
    if query.get("host"):
        raise ValueError(_HOST_QUERY_OVERRIDE_MSG)


def _require_secure_transport(url: str) -> None:
    """Require TLS for remote PostgreSQL URLs.

    Args:
        url: Validated SQLAlchemy database URL.

    Raises:
        ValueError: When a remote host omits TLS configuration.
    """
    parsed = make_url(url)
    _reject_host_query_override(parsed)
    if _is_loopback_host(parsed.host):
        return
    query = dict(parsed.query)
    sslmode = str(query.get("sslmode", "")).lower()
    ssl = str(query.get("ssl", "")).lower()
    if sslmode in _CERT_VALIDATED_SSLMODES or ssl in _CERT_VALIDATED_SSLMODES:
        return
    raise ValueError(_REMOTE_TLS_REQUIRED_MSG)


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


def asyncpg_ssl_connect_arg(url: str) -> str | None:
    """Return the asyncpg ``ssl`` connect argument from a database URL.

    Args:
        url: SQLAlchemy database URL (``postgresql+asyncpg://...``).

    Returns:
        Normalized asyncpg ``ssl`` mode when present in the URL query string.
    """
    parsed = make_url(url)
    query = dict(parsed.query)
    if not query:
        return None
    normalized = _normalize_asyncpg_ssl_query(query)
    return normalized.get("ssl")


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
        validated = _validate_async_database_url(database_url, error_msg=_EXPLICIT_URL_MSG)
        _require_secure_transport(validated)
        return _normalize_database_url(validated)
    env_url = os.environ.get("APME_DATABASE_URL", "").strip()
    if env_url:
        validated = _validate_async_database_url(env_url, error_msg=_INVALID_URL_MSG)
        _require_secure_transport(validated)
        return _normalize_database_url(validated)
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
