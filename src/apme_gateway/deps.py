"""FastAPI dependency injection — DB sessions and gRPC client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apme_gateway.config import GatewayConfig, load_config
from apme_gateway.database import get_session_factory
from apme_gateway.services.grpc_client import PrimaryClient

_client: PrimaryClient | None = None


def set_primary_client(client: PrimaryClient) -> None:
    """Register the PrimaryClient singleton (called at startup).

    Args:
        client: Initialised PrimaryClient instance.
    """
    global _client  # noqa: PLW0603
    _client = client


def get_primary_client() -> PrimaryClient:
    """Return the PrimaryClient singleton.

    Returns:
        The shared PrimaryClient.

    Raises:
        RuntimeError: If set_primary_client has not been called.
    """
    if _client is None:
        msg = "PrimaryClient not initialised"
        raise RuntimeError(msg)
    return _client


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield an async session; caller is responsible for commit/rollback.

    Yields:
        AsyncSession bound to the gateway database.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


def get_config() -> GatewayConfig:
    """Return the gateway configuration.

    Returns:
        Populated GatewayConfig instance.
    """
    return load_config()


def validate_project_path(project_path: str, config: GatewayConfig | None = None) -> Path:
    """Resolve *project_path* and ensure it lives under ``workspace_root``.

    Args:
        project_path: Raw path from the client request.
        config: Optional config; defaults to ``load_config()``.

    Returns:
        Resolved ``Path`` that is within the workspace.

    Raises:
        HTTPException: 403 if the resolved path escapes workspace_root.
        HTTPException: 404 if the path does not exist.
    """
    if config is None:
        config = load_config()
    workspace = Path(config.workspace_root).resolve()
    resolved = Path(project_path).resolve()
    if not str(resolved).startswith(str(workspace) + "/") and resolved != workspace:
        raise HTTPException(status_code=403, detail="Path outside workspace root")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {project_path}")
    return resolved
