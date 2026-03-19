"""FastAPI application for the APME API Gateway.

Translates REST/WebSocket requests to gRPC calls against the Primary
orchestrator.  Owns persistence (scan history) and serves the static
SPA assets in production.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from apme_gateway.database import init_db
from apme_gateway.deps import _primary_client
from apme_gateway.services.rule_catalog import load_catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _primary_client.connect()
    load_catalog()
    yield
    await _primary_client.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="APME API Gateway",
        version="0.1.0",
        description=(
            "REST/WebSocket gateway for the Ansible Policy & Modernization Engine. "
            "Translates HTTP to gRPC calls against the APME Primary orchestrator."
        ),
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )

    cors_origins = os.getenv("APME_CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from apme_gateway.routers import fix, format, health, remediation, rules, scans

    app.include_router(scans.router, prefix="/api/v1")
    app.include_router(rules.router, prefix="/api/v1")
    app.include_router(format.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(remediation.router, prefix="/api/v1")
    app.include_router(fix.router, prefix="/api/v1")

    static_dir = Path(
        os.getenv("APME_UI_DIR", str(Path(__file__).resolve().parent.parent.parent / "ui" / "standalone" / "dist"))
    )
    if static_dir.is_dir() and any(static_dir.iterdir()):
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
