"""Shared PostgreSQL fixtures for gateway unit tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from apme_gateway.db import close_db, init_db, reset_db

_DEFAULT_BASE_URL = "postgresql+asyncpg://apme:apme@localhost:5432/apme_test"


def base_test_database_url() -> str:
    """Return the configured base PostgreSQL URL for tests.

    Returns:
        Base PostgreSQL connection URL from environment or default.
    """
    return os.environ.get("APME_TEST_DATABASE_URL", _DEFAULT_BASE_URL).strip()


def worker_database_name() -> str:
    """Return an isolated database name for the current pytest-xdist worker.

    Returns:
        Worker-specific database name.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    return f"apme_test_{worker.replace('-', '_')}"


def test_database_url() -> str:
    """Return the PostgreSQL URL for the current test worker.

    Returns:
        Worker-specific PostgreSQL connection URL.
    """
    parsed = urlparse(base_test_database_url().replace("postgresql+asyncpg://", "postgresql://"))
    return urlunparse(parsed._replace(path=f"/{worker_database_name()}"))


async def ensure_worker_database() -> str:
    """Create the worker database if needed and return its URL.

    Returns:
        Worker-specific PostgreSQL connection URL.
    """
    parsed = urlparse(base_test_database_url().replace("postgresql+asyncpg://", "postgresql://"))
    db_name = worker_database_name()
    conn = await asyncpg.connect(
        user=parsed.username or "apme",
        password=parsed.password or "apme",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    return test_database_url()


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def gateway_test_database_url() -> str:
    """Session-scoped worker database URL (created once per xdist worker).

    Returns:
        Worker-specific PostgreSQL connection URL.
    """
    import asyncio

    return asyncio.run(ensure_worker_database())


@pytest.fixture  # type: ignore[untyped-decorator]
async def gateway_db(gateway_test_database_url: str) -> AsyncIterator[None]:
    """Initialise a fresh PostgreSQL schema per test.

    Args:
        gateway_test_database_url: Worker-specific database URL.

    Yields:
        None: Test runs between setup and teardown.
    """
    await close_db()
    await init_db(gateway_test_database_url)
    await reset_db()
    yield
    await close_db()
