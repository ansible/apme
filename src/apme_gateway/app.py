"""FastAPI application factory for the gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apme_gateway._galaxy_proxy_sync import schedule_push
from apme_gateway.api.abbenay_proxy import router as abbenay_proxy_router
from apme_gateway.api.feedback import router as feedback_router
from apme_gateway.api.operation_router import operation_router
from apme_gateway.api.router import router
from apme_gateway.operation_registry import get_operation_registry


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Gateway startup/shutdown lifecycle.

    On startup, schedule a background push of Galaxy server configs from
    the DB to the Galaxy Proxy and start the operation registry reaper.
    On shutdown, clean up in-flight operations.

    Args:
        app: The FastAPI application instance (unused, required by lifespan protocol).

    Yields:
        None: Control to the application.
    """
    schedule_push()
    get_operation_registry().start_reaper()
    yield
    await get_operation_registry().shutdown()


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers registered.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="APME Gateway",
        description=(
            "Reporting persistence and REST API for projects, activity, "
            "and operations (ADR-020 / ADR-029 / ADR-052). Public contract "
            "under /api/v1 (ADR-060). Abbenay admin HTTP proxy (ADR-070)."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    # Main router first so GET /api/v1/ai/models (Primary) wins over the
    # ADR-070 Abbenay admin catch-all /api/v1/ai/{path}.
    app.include_router(router)
    app.include_router(feedback_router)
    app.include_router(operation_router)
    app.include_router(abbenay_proxy_router)
    from apme_engine.observability.http_middleware import HttpMetricsMiddleware

    app.add_middleware(HttpMetricsMiddleware, service="gateway")
    return app
