"""SCM provider registry (ADR-050).

Maps provider identifiers to concrete implementations.  Phase 1 ships
GitHub; Phase 2 adds GitLab and Bitbucket (Cloud + Server/DC).
"""

from __future__ import annotations

from typing import Any

from apme_gateway.scm.base import ScmProvider
from apme_gateway.scm.bitbucket import create_bitbucket_provider
from apme_gateway.scm.github import GitHubProvider
from apme_gateway.scm.gitlab import GitLabProvider
from apme_gateway.scm.urls import (
    DEFAULT_BITBUCKET_CLOUD_API_URL,
    DEFAULT_GITLAB_API_URL,
)

_PROVIDERS: dict[str, type[ScmProvider]] = {
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
}


def get_provider(provider_type: str, *, api_base_url: str | None = None) -> ScmProvider:
    """Resolve a provider instance by type identifier.

    Args:
        provider_type: One of ``github``, ``gitlab``, ``bitbucket``.
        api_base_url: Override the default API URL for the provider.

    Returns:
        A concrete ScmProvider instance.

    Raises:
        ValueError: If the provider type is not supported.
    """
    if provider_type == "bitbucket":
        return create_bitbucket_provider(api_base_url or DEFAULT_BITBUCKET_CLOUD_API_URL)

    cls = _PROVIDERS.get(provider_type)
    if cls is None:
        supported = ", ".join(sorted([*_PROVIDERS, "bitbucket"]))
        msg = f"Unsupported SCM provider '{provider_type}'. Supported: {supported}"
        raise ValueError(msg)

    kwargs: dict[str, Any] = {}
    if api_base_url:
        kwargs["api_base_url"] = api_base_url
    elif provider_type == "gitlab":
        kwargs["api_base_url"] = DEFAULT_GITLAB_API_URL
    return cls(**kwargs)
