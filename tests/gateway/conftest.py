"""Shared fixtures for gateway tests.

Provides an in-memory SQLite database and a FastAPI TestClient wired to use
it instead of the default on-disk database.  The gRPC PrimaryClient is
replaced with a mock so gateway tests run without backend services.

Requires the ``gateway`` optional dependency group (sqlalchemy, fastapi, etc.).
When those packages are absent (e.g. in base CI), the parent conftest
excludes this directory via ``collect_ignore_glob``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apme_gateway.database import get_session
from apme_gateway.deps import get_primary_client
from apme_gateway.models.database import Base
from apme_gateway.services.grpc_client import PrimaryClient

_TEST_DB_URL = "sqlite+aiosqlite://"


@pytest.fixture  # type: ignore[untyped-decorator]
async def db_engine():
    """Create an in-memory async SQLite engine for test isolation.

    Yields:
        The async engine instance.
    """
    engine = create_async_engine(_TEST_DB_URL, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture  # type: ignore[untyped-decorator]
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session that rolls back after each test.

    Args:
        db_engine: In-memory engine fixture.

    Yields:
        An async database session.
    """
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture  # type: ignore[untyped-decorator]
def mock_primary_client() -> PrimaryClient:
    """Return a mock PrimaryClient so tests don't need a live gRPC backend.

    Returns:
        A MagicMock configured as a PrimaryClient.
    """
    client = MagicMock(spec=PrimaryClient)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.health = AsyncMock(return_value="ok")
    return client


@pytest.fixture  # type: ignore[untyped-decorator]
async def client(db_engine, mock_primary_client) -> AsyncGenerator[AsyncClient, None]:
    """Provide an httpx AsyncClient wired to the gateway app with test overrides.

    Args:
        db_engine: In-memory engine fixture.
        mock_primary_client: Mock gRPC client fixture.

    Yields:
        An httpx AsyncClient for making test requests.
    """
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    from apme_gateway.app import create_app

    app = create_app()
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_primary_client] = lambda: mock_primary_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
