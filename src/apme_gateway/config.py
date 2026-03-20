"""Gateway configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable gateway configuration resolved from environment."""

    primary_address: str = field(
        default_factory=lambda: os.environ.get("APME_PRIMARY_ADDRESS", "127.0.0.1:50051"),
    )
    listen_host: str = field(
        default_factory=lambda: os.environ.get("APME_GATEWAY_HOST", "0.0.0.0"),
    )
    listen_port: int = field(
        default_factory=lambda: int(os.environ.get("APME_GATEWAY_PORT", "8080")),
    )
    db_url: str = field(
        default_factory=lambda: os.environ.get("APME_DATABASE_URL", "sqlite+aiosqlite:///./data/apme.db"),
    )
    grpc_timeout: float = field(
        default_factory=lambda: float(os.environ.get("APME_GRPC_TIMEOUT", "120")),
    )
    grpc_max_message_bytes: int = field(
        default_factory=lambda: int(os.environ.get("APME_GRPC_MAX_MSG", str(50 * 1024 * 1024))),
    )
    workspace_root: str = field(
        default_factory=lambda: os.environ.get("APME_WORKSPACE_ROOT", "/workspace"),
    )
    cors_origins: list[str] = field(
        default_factory=lambda: os.environ.get("APME_CORS_ORIGINS", "*").split(","),
    )


def load_config() -> GatewayConfig:
    """Load gateway configuration from environment.

    Returns:
        Populated GatewayConfig instance.
    """
    return GatewayConfig()
