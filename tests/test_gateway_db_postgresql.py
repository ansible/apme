"""PostgreSQL-backed gateway DB smoke tests (optional).

Set ``APME_TEST_DATABASE_URL`` to a disposable database, e.g.::

    export APME_TEST_DATABASE_URL='postgresql+asyncpg://apme:apme@localhost:5432/apme_test'

Tests are skipped when the variable is unset so CI and local SQLite runs stay fast.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.api.schemas import ComponentHealth
from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db, reset_db
from apme_gateway.db import queries as q
from apme_gateway.db.models import Session
from tests.gateway_db import ensure_worker_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("APME_TEST_DATABASE_URL", "").strip(),
    reason="APME_TEST_DATABASE_URL not set",
)


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _postgresql_db() -> AsyncIterator[None]:
    """Initialise a fresh PostgreSQL schema per test.

    Yields:
        None: Test runs between setup and teardown.
    """
    url = await ensure_worker_database()
    await close_db()
    await init_db(url)
    await reset_db()
    yield
    await close_db()


async def test_postgresql_list_sessions_round_trip() -> None:
    """Basic CRUD works against PostgreSQL."""
    async with get_session() as db:
        db.add(Session(session_id="pg-sess", project_path="/proj", first_seen="t0", last_seen="t0"))
        await db.commit()
        sessions = await q.list_sessions(db)
    assert len(sessions) == 1
    assert sessions[0].session_id == "pg-sess"


async def test_postgresql_health_reports_database_type() -> None:
    """Health endpoint reports PostgreSQL when backed by PostgreSQL."""
    app = create_app()
    transport = ASGITransport(app=app)
    mock_component = ComponentHealth(name="mock", status="ok", address="127.0.0.1:0")
    with patch(
        "apme_gateway.api.router._check_component",
        new_callable=AsyncMock,
        return_value=mock_component,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "ok"
    assert body["database_type"] == "PostgreSQL"
