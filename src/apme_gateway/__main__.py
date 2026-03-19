"""Entry point: python -m apme_gateway or apme-gateway CLI."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("APME_GATEWAY_HOST", "0.0.0.0")
    port = int(os.getenv("APME_GATEWAY_PORT", "50050"))
    uvicorn.run(
        "apme_gateway.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
