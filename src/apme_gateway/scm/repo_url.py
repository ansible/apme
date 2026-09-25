"""Repository URL normalization for cross-system project lookup."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_repo_url(repo_url: str) -> str:
    """Normalize a clone URL for stable comparison across portal and gateway.

    Strips trailing slashes, ``.git`` suffix, userinfo (``user:pass@``), and
    lowercases the hostname. Default ports are stripped (443 for ``https``,
    80 for ``http``) so explicit-default and implicit spellings share one
    identity; all other scheme/port combinations stay distinct (``http`` vs
    ``https``, non-default ports). The path keeps its case: ``Org/Repo``
    and ``org/repo`` are treated as distinct projects. Non-URL inputs are
    returned trimmed without scheme normalization.

    Args:
        repo_url: Raw SCM clone URL from catalog or API clients.

    Returns:
        Canonical ``scheme://host[:port]/org/repo`` form when parseable.
    """
    value = repo_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]

    try:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return value
        scheme = parsed.scheme.lower()
        try:
            port = parsed.port
        except ValueError:
            # Malformed port (e.g. ``:notaport``) — still strip userinfo from host.
            port = None
        netloc_no_userinfo = parsed.netloc.rsplit("@", 1)[-1]
        host = parsed.hostname.lower() if parsed.hostname else netloc_no_userinfo.lower()
        if port is not None:
            is_default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
            if not is_default:
                host = f"{host}:{port}"
        path = parsed.path.rstrip("/")
        return f"{scheme}://{host}{path}"
    except ValueError:
        return value
