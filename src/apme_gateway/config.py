"""Gateway configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable gateway configuration.

    Attributes:
        db_path: Path to the SQLite database file.
        grpc_listen: Address for the gRPC Reporting service.
        http_host: Host for the FastAPI HTTP server.
        http_port: Port for the FastAPI HTTP server.
    """

    db_path: str = field(default_factory=lambda: os.environ.get("APME_DB_PATH", "/data/apme.db"))
    grpc_listen: str = field(default_factory=lambda: os.environ.get("APME_GATEWAY_GRPC_LISTEN", "0.0.0.0:50060"))
    http_host: str = field(default_factory=lambda: os.environ.get("APME_GATEWAY_HTTP_HOST", "0.0.0.0"))
    http_port: int = field(default_factory=lambda: int(os.environ.get("APME_GATEWAY_HTTP_PORT", "8080")))


def load_config() -> GatewayConfig:
    """Build config from current environment.

    Returns:
        Populated GatewayConfig.
    """
    return GatewayConfig()
