"""Tests for Gateway → Abbenay HTTP admin reverse-proxy (ADR-070)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.app import create_app


@pytest.fixture  # type: ignore[untyped-decorator]
async def app_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """ASGI client with Abbenay HTTP env set for proxy tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        AsyncClient: Client bound to the Gateway ASGI app.
    """
    monkeypatch.setenv("APME_ABBENAY_HTTP_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("APME_ABBENAY_HTTP_TOKEN", "admin-http-token")
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _mock_upstream(
    *,
    status_code: int = 200,
    content: bytes = b'{"ok":true}',
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a fake httpx response and AsyncClient context manager.

    Args:
        status_code: Upstream HTTP status.
        content: Upstream response body.
        headers: Optional upstream response headers.

    Returns:
        MagicMock: AsyncClient stand-in whose ``request`` returns the response.
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.content = content
    response.headers = httpx.Headers(headers or {"content-type": "application/json"})

    client = MagicMock()
    client.request = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_proxy_get_config_rewrites_path_and_injects_bearer(
    app_client: AsyncClient,
) -> None:
    """GET /api/v1/ai/config proxies to Abbenay /api/config with Bearer token.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream(content=b'{"config":{"providers":{}}}')
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get(
            "/api/v1/ai/config",
            headers={"Authorization": "Bearer portal-caller-token"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"config": {"providers": {}}}
    assert client.request.await_args is not None
    assert client.request.await_args.args[0] == "GET"
    assert client.request.await_args.args[1] == "http://127.0.0.1:8787/api/config"
    assert client.request.await_args.kwargs["headers"]["Authorization"] == ("Bearer admin-http-token")
    assert "Cookie" not in client.request.await_args.kwargs["headers"]


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_proxy_post_provider_configure(app_client: AsyncClient) -> None:
    """POST /api/v1/ai/provider/x/configure maps to Abbenay provider path.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream(content=b'{"success":true}')
    body = {"engine": "openrouter", "api_key": "sk-test"}
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.post("/api/v1/ai/provider/openrouter/configure", json=body)

    assert resp.status_code == 200
    assert client.request.await_args is not None
    assert client.request.await_args.args[1] == ("http://127.0.0.1:8787/api/provider/openrouter/configure")
    assert b"sk-test" in (client.request.await_args.kwargs.get("content") or b"")


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_proxy_upstream_unreachable_returns_502(app_client: AsyncClient) -> None:
    """Proxy returns 502 when Abbenay HTTP cannot be reached.

    Args:
        app_client: Async HTTP test client.
    """
    client = MagicMock()
    client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get("/api/v1/ai/providers")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"].lower()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_get_ai_models_not_shadowed_by_proxy(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/ai/models still uses Primary ListAIModels, not Abbenay proxy.

    Args:
        app_client: Async HTTP test client.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_PRIMARY_ADDRESS", "127.0.0.1:59999")
    client = _mock_upstream(content=b'[{"id":"should-not-appear"}]')
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get("/api/v1/ai/models")

    assert resp.status_code == 200
    assert resp.json() == []
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_post_ai_models_not_proxied(app_client: AsyncClient) -> None:
    """POST /api/v1/ai/models is rejected (models reserved for Primary GET).

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.post("/api/v1/ai/models", json={})

    assert resp.status_code == 404
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_chat_path_not_proxied(app_client: AsyncClient) -> None:
    """POST /api/v1/ai/chat is not an admin allowlist path (ADR-046).

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.post("/api/v1/ai/chat", json={"message": "hi"})

    assert resp.status_code == 404
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_secrets_path_not_proxied(app_client: AsyncClient) -> None:
    """GET /api/v1/ai/secrets is outside the documented admin allowlist.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get("/api/v1/ai/secrets")

    assert resp.status_code == 404
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_encoded_path_traversal_rejected(app_client: AsyncClient) -> None:
    """Encoded .. segments must not escape Abbenay /api/.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get("/api/v1/ai/%2e%2e/secret")

    assert resp.status_code == 404
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_encoded_query_delimiter_in_path_rejected(app_client: AsyncClient) -> None:
    """Encoded ? / # in the path param must not alter upstream URL shape.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp_q = await app_client.get("/api/v1/ai/config%3Fevil=1")
        resp_h = await app_client.get("/api/v1/ai/config%23frag")

    assert resp_q.status_code == 404
    assert resp_h.status_code == 404
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_missing_token_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy fails closed when no Abbenay HTTP token is configured.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("APME_ABBENAY_HTTP_URL", "http://127.0.0.1:8787")
    monkeypatch.delenv("APME_ABBENAY_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("APME_ABBENAY_TOKEN", raising=False)
    monkeypatch.delenv("ABBENAY_API_TOKEN", raising=False)
    transport = ASGITransport(app=create_app())
    client = _mock_upstream()
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get("/api/v1/ai/config")

    assert resp.status_code == 503
    client.request.assert_not_awaited()


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_set_cookie_not_forwarded(app_client: AsyncClient) -> None:
    """Upstream Set-Cookie must not be returned to Gateway clients.

    Args:
        app_client: Async HTTP test client.
    """
    client = _mock_upstream(
        headers={
            "content-type": "application/json",
            "set-cookie": "session=evil",
            "content-encoding": "gzip",
        },
    )
    with patch("apme_gateway.api.abbenay_proxy.httpx.AsyncClient", return_value=client):
        resp = await app_client.get("/api/v1/ai/config")

    assert resp.status_code == 200
    assert "set-cookie" not in {k.lower() for k in resp.headers}
    assert "content-encoding" not in {k.lower() for k in resp.headers}
