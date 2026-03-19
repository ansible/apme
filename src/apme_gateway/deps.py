"""Shared FastAPI dependencies — avoids circular imports between app and routers."""

from __future__ import annotations

from apme_gateway.services.grpc_client import PrimaryClient

_primary_client = PrimaryClient()


def get_primary_client() -> PrimaryClient:
    """FastAPI dependency returning the shared PrimaryClient."""
    return _primary_client
