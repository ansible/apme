"""FastAPI application factory for the gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apme_gateway._galaxy_proxy_sync import schedule_push
from apme_gateway.api.abbenay_proxy import router as abbenay_proxy_router
from apme_gateway.api.atomic_operate import operate_router
from apme_gateway.api.feedback import router as feedback_router
from apme_gateway.api.operation_router import operation_router
from apme_gateway.api.router import router
from apme_gateway.operation_registry import get_operation_registry


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Gateway startup/shutdown lifecycle.

    On startup, schedule a background push of Galaxy server configs from
    the DB to the Galaxy Proxy, start the operation registry reaper, and
    reconcile the notification outbox (N18). On shutdown, clean up
    in-flight operations.

    Args:
        app: The FastAPI application instance (unused, required by lifespan protocol).

    Yields:
        None: Control to the application.
    """
    schedule_push()
    get_operation_registry().start_reaper()
    try:
        from apme_gateway.notifications import reconcile_notification_outbox  # noqa: PLC0415

        # Drain all pages: each call delivers at most 100 rows and
        # returns the delivered count, so the loop continues only while
        # full pages keep delivering — a fully-failing page stops the
        # drain instead of spinning startup forever.
        while await reconcile_notification_outbox(limit=100) == 100:
            continue
    except Exception:  # noqa: BLE001 -- startup reconcile is best-effort
        import logging as _logging

        _logging.getLogger(__name__).warning("Notification outbox reconciliation failed at startup", exc_info=True)
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
    # Main router first so GET /api/v1/ai/models (Engine) wins over the
    # ADR-070 Abbenay admin catch-all /api/v1/ai/{path}.
    app.include_router(router)
    app.include_router(feedback_router)
    app.include_router(operation_router)
    app.include_router(operate_router)
    app.include_router(abbenay_proxy_router)
    from apme_engine.observability.http_middleware import HttpMetricsMiddleware

    app.add_middleware(HttpMetricsMiddleware, service="gateway")

    @app.exception_handler(Exception)  # type: ignore[untyped-decorator]
    async def _unhandled_gateway_error(_request: Request, exc: Exception) -> JSONResponse:
        """Surface unexpected failures as JSON 500 with string detail.

        Keeps the public error envelope consistent (FastAPI's default
        ServerErrorMiddleware returns plain text, which breaks clients
        expecting the ``{"detail": str}`` shape). Internal error text is
        logged server-side but never echoed — the response carries a
        stable string so callers can branch on status code.

        Args:
            _request: Incoming request (unused, required by handler protocol).
            exc: Unhandled exception (logged, never echoed).

        Returns:
            JSON 500 response with string detail.
        """
        import logging as _logging

        _logging.getLogger(__name__).warning("Unhandled gateway error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    return app
