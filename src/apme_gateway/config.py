"""Gateway configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable gateway configuration.

    Attributes:
        database_url: SQLAlchemy URL (``postgresql+asyncpg://...``) for persistence.
        grpc_listen: Address for the gRPC Reporting service.
        http_host: Host for the FastAPI HTTP server.
        http_port: Port for the FastAPI HTTP server.
        engine_address: gRPC address of the Engine orchestrator.
        feedback_enabled: Enable the user feedback endpoint (POC feature).
        feedback_github_repo: GitHub repo for issue creation (e.g. ``owner/repo``).
        feedback_github_token: GitHub token with ``issues:write`` for feedback.
        scm_token: Global SCM token fallback for PR creation (ADR-050).
        github_api_url: GitHub API base URL (ADR-050). Default ``https://api.github.com``.
        gitlab_api_url: GitLab API base URL (ADR-050 Phase 2). Default ``https://gitlab.com/api/v4``.
        bitbucket_api_url: Bitbucket API base URL (ADR-050 Phase 2). Default Cloud 2.0 API.
    """

    database_url: str | None = field(
        default_factory=lambda: os.environ.get("APME_DATABASE_URL", "").strip() or None,
    )
    grpc_listen: str = field(default_factory=lambda: os.environ.get("APME_GATEWAY_GRPC_LISTEN", "0.0.0.0:50060"))
    http_host: str = field(default_factory=lambda: os.environ.get("APME_GATEWAY_HTTP_HOST", "0.0.0.0"))
    http_port: int = field(default_factory=lambda: int(os.environ.get("APME_GATEWAY_HTTP_PORT", "8080")))
    engine_address: str = field(default_factory=lambda: os.environ.get("APME_ENGINE_ADDRESS", "localhost:50051"))
    feedback_enabled: bool = field(
        default_factory=lambda: os.environ.get("APME_FEEDBACK_ENABLED", "false").lower() in ("1", "true", "yes"),
    )
    feedback_github_repo: str = field(
        default_factory=lambda: os.environ.get("APME_FEEDBACK_GITHUB_REPO", ""),
    )
    feedback_github_token: str = field(
        default_factory=lambda: os.environ.get("APME_FEEDBACK_GITHUB_TOKEN", ""),
    )
    scm_token: str = field(
        default_factory=lambda: os.environ.get("APME_SCM_TOKEN", ""),
    )
    github_api_url: str = field(
        default_factory=lambda: os.environ.get("APME_GITHUB_API_URL", "https://api.github.com"),
    )
    gitlab_api_url: str = field(
        default_factory=lambda: os.environ.get("APME_GITLAB_API_URL", "https://gitlab.com/api/v4"),
    )
    bitbucket_api_url: str = field(
        default_factory=lambda: os.environ.get("APME_BITBUCKET_API_URL", "https://api.bitbucket.org/2.0"),
    )


def load_config() -> GatewayConfig:
    """Build config from current environment.

    Returns:
        Populated GatewayConfig.
    """
    return GatewayConfig()
