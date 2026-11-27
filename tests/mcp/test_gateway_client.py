from __future__ import annotations

# Tests use names as specifications; per-test docstrings add no signal.
# ruff: noqa: D100,D103
# pytest's decorator is intentionally untyped in the installed test stubs.
# mypy: disable-error-code="untyped-decorator"
import httpx
import pytest

from apme_mcp.gateway_client import GatewayClient, GatewayError, project_path


@pytest.mark.asyncio
async def test_client_posts_json() -> None:
    request = httpx.Request("POST", "http://127.0.0.1/api/v1/projects")
    response = httpx.Response(201, json={"id": "p1"}, request=request)

    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    async with GatewayClient(
        "http://127.0.0.1", httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler))
    ) as client:
        assert await client.post("/api/v1/projects", {"name": "demo"}) == {"id": "p1"}


@pytest.mark.asyncio
async def test_client_redacts_credentials_in_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"scm_token": "secret-value", "message": "invalid"}, request=request)

    async with GatewayClient(
        "http://127.0.0.1", httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler))
    ) as client:
        with pytest.raises(GatewayError, match=r"\[REDACTED\]"):
            await client.post("/api/v1/projects", {"scm_token": "secret-value"})


@pytest.mark.asyncio
async def test_client_does_not_expose_non_json_error_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="scm_token=secret-value", request=request)

    async with GatewayClient(
        "http://127.0.0.1", httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler))
    ) as client:
        with pytest.raises(GatewayError) as error:
            await client.post("/api/v1/projects", {"scm_token": "secret-value"})
        assert "secret-value" not in str(error.value)


def test_project_path_rejects_path_injection() -> None:
    with pytest.raises(ValueError):
        project_path("p1/operation")


def test_project_path_quotes_reserved_characters() -> None:
    assert project_path("p1?x=violations") == "/api/v1/projects/p1%3Fx%3Dviolations"


def test_client_rejects_remote_gateway_by_default() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GatewayClient("https://gateway.example")
