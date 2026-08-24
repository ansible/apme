"""Shared database fixtures for gateway unit tests."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

from apme_gateway.db import close_db, init_db, reset_db
from apme_gateway.operation_registry import get_operation_registry

_WORKER_NAME_RE = re.compile(r"^(master|gw\d+)$")


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


def base_test_database_url() -> str | None:
    """Return the configured PostgreSQL URL for optional smoke tests.

    Returns:
        Base PostgreSQL connection URL when ``APME_TEST_DATABASE_URL`` is set,
        otherwise ``None``.
    """
    url = os.environ.get("APME_TEST_DATABASE_URL", "").strip()
    return url or None


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

    Raises:
        RuntimeError: When ``APME_TEST_DATABASE_URL`` is not configured.
    """
    base_url = base_test_database_url()
    if not base_url:
        msg = "APME_TEST_DATABASE_URL is not configured"
        raise RuntimeError(msg)
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f"/{worker_database_name()}"))


async def ensure_worker_database() -> str:
    """Create the worker database if needed and return its URL.

    Returns:
        Worker-specific PostgreSQL connection URL.

    Raises:
        RuntimeError: When ``APME_TEST_DATABASE_URL`` is not configured.
    """
    import asyncpg

    base_url = base_test_database_url()
    if not base_url:
        msg = "APME_TEST_DATABASE_URL is not configured"
        raise RuntimeError(msg)
    parsed = urlparse(base_url.replace("postgresql+asyncpg://", "postgresql://"))
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


@pytest.fixture  # type: ignore[untyped-decorator]
async def gateway_db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh SQLite schema per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await close_db()
    await init_db(str(tmp_path / "test.db"))
    await reset_db()
    yield
    registry = get_operation_registry()
    await registry.shutdown()
    await close_db()
