"""PostgreSQL-backed gateway DB smoke tests."""

from __future__ import annotations

import pytest

from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import Session

pytestmark = pytest.mark.usefixtures("gateway_db")


async def test_postgresql_list_sessions_round_trip() -> None:
    """Basic CRUD works against PostgreSQL."""
    async with get_session() as db:
        db.add(Session(session_id="pg-sess", project_path="/proj", first_seen="t0", last_seen="t0"))
        await db.commit()
        sessions = await q.list_sessions(db)
    assert len(sessions) == 1
    assert sessions[0].session_id == "pg-sess"
