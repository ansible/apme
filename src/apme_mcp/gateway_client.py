"""Typed, credential-safe HTTP client for the APME Gateway REST API."""  # noqa: DOC101,DOC103,DOC201,DOC501,DOC503
# ruff: noqa: D102,D105,D107

from __future__ import annotations

import os
from typing import Any

import httpx

_SECRET_FIELDS = frozenset({"token", "scm_token", "requirements_token", "password", "secret", "api_key"})


def _redact(value: Any) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Return value with credential-shaped fields removed from diagnostics."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in _SECRET_FIELDS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class GatewayError(RuntimeError):
    """Gateway request failure with safe diagnostic context."""

    def __init__(self, status_code: int, path: str, detail: Any) -> None:  # noqa: DOC101,DOC103
        self.status_code = status_code
        self.path = path
        self.detail = _redact(detail)
        super().__init__(f"Gateway request failed ({status_code}) at {path}: {self.detail}")


class GatewayClient:
    """Small async client for existing versioned Gateway routes."""

    def __init__(self, base_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:  # noqa: DOC101,DOC103
        configured_url = base_url or os.getenv("APME_GATEWAY_URL") or "http://127.0.0.1:8080"
        self.base_url = configured_url.rstrip("/")
        self._client = client

    async def __aenter__(self) -> GatewayClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None and self._client.base_url == httpx.URL(self.base_url):
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GatewayClient must be used as an async context manager")
        return self._client

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text[:1000]
            raise GatewayError(response.status_code, path, detail)
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError(response.status_code, path, "Gateway returned non-JSON response") from exc

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, json=payload)

    async def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return await self.request("PATCH", path, json=payload)


def project_path(project_id: str, suffix: str = "") -> str:  # noqa: DOC101,DOC103,DOC201,DOC501,DOC503
    """Build project operation path without accepting arbitrary URL paths."""
    if not project_id or "/" in project_id:
        raise ValueError("project_id must be a non-empty identifier")
    return f"/api/v1/projects/{project_id}{suffix}"
