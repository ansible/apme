"""ASGI middleware that records HTTP request durations via OTel metrics."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("apme.observability.http")


class HttpMetricsMiddleware:
    """Record ``apme.http.server.duration`` for HTTP requests."""

    def __init__(self, app: ASGIApp, *, service: str) -> None:
        """Wrap ``app`` and tag metrics with ``service``.

        Args:
            app: Downstream ASGI application.
            service: Logical service label (``gateway`` or ``galaxy-proxy``).
        """
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entrypoint: time HTTP requests and record duration metrics.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: dict) -> None:  # type: ignore[type-arg]
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                from apme_engine.observability.metrics import record_http_request

                record_http_request(
                    time.perf_counter() - started,
                    method=str(method),
                    status_code=status_code,
                    service=self.service,
                )
            except Exception:  # noqa: BLE001 — metrics must never break HTTP responses
                logger.debug("Failed to record HTTP metrics", exc_info=True)
