"""Persisted global policy and write-only project dependency credential."""

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import Project

pytestmark = pytest.mark.usefixtures("gateway_db")


async def test_git_policy_and_project_credentials() -> None:
    """Global opt-in persists, and project APIs never return credential values."""
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        path = "/api/v1/settings/git-requirements"
        assert (await client.get(path)).json() == {"enabled": False}
        assert (await client.put(path, json={"enabled": True})).json() == {"enabled": True}
        assert (await client.get(path)).json() == {"enabled": True}
        response = await client.post(
            "/api/v1/projects",
            json={
                "name": "Git requirements",
                "repo_url": "https://github.com/example/project",
                "scm_token": "project-secret",
                "requirements_token": "dependency-secret",
            },
        )
        assert response.status_code == 201
        assert "dependency-secret" not in response.text
        project = response.json()
        assert project["has_requirements_token"]
        project_path = f"/api/v1/projects/{project['id']}"
        detail = (await client.get(project_path)).json()
        assert detail["has_requirements_token"]
        assert detail["git_requirements_enabled"]
        async with get_session() as db:
            saved = await db.get(Project, project["id"])
            assert saved is not None
            assert saved.requirements_token == "dependency-secret"
        cleared = await client.patch(project_path, json={"requirements_token": ""})
        assert cleared.status_code == 200
        assert not cleared.json()["has_requirements_token"]
        assert cleared.json()["has_scm_token"]
        assert (await client.put(path, json={"enabled": False})).json() == {"enabled": False}


async def test_concurrent_initial_policy_writes() -> None:
    """The singleton policy supports simultaneous initial saves."""
    import asyncio

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        responses = await asyncio.gather(
            *(client.put("/api/v1/settings/git-requirements", json={"enabled": value}) for value in (False, True))
        )
        assert all(response.status_code == 200 for response in responses)
