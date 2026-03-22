"""FastAPI application factory for the gateway."""

from __future__ import annotations

from fastapi import FastAPI

from apme_gateway.api.router import router


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers registered.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="APME Gateway",
        description="Reporting persistence and read-only REST API (ADR-020 / ADR-029)",
        version="0.1.0",
    )
    app.include_router(router)
    return app
