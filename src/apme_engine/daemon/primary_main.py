"""Run the Primary daemon gRPC server."""

import asyncio
import os
import sys
import traceback

from apme_engine.daemon.event_emitter import stop_sinks
from apme_engine.daemon.primary_server import serve


async def _run(listen: str) -> None:
    """Start the Primary daemon server and wait for termination.

    Args:
        listen: Host:port address to bind (e.g. 0.0.0.0:50051).
    """
    server = await serve(listen)
    sys.stderr.write(f"Primary daemon listening on {listen}\n")
    sys.stderr.flush()
    try:
        await server.wait_for_termination()
    finally:
        await stop_sinks()


def main() -> None:
    """Entry point: run Primary daemon gRPC server until interrupted.

    Uses APME_PRIMARY_LISTEN for bind address. Exits with code 1 on failure.
    """
    from apme_engine.log_bridge import install_handler

    install_handler()

    listen = os.environ.get("APME_PRIMARY_LISTEN", "0.0.0.0:50051")
    try:
        asyncio.run(_run(listen))
    except Exception as e:
        sys.stderr.write(f"Primary daemon failed: {e}\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
