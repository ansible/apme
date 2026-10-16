"""SCM repo-URL trust boundary — SSRF validation and token scoping.

Single home for the URL parsing, IP trust-boundary checks, DNS
resolution, and SCM token scoping helpers used by the scan driver
(:mod:`apme_gateway.scan.driver`). Extracted from the driver so security
reviewers can audit the trust boundary without reading scan
orchestration; external callers import from this module directly.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

#: One :func:`socket.getaddrinfo` result tuple:
#: ``(family, type, proto, canonname, sockaddr)``. Family/type arrive as
#: ``AddressFamily``/``SocketKind`` enums (int subclasses) so the alias
#: stays broad enough for both real getaddrinfo output and test fixtures.
GetaddrinfoResult = tuple[Any, ...]

_ALLOWED_SCHEMES = ("https://",)

#: Default hosts permitted to receive the global ``APME_SCM_TOKEN`` fallback.
#: Self-hosted forges opt in via ``APME_SCM_ALLOWED_HOSTS`` (comma-separated).
_DEFAULT_SCM_ALLOWED_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org"})

#: Explicit cloud-metadata endpoint (also link-local; blocked for clarity).
_CLOUD_METADATA_IP = "169.254.169.254"


def get_scm_allowed_hosts() -> set[str]:
    """Return hosts allowed to receive the global SCM token fallback.

    The allowlist is the cloud-forge defaults plus operator-configured
    self-hosted hosts from ``APME_SCM_ALLOWED_HOSTS`` (comma-separated,
    case-insensitive).

    Returns:
        Lowercased set of allowed hostnames.
    """
    allowed = set(_DEFAULT_SCM_ALLOWED_HOSTS)
    raw = os.environ.get("APME_SCM_ALLOWED_HOSTS", "")
    for entry in raw.split(","):
        host = entry.strip().lower()
        if host:
            allowed.add(host)
    return allowed


def is_global_token_allowed_for_host(host: str) -> bool:
    """Return True when the global SCM token may be sent to *host*.

    Args:
        host: Hostname to check (case-insensitive).

    Returns:
        True when *host* is in the allowlist from :func:`get_scm_allowed_hosts`.
    """
    return (host or "").strip().lower() in get_scm_allowed_hosts()


def _repo_host(repo_url: str) -> str:
    """Return the lowercased hostname for *repo_url*, or empty string.

    Args:
        repo_url: Clone URL under test.

    Returns:
        Lowercased hostname, or ``""`` when unparseable.
    """
    try:
        return (urlparse(repo_url).hostname or "").lower()
    except ValueError:
        return ""


def _ssrf_allow_private() -> bool:
    """Return True when on-prem private-IP clones are explicitly opted in.

    Reads ``APME_SSRF_ALLOW_PRIVATE`` (``1``/``true``/``yes``/``on``).
    Default (unset/any other value) still blocks RFC 1918 / ULA targets.

    Returns:
        True when private-IP repo targets are allowed.
    """
    return os.environ.get("APME_SSRF_ALLOW_PRIVATE", "").strip().lower() in {"1", "true", "yes", "on"}


def _reject_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, repo_url: str) -> None:
    """Raise when *ip* targets a non-public trust boundary.

    Blocks loopback, link-local (including cloud metadata
    ``169.254.169.254``), multicast, unspecified, and reserved addresses.
    Private (RFC 1918 / ULA) addresses are blocked by default and allowed
    only when ``APME_SSRF_ALLOW_PRIVATE=1`` opts in for on-prem forges.

    IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``) are unwrapped via
    ``ipv4_mapped`` and the embedded IPv4 address is checked as well, so
    mapped private/loopback literals cannot bypass the filter.

    Args:
        ip: Parsed IP address from a literal or DNS-resolved host.
        repo_url: Original URL for error messages (truncated).

    Raises:
        ValueError: When *ip* is not an allowed address.
    """
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        candidates.append(ip.ipv4_mapped)
    for candidate in candidates:
        if str(candidate) == _CLOUD_METADATA_IP:
            msg = f"Repo URL targets blocked cloud-metadata IP: {repo_url[:80]}"
            raise ValueError(msg)
        if (
            candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_unspecified
            or candidate.is_reserved
        ):
            msg = f"Repo URL targets blocked non-public IP {candidate}: {repo_url[:80]}"
            raise ValueError(msg)
    if not _ssrf_allow_private():
        for candidate in candidates:
            if candidate.is_private:
                msg = f"Repo URL targets blocked non-public IP {candidate}: {repo_url[:80]}"
                raise ValueError(msg)


def _parse_repo_url(value: str) -> tuple[str, str]:
    """Validate URL shape and return ``(value, host)`` without DNS.

    Checks scheme (https only), embedded userinfo, host presence, and port
    (implicit or explicit 443 only). DNS resolution and IP trust-boundary
    checks are left to the caller so sync and async entry points share
    one shape parser.

    Args:
        value: Stripped candidate clone URL (non-empty).

    Returns:
        Tuple of (value, lowercased hostname).

    Raises:
        ValueError: When the URL shape violates any trust-boundary rule.
    """
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        msg = f"Invalid repo URL: {value[:80]}: {exc}"
        raise ValueError(msg) from exc
    if (parsed.scheme or "").lower() != "https":
        msg = f"Only https:// clone URLs are allowed, got: {value[:60]}"
        raise ValueError(msg)
    if parsed.username is not None:
        msg = "Repo URL must not contain embedded credentials (userinfo)"
        raise ValueError(msg)
    host = parsed.hostname or ""
    if not host:
        msg = f"Repo URL has no host: {value[:80]}"
        raise ValueError(msg)
    try:
        port = parsed.port
    except ValueError as exc:
        msg = f"Invalid repo URL port: {value[:80]}: {exc}"
        raise ValueError(msg) from exc
    if port is not None and port != 443:
        msg = f"Repo URL uses non-standard port {port}; only 443 is allowed"
        raise ValueError(msg)
    return value, host


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse *host* as a literal IP address, or return None for DNS names.

    Args:
        host: Hostname that may be an IP literal (brackets tolerated).

    Returns:
        Parsed address, or ``None`` when *host* is a DNS name.
    """
    try:
        return ipaddress.ip_address(host.lower().strip("[]"))
    except ValueError:
        return None


def _validate_parsed_url(
    value: str,
    host: str,
    infos: list[GetaddrinfoResult] | None,
) -> str:
    """Apply shape/literal/resolved-IP trust-boundary checks (shared core).

    Single home for the checks shared by the sync and async SSRF
    validators: IP-literal hosts are checked directly without DNS;
    DNS names require a non-empty resolved-IP list whose every usable
    address passes :func:`_reject_blocked_ip` (see
    :func:`_check_resolved_ips`).

    Args:
        value: Stripped candidate clone URL (shape already validated).
        host: Hostname from :func:`_parse_repo_url`.
        infos: Raw :func:`socket.getaddrinfo` result list for DNS names,
            or ``None`` when *host* is an IP literal (no resolution needed).

    Returns:
        The validated URL unchanged.

    Raises:
        ValueError: When the literal or any resolved address is blocked,
            or when a DNS name resolves to nothing usable.
    """
    literal = _parse_ip_literal(host)
    if literal is not None:
        _reject_blocked_ip(literal, value)
        return value
    if not infos:
        msg = f"Repo URL host failed DNS resolution: {host.lower()}"
        raise ValueError(msg)
    _check_resolved_ips(infos, value)
    return value


def _check_resolved_ips(infos: list[GetaddrinfoResult], value: str) -> None:
    """Re-check every DNS-resolved address against the trust boundary.

    Args:
        infos: Raw :func:`socket.getaddrinfo` result tuples.
        value: Original URL for error messages (truncated).

    Raises:
        ValueError: When any resolved address is blocked or none resolve.
    """
    seen = False
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        ip_str = sockaddr[0] if sockaddr else ""
        if not ip_str:
            continue
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        seen = True
        _reject_blocked_ip(ip, value)
    if not seen:
        msg = f"Repo URL host failed DNS resolution: {value[:80]}"
        raise ValueError(msg)


async def _resolve_host_ips(host: str) -> list[GetaddrinfoResult]:
    """Resolve *host* without blocking the event loop.

    :func:`socket.getaddrinfo` is blocking; offload it via
    ``run_in_executor`` so concurrent scans never stall the loop on DNS.

    Args:
        host: Hostname to resolve.

    Returns:
        Raw ``getaddrinfo`` result list.

    Raises:
        ValueError: When DNS resolution fails.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
    except socket.gaierror as exc:
        msg = f"Repo URL host failed DNS resolution: {host.lower()}: {exc}"
        raise ValueError(msg) from exc


def validate_repo_url(repo_url: str) -> str:
    """Validate an HTTPS clone URL against SSRF trust boundaries.

    Rejects non-HTTPS schemes, embedded userinfo, non-standard ports
    (only implicit or explicit 443 is allowed), literal blocked IPs, and
    DNS-resolved blocked IPs (loopback, link-local, multicast,
    unspecified, reserved, private, cloud metadata). DNS is resolved via
    :func:`socket.getaddrinfo` and every returned address is re-checked.
    IPv4-mapped IPv6 addresses (``::ffff:a.b.c.d``) are unwrapped and the
    embedded IPv4 address is checked too. Private (RFC 1918 / ULA)
    targets are allowed only with ``APME_SSRF_ALLOW_PRIVATE=1`` (on-prem
    forges); the default still blocks.

    Best-effort pre-check only: DNS is re-resolved by ``git`` at
    clone/``ls-remote`` time (TOCTOU — the address checked here may not
    be the address git connects to), and HTTPS redirects can carry the
    connection to an unvalidated host. Do not rely on this check alone;
    enforce egress firewall rules for the GitHub/GitLab/forge allowlist
    in production. Clone/``ls-remote`` subprocesses additionally pin
    ``http.followRedirects=false`` and re-resolve the host immediately
    before spawn (see the driver), narrowing — not closing — the window.

    In async contexts prefer :func:`validate_repo_url_async`, which
    offloads the blocking ``getaddrinfo`` call via ``run_in_executor``.
    This sync variant blocks the calling thread on DNS.

    Args:
        repo_url: Candidate clone URL.

    Returns:
        The stripped URL when valid.

    Raises:
        ValueError: When the URL violates any trust-boundary rule.
    """
    value = (repo_url or "").strip()
    if not value:
        msg = "Repo URL must not be empty"
        raise ValueError(msg)
    value, host = _parse_repo_url(value)
    infos: list[GetaddrinfoResult] | None = None
    if _parse_ip_literal(host) is None:
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            msg = f"Repo URL host failed DNS resolution: {host.lower()}: {exc}"
            raise ValueError(msg) from exc
    return _validate_parsed_url(value, host, infos)


async def validate_repo_url_async(repo_url: str) -> str:
    """Async SSRF validator — same rules as :func:`validate_repo_url`.

    Blocking DNS (``getaddrinfo``) is offloaded via ``run_in_executor``
    so the event loop never stalls on resolution. All trust-boundary
    semantics (IPv4-mapped unwrapping, ``APME_SSRF_ALLOW_PRIVATE`` opt-in,
    best-effort TOCTOU/redirect limitation) match the sync variant — see
    its docstring.

    Args:
        repo_url: Candidate clone URL.

    Returns:
        The stripped URL when valid.

    Raises:
        ValueError: When the URL violates any trust-boundary rule.
    """
    value = (repo_url or "").strip()
    if not value:
        msg = "Repo URL must not be empty"
        raise ValueError(msg)
    value, host = _parse_repo_url(value)
    infos: list[GetaddrinfoResult] | None = None
    if _parse_ip_literal(host) is None:
        infos = await _resolve_host_ips(host)
    return _validate_parsed_url(value, host, infos)


def resolve_scm_token(
    repo_url: str,
    project_token: str | None,
    global_token: str | None,
) -> str | None:
    """Select the SCM token to send for *repo_url* with per-host scoping.

    A per-project token is always honored (operator explicitly scoped it
    to this project). The global ``APME_SCM_TOKEN`` fallback is only
    returned when the repo host matches the allowlist
    (:func:`get_scm_allowed_hosts`); otherwise fail closed so the global
    credential is never exfiltrated to an attacker-controlled host.

    Args:
        repo_url: Clone URL the token would be sent to.
        project_token: Per-project token, if any.
        global_token: Global fallback token, if any.

    Returns:
        The token to use, or ``None`` when neither is configured.

    Raises:
        ValueError: When only a global token is available but the host
            is not allowlisted — configure a per-project ``scm_token``
            or add the host to ``APME_SCM_ALLOWED_HOSTS``.
    """
    if project_token and project_token.strip():
        return project_token
    if not global_token or not global_token.strip():
        return None
    host = _repo_host(repo_url)
    if is_global_token_allowed_for_host(host):
        return global_token
    msg = (
        f"Global SCM token is scoped to {sorted(get_scm_allowed_hosts())}; "
        f"host {host!r} requires a per-project scm_token "
        "or APME_SCM_ALLOWED_HOSTS opt-in"
    )
    raise ValueError(msg)


def _is_global_token_value(token: str | None) -> bool:
    """Return True when *token* matches the global fallback value.

    Used to scope tokens merged by callers (``proj.scm_token or
    cfg.scm_token``) without changing their signatures: a merged token
    equal to ``APME_SCM_TOKEN`` is treated as global for allowlist
    enforcement.

    Args:
        token: Token passed to clone/fetch.

    Returns:
        True when *token* equals the non-empty global fallback.
    """
    if not token:
        return False
    glob = os.environ.get("APME_SCM_TOKEN", "")
    return bool(glob) and token == glob


class GlobalTokenScopeError(ValueError):
    """Global SCM token used on a non-allowlisted host (fail closed)."""


async def prepare_clone_inputs(
    repo_url: str,
    branch: object,
    scm_token: str | None,
) -> str:
    """Validate repo inputs shared by the ls-remote probe and clone paths (#23).

    Single home for the trust-boundary precondition both driver paths
    hand-rolled: SSRF validation of the (already userinfo-stripped) URL,
    per-host global-token scoping (fail closed), and branch-name shape
    validation. Callers map the ``ValueError`` to their own contract
    (probe returns ``None``; clone raises).

    Args:
        repo_url: Userinfo-stripped HTTPS clone URL.
        branch: Branch name to check out (must be a valid ref name).
        scm_token: Optional SCM token for private repository access.

    Returns:
        Validated URL for downstream use and revalidation.

    Raises:
        GlobalTokenScopeError: Global token on a non-allowlisted host
            (a ``ValueError`` subclass; callers that fail loud keep it).
        ValueError: On SSRF failure or an invalid branch name.
    """
    from apme_gateway.scm.urls import validate_branch_name  # noqa: PLC0415

    validated_url = await validate_repo_url_async(repo_url)
    if scm_token and _is_global_token_value(scm_token) and not is_global_token_allowed_for_host(_repo_host(repo_url)):
        msg = (
            f"Global SCM token is not allowed for host {_repo_host(repo_url)!r}; "
            "configure a per-project scm_token or add the host to APME_SCM_ALLOWED_HOSTS"
        )
        raise GlobalTokenScopeError(msg)
    if not isinstance(branch, str):
        msg = f"Invalid branch name: {branch!r}"
        raise ValueError(msg)
    try:
        validate_branch_name(branch)
    except ValueError as exc:
        msg = f"Invalid branch name: {branch[:60]}: {exc}"
        raise ValueError(msg) from exc
    return validated_url
