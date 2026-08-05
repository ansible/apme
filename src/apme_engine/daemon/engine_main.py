"""Run the Engine daemon gRPC server."""

import asyncio
import os
import sys

from apme_engine.daemon.engine_server import serve
from apme_engine.daemon.event_emitter import stop_sinks


async def _run(listen: str) -> None:
    """Start the Engine daemon server and wait for termination.

    Args:
        listen: Host:port address to bind (e.g. 0.0.0.0:50051).
    """
    server = await serve(listen)
    sys.stderr.write(f"Engine daemon listening on {listen}\n")
    sys.stderr.flush()
    try:
        await server.wait_for_termination()
    finally:
        await stop_sinks()


def main() -> None:
    """Entry point: run Engine daemon gRPC server until interrupted.

    Uses APME_ENGINE_LISTEN for bind address. Exits with code 1 on failure.
    """
    from apme_engine.log_bridge import install_handler
    from apme_engine.observability import setup_otel, shutdown_otel

    install_handler()
    setup_otel(service_name=os.environ.get("OTEL_SERVICE_NAME", "apme-engine"))

    listen = os.environ.get("APME_ENGINE_LISTEN", "0.0.0.0:50051")
    try:
        asyncio.run(_run(listen))
    except Exception:  # noqa: BLE001 — top-level daemon boundary must surface any failure
        sys.stderr.write("Engine daemon failed: [REDACTED]\n")
        sys.stderr.flush()
        sys.exit(1)
    finally:
        shutdown_otel()


if __name__ == "__main__":
    main()
