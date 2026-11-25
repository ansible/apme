from __future__ import annotations

# pytest's decorator is intentionally untyped in the installed test stubs.
# mypy: disable-error-code="untyped-decorator"

import httpx
import pytest

from apme_mcp.gateway_client import GatewayClient, GatewayError, project_path


@pytest.mark.asyncio
async def test_client_posts_json() -> None:
    request = httpx.Request("POST", "http://gateway/api/v1/projects")
    response = httpx.Response(201, json={"id": "p1"}, request=request)

    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    async with GatewayClient(
        "http://gateway", httpx.AsyncClient(base_url="http://gateway", transport=httpx.MockTransport(handler))
    ) as client:
        assert await client.post("/api/v1/projects", {"name": "demo"}) == {"id": "p1"}


@pytest.mark.asyncio
async def test_client_redacts_credentials_in_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"scm_token": "secret-value", "message": "invalid"}, request=request)

    async with GatewayClient(
        "http://gateway", httpx.AsyncClient(base_url="http://gateway", transport=httpx.MockTransport(handler))
    ) as client:
        with pytest.raises(GatewayError, match=r"\[REDACTED\]"):
            await client.post("/api/v1/projects", {"scm_token": "secret-value"})


def test_project_path_rejects_path_injection() -> None:
    with pytest.raises(ValueError):
        project_path("p1/operation")
