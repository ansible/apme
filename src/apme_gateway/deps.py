"""FastAPI dependency injection — DB sessions and gRPC client."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

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
    """Yield an async session and commit/rollback via context manager.

    Yields:
        AsyncSession bound to the gateway database.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session
