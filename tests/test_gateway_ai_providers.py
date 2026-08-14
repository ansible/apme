"""Unit tests for the AI provider settings REST API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway._abbenay_admin import AbbenayDiscoveredModel, AbbenayEngineInfo
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


async def test_list_ai_providers_empty(client: AsyncClient) -> None:
    """GET /settings/ai-providers returns empty list when none configured.

    Args:
        client: Async HTTP test client.
    """
    resp = await client.get("/api/v1/settings/ai-providers")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_ai_provider(client: AsyncClient) -> None:
    """POST /settings/ai-providers creates a provider and masks the API key.

    Args:
        client: Async HTTP test client.
    """
    body = {
        "name": "openrouter",
        "engine": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "sk-test",
        "models": {"anthropic/claude-sonnet-4-6": {}},
    }
    with patch("apme_gateway._abbenay_sync.schedule_sync") as mock_sync:
        resp = await client.post("/api/v1/settings/ai-providers", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "openrouter"
    assert data["engine"] == "openrouter"
    assert data["has_api_key"] is True
    assert "sk-test" not in resp.text
    assert data["models"]["anthropic/claude-sonnet-4-6"] == {}
    mock_sync.assert_called_once()


async def test_create_ai_provider_duplicate_name(client: AsyncClient) -> None:
    """POST /settings/ai-providers returns 409 for duplicate names.

    Args:
        client: Async HTTP test client.
    """
    body = {
        "name": "openrouter",
        "engine": "openrouter",
        "models": {"gpt-4o": {}},
    }
    resp1 = await client.post("/api/v1/settings/ai-providers", json=body)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/v1/settings/ai-providers", json=body)
    assert resp2.status_code == 409


async def test_update_ai_provider(client: AsyncClient) -> None:
    """PATCH /settings/ai-providers/{id} updates mutable fields.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/ai-providers",
        json={"name": "ollama", "engine": "ollama", "models": {"llama3": {}}},
    )
    provider_id = create_resp.json()["id"]
    with patch("apme_gateway._abbenay_sync.schedule_sync"):
        resp = await client.patch(
            f"/api/v1/settings/ai-providers/{provider_id}",
            json={"base_url": "http://localhost:11434", "models": {"llama3.2": {}}},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_url"] == "http://localhost:11434"
    assert "llama3.2" in data["models"]


async def test_delete_ai_provider(client: AsyncClient) -> None:
    """DELETE /settings/ai-providers/{id} removes DB row and Abbenay provider.

    Args:
        client: Async HTTP test client.
    """
    create_resp = await client.post(
        "/api/v1/settings/ai-providers",
        json={"name": "temp", "engine": "mock", "models": {"mock-model": {}}},
    )
    provider_id = create_resp.json()["id"]
    mock_admin = AsyncMock()
    mock_admin.remove_provider = AsyncMock()
    mock_admin.close = AsyncMock()
    with (
        patch("apme_gateway._abbenay_admin.open_abbenay_admin", return_value=mock_admin),
        patch("apme_gateway._abbenay_sync.schedule_sync"),
    ):
        resp = await client.delete(f"/api/v1/settings/ai-providers/{provider_id}")
    assert resp.status_code == 204
    mock_admin.remove_provider.assert_called_once_with("temp")

    list_resp = await client.get("/api/v1/settings/ai-providers")
    assert list_resp.json() == []


async def test_list_ai_engines_from_abbenay(client: AsyncClient) -> None:
    """GET /settings/ai-engines returns Abbenay engine descriptors.

    Args:
        client: Async HTTP test client.
    """
    mock_admin = AsyncMock()
    mock_admin.list_engines = AsyncMock(
        return_value=[
            AbbenayEngineInfo(
                id="openrouter",
                requires_key=True,
                default_base_url="https://openrouter.ai/api/v1",
                default_env_var="OPENROUTER_API_KEY",
            ),
            AbbenayEngineInfo(
                id="anthropic",
                requires_key=True,
                default_base_url="",
                default_env_var="ANTHROPIC_API_KEY",
            ),
        ],
    )
    mock_admin.close = AsyncMock()
    with patch("apme_gateway._abbenay_admin.open_abbenay_admin", return_value=mock_admin):
        resp = await client.get("/api/v1/settings/ai-engines")
    assert resp.status_code == 200
    engines = resp.json()
    assert len(engines) == 2
    assert engines[0]["id"] == "openrouter"
    assert engines[0]["requires_key"] is True


async def test_discover_ai_models(client: AsyncClient) -> None:
    """POST /settings/ai-providers/discover-models proxies Abbenay discovery.

    Args:
        client: Async HTTP test client.
    """
    mock_admin = AsyncMock()
    mock_admin.discover_models = AsyncMock(
        return_value=[
            AbbenayDiscoveredModel(
                id="gpt-4o",
                name="gpt-4o",
                provider="openai",
                engine="openai",
            ),
        ],
    )
    mock_admin.close = AsyncMock()
    with patch("apme_gateway._abbenay_admin.open_abbenay_admin", return_value=mock_admin):
        resp = await client.post(
            "/api/v1/settings/ai-providers/discover-models",
            json={"engine": "openai", "api_key": "sk-test"},
        )
    assert resp.status_code == 200
    models = resp.json()
    assert models[0]["id"] == "gpt-4o"
    mock_admin.discover_models.assert_called_once_with(
        engine_id="openai",
        api_key="sk-test",
        base_url="",
    )
