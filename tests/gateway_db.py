"""Shared database fixtures for gateway unit tests."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from urllib.parse import unquote, urlparse, urlunparse

import pytest

from apme_gateway.db import close_db, init_db, reset_db
from apme_gateway.db.url import _require_secure_transport, asyncpg_ssl_connect_arg
from apme_gateway.operation_registry import get_operation_registry

_WORKER_NAME_RE = re.compile(r"^(master|gw\d+)$")
_DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://apme:apme@localhost:5432/apme_test"


def _validated_worker_suffix() -> str:
    """Return a safe database-name suffix for the current xdist worker.

    Returns:
        Sanitized worker suffix for PostgreSQL database names.

    Raises:
        ValueError: When ``PYTEST_XDIST_WORKER`` is not a recognized xdist name.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    if not _WORKER_NAME_RE.fullmatch(worker):
        msg = f"Invalid PYTEST_XDIST_WORKER: {worker!r}"
        raise ValueError(msg)
    return worker.replace("-", "_")


def base_test_database_url() -> str:
    """Return the configured PostgreSQL URL for gateway tests.

    Returns:
        Base PostgreSQL connection URL from ``APME_TEST_DATABASE_URL``, or the
        local default when unset.
    """
    url = os.environ.get("APME_TEST_DATABASE_URL", "").strip()
    return url or _DEFAULT_TEST_DATABASE_URL


def worker_database_name() -> str:
    """Return an isolated database name for the current pytest-xdist worker.

    Returns:
        Worker-specific database name.
    """
    return f"apme_test_{_validated_worker_suffix()}"


def test_database_url() -> str:
    """Return the PostgreSQL URL for the current test worker.

    Returns:
        Worker-specific PostgreSQL connection URL.
    """
    parsed = urlparse(base_test_database_url())
    return urlunparse(parsed._replace(path=f"/{worker_database_name()}"))


async def ensure_worker_database() -> str:
    """Create the worker database if needed and return its URL.

    Returns:
        Worker-specific PostgreSQL connection URL.
    """
    import asyncpg

    base_url = base_test_database_url()
    _require_secure_transport(base_url)
    parsed = urlparse(base_url.replace("postgresql+asyncpg://", "postgresql://"))
    db_name = worker_database_name()
    connect_kwargs: dict[str, object] = {
        "user": parsed.username or "apme",
        "password": unquote(parsed.password) if parsed.password is not None else "apme",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": "postgres",
    }
    ssl_arg = asyncpg_ssl_connect_arg(base_url)
    if ssl_arg is not None:
        connect_kwargs["ssl"] = ssl_arg
    conn = await asyncpg.connect(**connect_kwargs)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    return test_database_url()


@pytest.fixture  # type: ignore[untyped-decorator]
async def gateway_db() -> AsyncIterator[None]:
    """Initialise a fresh PostgreSQL schema per test.

    Yields:
        None: Test runs between setup and teardown.
    """
    url = await ensure_worker_database()
    await close_db()
    await init_db(url)
    await reset_db()
    yield
    registry = get_operation_registry()
    await registry.shutdown()
    await close_db()
