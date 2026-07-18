"""Tests for inline scm_token on POST /operation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, get_session, init_db
from apme_gateway.db.models import Project
from apme_gateway.operation_registry import get_operation_registry


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB and clear operation registry per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    registry = get_operation_registry()
    await registry.shutdown()
    await close_db()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_project(scm_token: str | None = None) -> str:
    """Insert a project row for operation tests.

    Args:
        scm_token: Optional per-project SCM token.

    Returns:
        The new project id.
    """
    project_id = "proj-scm-op"
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="SCM Op",
                repo_url="https://github.com/org/repo.git",
                branch="main",
                created_at="2026-01-01T00:00:00+00:00",
                scm_token=scm_token,
            )
        )
        await db.commit()
    return project_id


@pytest.mark.parametrize(
    ("body_token", "project_token", "global_token", "expected"),
    [
        ("inline-token", "project-token", "global-token", "inline-token"),
        (None, "project-token", "global-token", "project-token"),
        (None, None, "global-token", "global-token"),
    ],
)
@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_operate_scm_token_resolution(
    client: AsyncClient,
    body_token: str | None,
    project_token: str | None,
    global_token: str,
    expected: str,
) -> None:
    """Inline body scm_token overrides project and global tokens.

    Args:
        client: Async HTTP test client.
        body_token: Optional inline scm_token from request body.
        project_token: Optional per-project scm_token.
        global_token: Global config scm_token fallback.
        expected: Expected token passed to ``_drive_operation``.
    """
    project_id = await _seed_project(scm_token=project_token)
    captured: dict[str, object] = {}

    async def capture_drive(**kwargs: object) -> None:
        captured.update(kwargs)

    body: dict[str, object] = {"action": "check", "options": {}}
    if body_token is not None:
        body["scm_token"] = body_token

    mock_cfg = MagicMock()
    mock_cfg.scm_token = global_token
    mock_cfg.primary_address = "127.0.0.1:50051"

    with (
        patch("apme_gateway.api.operation_router._drive_operation", side_effect=capture_drive),
        patch("apme_gateway.config.load_config", return_value=mock_cfg),
        patch(
            "apme_gateway._galaxy_inject.load_galaxy_server_defs",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        resp = await client.post(f"/api/v1/projects/{project_id}/operation", json=body)
        await asyncio.sleep(0.05)

    assert resp.status_code == 201
    assert captured.get("scm_token") == expected


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_operate_scm_token_strips_whitespace(client: AsyncClient) -> None:
    """Whitespace around inline scm_token is trimmed before use.

    Args:
        client: Async HTTP test client.
    """
    project_id = await _seed_project()
    captured: dict[str, object] = {}

    async def capture_drive(**kwargs: object) -> None:
        captured.update(kwargs)

    mock_cfg = MagicMock()
    mock_cfg.scm_token = ""
    mock_cfg.primary_address = "127.0.0.1:50051"

    with (
        patch("apme_gateway.api.operation_router._drive_operation", side_effect=capture_drive),
        patch("apme_gateway.config.load_config", return_value=mock_cfg),
        patch(
            "apme_gateway._galaxy_inject.load_galaxy_server_defs",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        resp = await client.post(
            f"/api/v1/projects/{project_id}/operation",
            json={"action": "check", "options": {}, "scm_token": "  ghp_inline  "},
        )
        await asyncio.sleep(0.05)

    assert resp.status_code == 201
    assert captured.get("scm_token") == "ghp_inline"
