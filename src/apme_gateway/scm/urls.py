"""SCM API base URL resolution for cloud and self-hosted forges."""

from __future__ import annotations

from urllib.parse import urlparse

DEFAULT_GITHUB_API_URL = "https://api.github.com"
DEFAULT_GITLAB_API_URL = "https://gitlab.com/api/v4"
DEFAULT_BITBUCKET_CLOUD_API_URL = "https://api.bitbucket.org/2.0"

KNOWN_SCM_PROVIDERS = frozenset({"github", "gitlab", "bitbucket"})
_CLOUD_GITLAB_HOSTS = frozenset({"gitlab.com"})
_CLOUD_BITBUCKET_HOSTS = frozenset({"bitbucket.org"})


def require_https_api_base(url: str, provider: str) -> None:
    """Reject SCM API bases that are not absolute HTTPS URLs.

    Args:
        url: Candidate API base URL.
        provider: Provider label for error messages (e.g. ``GitLab``).

    Raises:
        ValueError: When *url* lacks an ``https`` scheme or hostname.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        msg = f"{provider} API URL must be an absolute https:// URL, got: {url}"
        raise ValueError(msg)


def _is_gitlab_cloud_api_url(url: str) -> bool:
    """Return True when *url* is a well-formed GitLab SaaS API base.

    Args:
        url: Candidate GitLab API base URL.

    Returns:
        ``True`` when the URL uses HTTPS, targets ``gitlab.com``, and ends
        with ``/api/v4``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _CLOUD_GITLAB_HOSTS:
        return False
    path = (parsed.path or "").rstrip("/")
    return path == "/api/v4" or path.endswith("/api/v4")


def _hostname(repo_url: str) -> str:
    """Return the lowercased hostname from *repo_url*, or empty string.

    Args:
        repo_url: HTTPS clone URL (or any parseable URL).

    Returns:
        Lowercased hostname, or ``""`` when parsing fails or host is absent.
    """
    try:
        return (urlparse(repo_url).hostname or "").lower()
    except Exception:
        return ""


def is_cloud_scm_host(repo_url: str) -> bool:
    """Return True when *repo_url* targets a known SaaS forge host.

    Only exact SaaS hostnames (and their ``www.`` variants) count as cloud.
    GitLab Dedicated (``customer.gitlab.com``) and self-hosted forges return
    False so callers require an explicit API base URL.

    Args:
        repo_url: HTTPS clone URL.

    Returns:
        ``True`` for github.com / gitlab.com / bitbucket.org (and www).
    """
    host = _hostname(repo_url)
    if not host:
        return False
    if host.startswith("www."):
        host = host[4:]
    if host == "github.com" or host.endswith(".github.com"):
        # github.com and gist.github.com; Enterprise is a different hostname.
        return host == "github.com"
    if host in _CLOUD_GITLAB_HOSTS:
        return True
    return host in _CLOUD_BITBUCKET_HOSTS


def resolve_gitlab_api_url(configured: str, repo_url: str) -> str:
    """Resolve the GitLab API base for *repo_url*.

    SaaS ``gitlab.com`` uses the configured cloud default. Self-hosted and
    GitLab Dedicated hosts require an explicit non-default
    ``APME_GITLAB_API_URL`` — the Gateway does **not** derive the API base
    from ``repo_url`` (SSRF / context-path safety).

    Args:
        configured: Value from ``APME_GITLAB_API_URL``.
        repo_url: HTTPS clone URL.

    Returns:
        API base URL without a trailing slash.

    Raises:
        ValueError: If *repo_url* is self-hosted/Dedicated and *configured*
            is still the cloud default (or host is empty).
    """
    configured = (configured or DEFAULT_GITLAB_API_URL).rstrip("/")
    host = _hostname(repo_url)
    configured_host = _hostname(configured)
    if not host:
        msg = f"Cannot resolve GitLab API URL from invalid repo URL: {repo_url}"
        raise ValueError(msg)

    if host in _CLOUD_GITLAB_HOSTS:
        if _is_gitlab_cloud_api_url(configured):
            require_https_api_base(configured, "GitLab")
            return configured
        require_https_api_base(DEFAULT_GITLAB_API_URL, "GitLab")
        return DEFAULT_GITLAB_API_URL

    # Self-hosted / Dedicated: require explicit non-cloud API base.
    if configured.rstrip("/") == DEFAULT_GITLAB_API_URL.rstrip("/") or configured_host in _CLOUD_GITLAB_HOSTS:
        msg = (
            f"Self-hosted GitLab at '{host}' requires APME_GITLAB_API_URL "
            f"(e.g. https://{host}/api/v4). Auto-derivation from repo_url is disabled."
        )
        raise ValueError(msg)
    require_https_api_base(configured, "GitLab")
    return configured


def resolve_bitbucket_api_url(configured: str, repo_url: str) -> str:
    """Resolve the Bitbucket API base for Cloud vs Server/DC.

    Cloud hosts use the Cloud 2.0 API. Self-hosted Server/DC requires an
    explicit non-default ``APME_BITBUCKET_API_URL`` (SSRF / context-path safety).

    Args:
        configured: Value from ``APME_BITBUCKET_API_URL``.
        repo_url: HTTPS clone URL.

    Returns:
        API base URL without a trailing slash.

    Raises:
        ValueError: If *repo_url* is self-hosted and *configured* is still
            the Cloud default (or host is empty).
    """
    configured = (configured or DEFAULT_BITBUCKET_CLOUD_API_URL).rstrip("/")
    host = _hostname(repo_url)
    if not host:
        msg = f"Cannot resolve Bitbucket API URL from invalid repo URL: {repo_url}"
        raise ValueError(msg)

    if host in _CLOUD_BITBUCKET_HOSTS:
        if is_bitbucket_cloud_api(configured):
            require_https_api_base(configured, "Bitbucket")
            return configured
        require_https_api_base(DEFAULT_BITBUCKET_CLOUD_API_URL, "Bitbucket")
        return DEFAULT_BITBUCKET_CLOUD_API_URL

    if configured.rstrip("/") == DEFAULT_BITBUCKET_CLOUD_API_URL.rstrip("/") or is_bitbucket_cloud_api(configured):
        msg = (
            f"Self-hosted Bitbucket at '{host}' requires APME_BITBUCKET_API_URL "
            f"(e.g. https://{host}/rest/api/1.0). Auto-derivation from repo_url is disabled."
        )
        raise ValueError(msg)
    require_https_api_base(configured, "Bitbucket")
    return configured


def is_bitbucket_cloud_api(api_base_url: str) -> bool:
    """Return True when *api_base_url* targets Bitbucket Cloud API 2.0.

    Classification is hostname-based only (``api.bitbucket.org``). Paths
    ending in ``/2.0`` on other hosts are treated as Server/DC so that
    misconfigured Server bases are not routed to the Cloud provider.

    Args:
        api_base_url: Candidate Bitbucket API base URL.

    Returns:
        ``True`` when the URL targets Cloud API 2.0, else ``False``.
    """
    host = _hostname(api_base_url)
    return host == "api.bitbucket.org"


def split_user_pass_token(token: str) -> tuple[str, str] | None:
    """Split ``username:secret`` app-password tokens.

    Only splits on the first colon so passwords may contain ``:``.

    Args:
        token: Raw SCM token string.

    Returns:
        ``(username, password)`` when the token looks like Basic credentials,
        else ``None``.
    """
    if ":" not in token:
        return None
    user, _, secret = token.partition(":")
    if not user or not secret:
        return None
    return user, secret
