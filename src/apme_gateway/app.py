"""FastAPI application factory for the APME Web Gateway.

The gateway is a standalone container that sits **outside** the engine pod
(ADR-029). It translates HTTP/WebSocket to gRPC, owns scan result persistence
in SQLite, and serves the optional static SPA (ADR-030).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apme_gateway.config import load_config
from apme_gateway.database import close_db, get_session_factory, init_db
from apme_gateway.deps import set_primary_client
from apme_gateway.routers import fix, format, health, remediation, reports, rules, scans
from apme_gateway.services import event_subscriber
from apme_gateway.services.grpc_client import PrimaryClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown lifecycle — init DB, gRPC client, event subscriber."""
    config = load_config()

    await init_db(config)
    logger.info("Database initialised (%s)", config.db_url)

    client = PrimaryClient(config)
    set_primary_client(client)
    logger.info("gRPC client targeting %s", config.primary_address)

    event_subscriber.start(
        primary_address=config.primary_address,
        session_factory=get_session_factory(),
        max_msg_bytes=config.grpc_max_message_bytes,
    )

    yield

    await event_subscriber.stop()
    await client.close()
    await close_db()
    logger.info("Gateway shutdown complete")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application.

    Returns:
        FastAPI app with all routers mounted.
    """
    config = load_config()

    app = FastAPI(
        title="APME Web Gateway",
        description="HTTP/WebSocket → gRPC translation layer for the APME engine.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(scans.router)
    app.include_router(rules.router)
    app.include_router(format.router)
    app.include_router(remediation.router)
    app.include_router(reports.router)
    app.include_router(fix.router)

    return app
