"""Run the Gitleaks validator gRPC server."""

import asyncio
import os
import sys
import traceback

from apme_engine.daemon.gitleaks_validator_server import serve


async def _run(listen: str) -> None:
    server = await serve(listen)
    sys.stderr.write(f"Gitleaks validator listening on {listen}\n")
    sys.stderr.flush()
    await server.wait_for_termination()


def main() -> None:
    """Entry point: run Gitleaks validator gRPC server until interrupted.

    Uses APME_GITLEAKS_VALIDATOR_LISTEN for bind address. Exits with code 1 on failure.
    """
    from apme_engine.log_bridge import install_handler
    from apme_engine.observability import setup_otel, shutdown_otel

    install_handler()
    setup_otel(service_name=os.environ.get("OTEL_SERVICE_NAME", "apme-gitleaks"))

    listen = os.environ.get("APME_GITLEAKS_VALIDATOR_LISTEN", "0.0.0.0:50056")
    try:
        asyncio.run(_run(listen))
    except Exception as e:
        sys.stderr.write(f"Gitleaks validator failed: {e}\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)
    finally:
        shutdown_otel()


if __name__ == "__main__":
    main()
