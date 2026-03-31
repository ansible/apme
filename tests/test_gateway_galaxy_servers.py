"""Unit tests for the Galaxy server settings REST API (ADR-045)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app
from apme_gateway.db import close_db, init_db


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    await init_db(str(tmp_path / "test.db"))
    yield
    await close_db()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── List (empty) ─────────────────────────────────────────────────────


async def test_list_galaxy_servers_empty(client: AsyncClient) -> None:
    """GET /settings/galaxy-servers returns empty list when no servers configured.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/settings/galaxy-servers")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Create ───────────────────────────────────────────────────────────


async def test_create_galaxy_server(client: AsyncClient) -> None:
    """POST /settings/galaxy-servers creates and returns a server (token masked).

    Args:
        client: Async HTTP test client.
    """
    body = {
        "name": "automation_hub",
        "url": "https://hub.example.com/api/",
        "token": "secret-token",
        "auth_url": "https://sso.example.com/auth",
    }
    resp = await client.post("/api/v1/settings/galaxy-servers", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "automation_hub"
    assert data["url"] == "https://hub.example.com/api/"
    assert data["auth_url"] == "https://sso.example.com/auth"
    assert data["has_token"] is True
    assert "token" not in data
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


async def test_create_galaxy_server_no_token(client: AsyncClient) -> None:
    """POST /settings/galaxy-servers with no token sets has_token=False.

    Args:
        client: Async HTTP test client.
    """
    body = {"name": "public_galaxy", "url": "https://galaxy.ansible.com/"}
    resp = await client.post("/api/v1/settings/galaxy-servers", json=body)
    assert resp.status_code == 201
    assert resp.json()["has_token"] is False


async def test_create_galaxy_server_duplicate_name(client: AsyncClient) -> None:
    """POST /settings/galaxy-servers returns 409 on duplicate name.

    Args:
        client: Async HTTP test client.
    """
    body = {"name": "hub", "url": "https://hub.example.com/"}
    resp1 = await client.post("/api/v1/settings/galaxy-servers", json=body)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/v1/settings/galaxy-servers", json=body)
    assert resp2.status_code == 409


# ── List (populated) ─────────────────────────────────────────────────


async def test_list_galaxy_servers(client: AsyncClient) -> None:
    """GET /settings/galaxy-servers returns all servers sorted by name.

    Args:
        client: Async HTTP test client.
    """
    await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "zebra", "url": "https://z.example.com/"},
    )
    await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "alpha", "url": "https://a.example.com/"},
    )
    resp = await client.get("/api/v1/settings/galaxy-servers")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert names == ["alpha", "zebra"]


# ── Get by ID ────────────────────────────────────────────────────────


async def test_get_galaxy_server(client: AsyncClient) -> None:
    """GET /settings/galaxy-servers/{id} returns the server.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "hub", "url": "https://hub.example.com/", "token": "tok"},
    )
    server_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/settings/galaxy-servers/{server_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "hub"
    assert resp.json()["has_token"] is True


async def test_get_galaxy_server_not_found(client: AsyncClient) -> None:
    """GET /settings/galaxy-servers/999 returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/settings/galaxy-servers/999")
    assert resp.status_code == 404


# ── Update ───────────────────────────────────────────────────────────


async def test_update_galaxy_server(client: AsyncClient) -> None:
    """PATCH /settings/galaxy-servers/{id} updates the specified fields.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "hub", "url": "https://old.example.com/"},
    )
    server_id = create_resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/settings/galaxy-servers/{server_id}",
        json={"url": "https://new.example.com/", "token": "new-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://new.example.com/"
    assert data["has_token"] is True
    assert data["name"] == "hub"


async def test_update_galaxy_server_not_found(client: AsyncClient) -> None:
    """PATCH /settings/galaxy-servers/999 returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.patch(
        "/api/v1/settings/galaxy-servers/999",
        json={"name": "new_name"},
    )
    assert resp.status_code == 404


async def test_update_galaxy_server_duplicate_name(client: AsyncClient) -> None:
    """PATCH /settings/galaxy-servers/{id} returns 409 when renaming to an existing name.

    Args:
        client: Async HTTP test client.
    """
    await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "alpha", "url": "https://a.example.com/"},
    )
    create_resp = await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "beta", "url": "https://b.example.com/"},
    )
    beta_id = create_resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/settings/galaxy-servers/{beta_id}",
        json={"name": "alpha"},
    )
    assert resp.status_code == 409


async def test_update_galaxy_server_no_fields(client: AsyncClient) -> None:
    """PATCH /settings/galaxy-servers/{id} with empty body returns 400.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "hub", "url": "https://hub.example.com/"},
    )
    server_id = create_resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/settings/galaxy-servers/{server_id}",
        json={},
    )
    assert resp.status_code == 400


# ── Delete ───────────────────────────────────────────────────────────


async def test_delete_galaxy_server(client: AsyncClient) -> None:
    """DELETE /settings/galaxy-servers/{id} removes the server.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/galaxy-servers",
        json={"name": "hub", "url": "https://hub.example.com/"},
    )
    server_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/v1/settings/galaxy-servers/{server_id}")
    assert resp.status_code == 204

    list_resp = await client.get("/api/v1/settings/galaxy-servers")
    assert list_resp.json() == []


async def test_delete_galaxy_server_not_found(client: AsyncClient) -> None:
    """DELETE /settings/galaxy-servers/999 returns 404.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.delete("/api/v1/settings/galaxy-servers/999")
    assert resp.status_code == 404


# ── Injection helper ─────────────────────────────────────────────────


async def test_load_galaxy_server_defs(client: AsyncClient) -> None:
    """load_galaxy_server_defs returns proto messages from DB rows.

    Args:
        client: Async HTTP test client (used to seed data via REST).
    """
    await client.post(
        "/api/v1/settings/galaxy-servers",
        json={
            "name": "hub",
            "url": "https://hub.example.com/",
            "token": "tok123",
            "auth_url": "https://sso.example.com/",
        },
    )
    from apme_gateway._galaxy_inject import load_galaxy_server_defs

    defs = await load_galaxy_server_defs()
    assert len(defs) == 1
    assert defs[0].name == "hub"
    assert defs[0].url == "https://hub.example.com/"
    assert defs[0].token == "tok123"
    assert defs[0].auth_url == "https://sso.example.com/"


async def test_load_galaxy_server_defs_empty() -> None:
    """load_galaxy_server_defs returns empty list when no servers exist."""
    from apme_gateway._galaxy_inject import load_galaxy_server_defs

    defs = await load_galaxy_server_defs()
    assert defs == []
