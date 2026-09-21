"""Gateway entry point — runs gRPC Reporting server and FastAPI HTTP server concurrently.

The gateway serves two protocols:
- **gRPC** on ``APME_GATEWAY_GRPC_LISTEN`` (default ``0.0.0.0:50060``)
  for receiving ``FixCompletedEvent`` from engine pods.
- **HTTP** on ``APME_GATEWAY_HTTP_PORT`` (default ``8080``)
  for the read-only REST API consumed by dashboards.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import grpc
import uvicorn

from apme.v1 import reporting_pb2_grpc
from apme_gateway.app import create_app
from apme_gateway.config import load_config
from apme_gateway.db import close_db, init_db_from_config
from apme_gateway.db.url import sanitize_database_url
from apme_gateway.grpc_reporting.servicer import ReportingServicer

logger = logging.getLogger(__name__)

_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — large scan/fix events exceed the 4 MiB default


async def _run_grpc(listen: str, stop_event: asyncio.Event) -> None:
    """Start the async gRPC server and block until stop_event is set.

    Args:
        listen: Bind address (e.g. ``0.0.0.0:50060``).
        stop_event: Signals graceful shutdown.
    """
    server = grpc.aio.server(
        options=[
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
        ],
    )
    reporting_pb2_grpc.add_ReportingServicer_to_server(ReportingServicer(), server)  # type: ignore[no-untyped-call]

    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    health_servicer = health.aio.HealthServicer()  # type: ignore[attr-defined]
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    server.add_insecure_port(listen)
    await server.start()

    await health_servicer.set(
        "",
        health_pb2.HealthCheckResponse.SERVING,
    )

    logger.info("gRPC Reporting server listening on %s", listen)
    await stop_event.wait()
    await server.stop(grace=5)
    logger.info("gRPC server stopped")


async def _run_http(host: str, port: int, stop_event: asyncio.Event) -> None:
    """Start uvicorn in-process and block until stop_event is set.

    Args:
        host: Bind host.
        port: Bind port.
        stop_event: Signals graceful shutdown.
    """
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    server.install_signal_handlers = lambda: None
    serve_task = asyncio.create_task(server.serve())

    await stop_event.wait()
    server.should_exit = True
    await serve_task
    logger.info("HTTP server stopped")


async def _run() -> None:
    """Orchestrate both servers with shared lifecycle."""
    cfg = load_config()

    db_url = await init_db_from_config(database_url=cfg.database_url)

    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
    except NotImplementedError:
        logger.warning("Signal handlers not supported on this platform")

    logger.info(
        "APME Gateway starting — gRPC=%s  HTTP=%s:%d  DB=%s",
        cfg.grpc_listen,
        cfg.http_host,
        cfg.http_port,
        sanitize_database_url(db_url),
    )

    try:
        await asyncio.gather(
            _run_grpc(cfg.grpc_listen, stop_event),
            _run_http(cfg.http_host, cfg.http_port, stop_event),
        )
    finally:
        await close_db()
        logger.info("Database closed")


def main() -> None:
    """CLI entry point for ``apme-gateway``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    from apme_engine.observability import setup_otel, shutdown_otel

    setup_otel(service_name=os.environ.get("OTEL_SERVICE_NAME", "apme-gateway"))
    try:
        asyncio.run(_run())
    finally:
        shutdown_otel()


if __name__ == "__main__":
    main()
