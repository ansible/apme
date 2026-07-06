"""Repository URL normalization for cross-system project lookup."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_repo_url(repo_url: str) -> str:
    """Normalize a clone URL for stable comparison across portal and gateway.

    Strips trailing slashes, ``.git`` suffix, and lowercases the hostname.
    Non-URL inputs are returned trimmed without scheme normalization.

    Args:
        repo_url: Raw SCM clone URL from catalog or API clients.

    Returns:
        Canonical ``https://host/org/repo`` form when parseable.
    """
    value = repo_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]

    try:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return value
        host = parsed.hostname.lower() if parsed.hostname else parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"https://{host}{path}"
    except ValueError:
        return value
