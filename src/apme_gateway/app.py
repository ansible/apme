"""FastAPI application factory for the gateway."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apme_gateway._galaxy_proxy_sync import push_galaxy_config
from apme_gateway.api.feedback import router as feedback_router
from apme_gateway.api.router import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Gateway startup/shutdown lifecycle.

    On startup, push the current Galaxy server configs from the DB to the
    Galaxy Proxy so it can authenticate with Automation Hub from the first
    download request.

    Args:
        app: The FastAPI application instance (unused, required by lifespan protocol).

    Yields:
        None: Control to the application.
    """
    await push_galaxy_config()
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers registered.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="APME Gateway",
        description="Reporting persistence and read-only REST API (ADR-020 / ADR-029)",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.include_router(router)
    app.include_router(feedback_router)
    return app
