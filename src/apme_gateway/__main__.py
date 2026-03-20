"""Run the gateway with ``python -m apme_gateway``."""

from __future__ import annotations

import logging

import uvicorn

from apme_gateway.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    """Entry point for the gateway server."""
    config = load_config()
    uvicorn.run(
        "apme_gateway.app:create_app",
        factory=True,
        host=config.listen_host,
        port=config.listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
