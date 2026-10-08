"""Project operation driver — clone, chunk, check/remediate via gRPC (ADR-037, ADR-039).

The gateway acts as a gRPC client to Engine for project-initiated operations.
On each invocation the project repo is shallow-cloned into a temporary directory,
chunked via the engine's ``yield_scan_chunks``, and streamed to Engine via
``FixSession`` (check mode omits ``fix_options`` on chunks; remediate mode sets
them on the first chunk).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import inspect
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse, urlunparse

import grpc
import grpc.aio

from apme.v1 import engine_pb2, engine_pb2_grpc
from apme.v1.common_pb2 import File as ProtoFile
from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.config_env import get_env_float, get_env_int
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_gateway.scan.operator_queue import OperatorAnswerQueue
from apme_gateway.scan.repo_url import (
    GlobalTokenScopeError,
    _parse_ip_literal,
    _parse_repo_url,
    _resolve_host_ips,
    _validate_parsed_url,
    prepare_clone_inputs,
)
from apme_gateway.scm.redaction import redact_credentials as _redact_credentials
from apme_gateway.scm.repo_url import normalize_repo_url

logger = logging.getLogger(__name__)

_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — matches Engine

# PE-25: operator-wait timeouts (env-overridable). The driver must never hang
# forever holding temp dirs/streams waiting on an operator who never responds.
# On timeout each wait mirrors its existing no-queue default:
#   begin    (FindingsReady assess pause) -> auto-begin (same as queue omitted)
#   escalate (AiTriageReady)              -> allow-all (same as queue omitted)
#   approve  (ProposalsReady)             -> decline-all (same as queue omitted)
_OP_BEGIN_TIMEOUT_DEFAULT_S = 600.0  # APME_OP_BEGIN_TIMEOUT_S
_OP_ESCALATE_TIMEOUT_DEFAULT_S = 600.0  # APME_OP_ESCALATE_TIMEOUT_S
_OP_APPROVE_TIMEOUT_DEFAULT_S = 1800.0  # APME_OP_APPROVE_TIMEOUT_S

# Backward-compatibility alias: identical to the canonical parser with the
# positive floor, kept because tests import this name (single canonical
# home: config_env). Drift-proof: partial application, no body to diverge.
# Production call sites use get_env_float(..., positive_only=True) directly.
_op_timeout = partial(get_env_float, positive_only=True)

# ADR-068: server enforces adaptive deadlines; no fixed client gRPC timeout.

# Aggregate scan caps (fail-fast before streaming to Engine). The Engine
# enforces its own PE-35 session caps; these gateway-side guards prevent a
# large clone from ballooning RAM in the chunk list or saturating gRPC.
_SCAN_MAX_FILES_DEFAULT = 2000  # APME_SCAN_MAX_FILES
_SCAN_MAX_BYTES_DEFAULT = 256 * 1024 * 1024  # APME_SCAN_MAX_BYTES (256MiB)


def _scan_max_files() -> int:
    """Return maximum files per project scan operation.

    Returns:
        File-count cap from ``APME_SCAN_MAX_FILES``.
    """
    return get_env_int(
        "APME_SCAN_MAX_FILES",
        _SCAN_MAX_FILES_DEFAULT,
        min_value=1,
    )


def _scan_max_bytes() -> int:
    """Return maximum aggregate content bytes per project scan operation.

    Returns:
        Byte cap from ``APME_SCAN_MAX_BYTES``.
    """
    return get_env_int(
        "APME_SCAN_MAX_BYTES",
        _SCAN_MAX_BYTES_DEFAULT,
        min_value=1,
    )


class ScanCapExceeded(ValueError):
    """Aggregate or per-file scan cap exceeded (maps to HTTP 413).

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handling (branch/URL validation paths, background task logging) is
    unchanged; REST handlers catch this type first to return 413.
    """


_FALSEY_OPTION_STRINGS = frozenset({"", "0", "false", "no", "off", "n"})
_TRUTHY_OPTION_STRINGS = frozenset({"1", "true", "yes", "on", "y"})


def coerce_option_bool(value: object, *, default: bool = False) -> bool:
    """Coerce untyped JSON/WebSocket option values to bool.

    ``bool("false")`` is True in Python; this helper treats common falsey
    string/number encodings as False so Gateway clients cannot accidentally
    enable flags via stringified JSON.

    Args:
        value: Raw option value from JSON/WebSocket options.
        default: Value used for ``None`` and unrecognized strings.

    Returns:
        Coerced boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _FALSEY_OPTION_STRINGS:
            return False
        if lowered in _TRUTHY_OPTION_STRINGS:
            return True
        return default
    return default


def derive_session_id(project_id: str) -> str:
    """Deterministic session ID so the engine reuses venvs across operations.

    Must remain a pure function of ``project_id`` (PE-32): session reuse is
    what makes the engine-side requirements-hash reconcile work — changed
    requirements refresh the existing venv (stale collections removed, new
    ones installed) instead of leaking state between sessions.

    Args:
        project_id: UUID hex of the project.

    Returns:
        First 16 hex characters of the SHA-256 hash.
    """
    return hashlib.sha256(project_id.encode()).hexdigest()[:16]


# SSRF trust-boundary helpers (URL parsing, IP checks, DNS resolution,
# token scoping) live in :mod:`apme_gateway.scan.repo_url`; this module
# imports only the names it uses. External callers import the canonical
# homes directly (``repo_url`` for trust-boundary helpers and patch
# targets, ``operator_queue`` for the answer queue).


_REMOTE_HEAD_CACHE: dict[str, tuple[float, str | None]] = {}
_REMOTE_HEAD_TTL = 60.0  # seconds
_REMOTE_HEAD_CACHE_MAX = 256
#: Short TTL for negative ``ls-remote`` results: a transient failure must not
#: poison refreshes for a full minute, but hammering the SCM on every poll
#: during an outage is a self-inflicted retry storm.
_REMOTE_HEAD_NEG_TTL = 10.0  # seconds
_REMOTE_HEAD_NEG_CACHE: dict[str, float] = {}


def _git_subprocess_env() -> dict[str, str]:
    """Return environment variables for git subprocesses.

    Git already inherits the process environment by default. This helper adds a
    small compatibility bridge so git will also trust a custom PEM bundle when
    the container only exposes it via generic CA variables such as
    ``SSL_CERT_FILE`` or ``REQUESTS_CA_BUNDLE``.

    Every git subprocess env also pins ``http.followRedirects=false`` so a
    validated clone URL cannot redirect (server-side) to an unvalidated
    host such as instance metadata — redirects fail closed instead of being
    followed. Pre-existing ``GIT_CONFIG_*`` entries are preserved via
    :func:`_merge_git_config_env`.

    Returns:
        Copy of ``os.environ`` with ``GIT_SSL_CAINFO`` populated when a CA bundle
        path is available via another standard environment variable, plus the
        redirect-pinning git-config entry.
    """
    env = os.environ.copy()
    if not env.get("GIT_SSL_CAINFO"):
        for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
            candidate = env.get(key, "").strip()
            if candidate:
                env["GIT_SSL_CAINFO"] = candidate
                break
    return _merge_git_config_env(env, [("http.followRedirects", "false")])


async def _revalidate_host_before_spawn(validated_url: str) -> None:
    """Re-resolve *validated_url* immediately before spawning git.

    Narrows the DNS-rebinding (TOCTOU) window between
    :func:`validate_repo_url_async` and the ``git`` subprocess (which
    re-resolves DNS itself): the host is resolved again and the fresh
    address set must still pass :func:`_validate_parsed_url`. Any change
    to a blocked address (loopback, link-local, metadata, private, …)
    aborts before git is spawned. IP literals need no re-resolution.

    Residual risk: git performs a third resolution at connect time, so a
    rebinding between this check and connect can still reach a blocked
    address. Production deployments must enforce egress-firewall rules
    for the forge allowlist; credentials travel via per-origin
    ``http.extraHeader`` (never URL-embedded) to limit exposure.

    Args:
        validated_url: Already-validated clone URL.

    Raises:
        ValueError: When re-resolution fails or yields blocked addresses.
    """
    value, host = _parse_repo_url(validated_url)
    if _parse_ip_literal(host) is not None:
        _validate_parsed_url(value, host, None)
        return
    fresh_infos = await _resolve_host_ips(host)
    try:
        _validate_parsed_url(value, host, fresh_infos)
    except ValueError as exc:
        msg = f"Repo URL re-resolution failed trust boundary: {exc}"
        raise ValueError(msg) from exc


def _scm_basic_credentials(
    repo_url: str,
    token: str,
    *,
    scm_provider: str | None = None,
) -> tuple[str, str]:
    """Select the HTTP Basic (username, password) pair for an SCM token.

    Supports multiple SCM providers with their respective auth schemes:
    - GitHub: ``x-access-token:TOKEN``
    - GitLab: ``oauth2:TOKEN``
    - Bitbucket access token: ``x-token-auth:TOKEN``
    - Bitbucket app password (``user:pass``): ``user:pass`` as credentials
    - Others: ``git:TOKEN`` (generic fallback)

    When *scm_provider* is set, it takes precedence over hostname heuristics
    so self-hosted Bitbucket/GitLab hosts authenticate correctly.

    Args:
        repo_url: Original HTTPS clone URL (used for provider heuristics).
        token: SCM token (e.g., PAT, OAuth token, or ``user:pass``).
        scm_provider: Optional explicit provider (``github`` / ``gitlab`` /
            ``bitbucket``).

    Returns:
        Raw (username, password) tuple — callers encode as needed.
    """
    from apme_gateway.scm.urls import split_user_pass_token

    parsed = urlparse(repo_url)
    hostname = parsed.hostname or ""
    provider = (scm_provider or "").lower().strip()
    host_l = hostname.lower()

    user_pass = split_user_pass_token(token)
    if user_pass is not None:
        use_user_pass = provider in {"bitbucket", "gitlab"} or (
            not provider and ("bitbucket" in host_l or "gitlab" in host_l)
        )
        if use_user_pass:
            return user_pass

    if provider == "github" or (not provider and "github" in host_l):
        return ("x-access-token", token)
    if provider == "gitlab" or (not provider and "gitlab" in host_l):
        return ("oauth2", token)
    if provider == "bitbucket" or (not provider and "bitbucket" in host_l):
        return ("x-token-auth", token)
    return ("git", token)


def _git_origin(repo_url: str) -> str:
    """Return the ``scheme://host[:port]`` origin for *repo_url*.

    Args:
        repo_url: HTTPS clone URL.

    Returns:
        Origin string used to scope git ``http.<origin>.extraHeader`` keys.

    Raises:
        ValueError: When the URL contains a malformed port.
    """
    parsed = urlparse(repo_url)
    host = parsed.hostname or ""
    origin = f"{parsed.scheme}://{host}"
    try:
        port = parsed.port
    except ValueError:
        raise
    if port:
        origin += f":{port}"
    return origin


def _url_embedded_userpass(repo_url: str) -> tuple[str, str] | None:
    """Return username/password embedded in a clone URL's userinfo.

    Args:
        repo_url: Raw clone URL, possibly with embedded userinfo.

    Returns:
        ``(username, password)`` tuple, or ``None`` when no userinfo is present.
    """
    try:
        parsed = urlparse(repo_url)
    except ValueError:
        return None
    if not parsed.username:
        return None
    user = unquote(parsed.username)
    password = unquote(parsed.password or "")
    return user, password


def _auth_cache_marker(scm_token: str | None, url_userpass: tuple[str, str] | None) -> str:
    """Build a stable cache marker for credential-aware SCM lookups.

    Args:
        scm_token: Explicit SCM token, if any.
        url_userpass: URL-embedded ``(username, password)`` credentials.

    Returns:
        Empty string when unauthenticated, otherwise ``:auth:<hash>``.
    """
    if scm_token:
        material = scm_token
    elif url_userpass is not None:
        material = f"{url_userpass[0]}:{url_userpass[1]}"
    else:
        return ""
    token_hash = hashlib.sha256(material.encode()).hexdigest()[:16]
    return f":auth:{token_hash}"


def _apply_git_auth_env(
    env: dict[str, str],
    repo_url: str,
    scm_token: str | None,
    url_userpass: tuple[str, str] | None = None,
    *,
    scm_provider: str | None = None,
) -> dict[str, str]:
    """Merge per-origin git auth headers into *env* when a token is available.

    Args:
        env: Base git subprocess environment.
        repo_url: Stripped HTTPS clone URL.
        scm_token: Explicit SCM token, if any.
        url_userpass: URL-embedded credentials when *scm_token* is absent.
        scm_provider: Optional explicit SCM provider.

    Returns:
        Environment with auth headers merged, unchanged when no token.
    """
    if scm_token:
        auth = _git_auth_env(repo_url, scm_token, scm_provider=scm_provider)
    elif url_userpass is not None:
        username, password = url_userpass
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        auth = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{_git_origin(repo_url)}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: Basic {encoded}",
        }
    else:
        return env
    try:
        auth_count = int(auth.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        auth_count = 0
    pairs = [
        (auth[f"GIT_CONFIG_KEY_{i}"], auth[f"GIT_CONFIG_VALUE_{i}"])
        for i in range(auth_count)
        if f"GIT_CONFIG_KEY_{i}" in auth and f"GIT_CONFIG_VALUE_{i}" in auth
    ]
    return _merge_git_config_env(env, pairs)


def _strip_url_userinfo(repo_url: str) -> str:
    """Remove embedded ``user:pass@`` credentials from a clone URL.

    Stored project URLs may contain userinfo; passing them verbatim into
    ``git clone``/``ls-remote`` argv exposes the secret in process listings.
    Token auth travels via the per-origin ``http.extraHeader`` env entry
    instead, so the userinfo component is always safe to drop.

    Args:
        repo_url: Raw clone URL, possibly with embedded userinfo.

    Returns:
        URL with the userinfo component removed; unchanged when none present.
    """
    try:
        parsed = urlparse(repo_url)
    except ValueError:
        return repo_url
    netloc = parsed.netloc
    if "@" not in netloc:
        return repo_url
    host = parsed.hostname or ""
    if not host:
        return repo_url
    logger.warning("Stripping embedded credentials from repo URL for host %s", host)
    return urlunparse(parsed._replace(netloc=netloc.rsplit("@", 1)[-1]))


def _merge_git_config_env(base: dict[str, str], extra_pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Merge ``GIT_CONFIG_KEY_n/VALUE_n`` pairs into a copy of *base*.

    Existing numbered entries are preserved; new pairs are appended at the
    next indices and ``GIT_CONFIG_COUNT`` is updated. A missing or
    unparseable count is treated as zero (numbered entries are still kept).

    Args:
        base: Base environment mapping (e.g. from :func:`_git_subprocess_env`).
        extra_pairs: ``(key, value)`` config pairs to append.

    Returns:
        New environment mapping with the merged git-config entries.
    """
    merged = dict(base)
    try:
        count = int(merged.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    if count < 0:
        count = 0
    for key, value in extra_pairs:
        merged[f"GIT_CONFIG_KEY_{count}"] = key
        merged[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    merged["GIT_CONFIG_COUNT"] = str(count)
    return merged


def _git_auth_env(
    repo_url: str,
    token: str,
    *,
    scm_provider: str | None = None,
) -> dict[str, str]:
    """Build git-config env carrying the SCM token as an HTTP header.

    The token travels in ``GIT_CONFIG_*`` environment (a per-origin
    ``http.<origin>.extraHeader`` with an ``AUTHORIZATION: Basic`` value)
    instead of the clone URL, so it never appears in subprocess argv,
    process listings, or error output. Scoping to the repo origin keeps the
    credential from being sent to any other host git contacts (e.g.
    redirects, submodules). Merge with :func:`_merge_git_config_env` so
    pre-existing ``GIT_CONFIG_*`` entries are preserved.

    Args:
        repo_url: HTTPS clone URL (used for provider heuristics and origin
            scoping).
        token: SCM token.
        scm_provider: Optional explicit provider.

    Returns:
        Env mapping with ``GIT_CONFIG_COUNT/KEY_0/VALUE_0`` to merge into
        the git subprocess environment.
    """
    username, password = _scm_basic_credentials(repo_url, token, scm_provider=scm_provider)
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{_git_origin(repo_url)}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: Basic {encoded}",
    }


def _inject_token_in_url(
    repo_url: str,
    token: str,
    *,
    scm_provider: str | None = None,
) -> str:
    """Inject an authentication token into an HTTPS git URL.

    .. deprecated::
        Prefer :func:`_git_auth_env` for subprocess calls so tokens stay out
        of argv, process listings, and error output. This helper remains only
        for contexts where a URL is required, and for its unit tests — do not
        adopt it for new subprocess call sites.

    Supports multiple SCM providers with their respective auth schemes:
    - GitHub: ``x-access-token:TOKEN``
    - GitLab: ``oauth2:TOKEN``
    - Bitbucket access token: ``x-token-auth:TOKEN``
    - Bitbucket app password (``user:pass``): ``user:pass`` as URL credentials
    - Others: ``git:TOKEN`` (generic fallback)

    When *scm_provider* is set, it takes precedence over hostname heuristics
    so self-hosted Bitbucket/GitLab hosts authenticate correctly.

    Args:
        repo_url: Original HTTPS clone URL.
        token: SCM token (e.g., PAT, OAuth token, or ``user:pass``).
        scm_provider: Optional explicit provider (``github`` / ``gitlab`` /
            ``bitbucket``).

    Returns:
        URL with embedded credentials.
    """
    parsed = urlparse(repo_url)
    hostname = parsed.hostname or ""
    username, password = _scm_basic_credentials(repo_url, token, scm_provider=scm_provider)
    # Percent-encode credentials to handle special characters (@, :, /, etc.)
    netloc_with_auth = f"{quote(username, safe='')}:{quote(password, safe='')}@{hostname}"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        netloc_with_auth += f":{port}"
    return urlunparse(parsed._replace(netloc=netloc_with_auth))


def _evict_remote_head_entries(now: float) -> None:
    """Make room in the ``ls-remote`` caches without dropping everything.

    Expired positive entries go first; when still full, the single oldest
    entry (positive or negative) is evicted. Clearing the whole map on one
    miss turns a full cache into a subprocess-per-poll storm.

    Args:
        now: Current ``time.monotonic()`` reading.
    """
    expired = [k for k, (ts, _) in _REMOTE_HEAD_CACHE.items() if (now - ts) >= _REMOTE_HEAD_TTL]
    for k in expired:
        del _REMOTE_HEAD_CACHE[k]
    expired_neg = [k for k, ts in _REMOTE_HEAD_NEG_CACHE.items() if (now - ts) >= _REMOTE_HEAD_NEG_TTL]
    for k in expired_neg:
        del _REMOTE_HEAD_NEG_CACHE[k]
    while len(_REMOTE_HEAD_CACHE) + len(_REMOTE_HEAD_NEG_CACHE) >= _REMOTE_HEAD_CACHE_MAX:
        oldest_key: str | None = None
        oldest_ts = float("inf")
        for k, (ts, _) in _REMOTE_HEAD_CACHE.items():
            if ts < oldest_ts:
                oldest_ts, oldest_key = ts, k
        oldest_neg: str | None = None
        for k, ts in _REMOTE_HEAD_NEG_CACHE.items():
            if ts < oldest_ts:
                oldest_ts, oldest_key, oldest_neg = ts, k, k
        if oldest_key is None:
            break
        if oldest_neg is not None and oldest_key == oldest_neg:
            del _REMOTE_HEAD_NEG_CACHE[oldest_key]
        else:
            del _REMOTE_HEAD_CACHE[oldest_key]


async def fetch_remote_head(
    repo_url: str,
    branch: str,
    scm_token: str | None = None,
    *,
    scm_provider: str | None = None,
) -> str | None:
    """Query the remote for the HEAD commit SHA of *branch* without cloning.

    Uses ``git ls-remote`` which only contacts the server for ref advertisement.
    Hits are cached for 60 seconds per (repo_url, branch, credential); misses
    are cached for 10 seconds so a flapping SCM does not cause a subprocess
    per poll while still recovering quickly.

    SSRF hardening: the URL passes :func:`validate_repo_url_async`, the git
    subprocess env pins ``http.followRedirects=false`` (see
    :func:`_git_subprocess_env`) so redirects fail closed, and the host is
    re-resolved immediately before spawn
    (:func:`_revalidate_host_before_spawn`) so a rebinding to a blocked
    address aborts. Residual TOCTOU (git re-resolves DNS itself) requires
    egress-firewall enforcement for the forge allowlist in production.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch name to resolve.
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for auth username selection.

    Returns:
        40-char hex SHA, or ``None`` if the lookup fails.

    Raises:
        GlobalTokenScopeError: When only a global SCM token is available
            for a non-allowlisted host (fail closed, matching
            :func:`clone_repo` — configure a per-project ``scm_token``
            or add the host to ``APME_SCM_ALLOWED_HOSTS``).
            Other validation failures return ``None``.
    """
    url_userpass = _url_embedded_userpass(repo_url) if not scm_token else None
    repo_url = _strip_url_userinfo(repo_url)
    try:
        validated_url = await prepare_clone_inputs(repo_url, branch, scm_token)
    except GlobalTokenScopeError:
        # Fail closed (raise) so the global credential never reaches an
        # attacker host via a silent unauthenticated probe — same as clone_repo.
        raise
    except ValueError:
        return None

    # Key authenticated lookups on a credential hash: two tokens with
    # different access must not share one entry.
    token_marker = _auth_cache_marker(scm_token, url_userpass)
    cache_key = f"{normalize_repo_url(repo_url)}:{branch}{token_marker}:{scm_provider or ''}"
    now = time.monotonic()
    cached = _REMOTE_HEAD_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _REMOTE_HEAD_TTL:
        return cached[1]
    neg_ts = _REMOTE_HEAD_NEG_CACHE.get(cache_key)
    if neg_ts is not None and (now - neg_ts) < _REMOTE_HEAD_NEG_TTL:
        return None

    # Pass the token via a per-origin http.extraHeader env entry so it never
    # appears in argv; pre-existing GIT_CONFIG_* entries are preserved
    # (including the http.followRedirects=false redirect pinning).
    env = _git_subprocess_env()
    try:
        env = _apply_git_auth_env(
            env,
            repo_url,
            scm_token,
            url_userpass,
            scm_provider=scm_provider,
        )
    except ValueError:
        logger.debug("ls-remote auth env failed for %s branch %s", repo_url, branch, exc_info=True)
        return None
    try:
        await _revalidate_host_before_spawn(validated_url)
    except ValueError:
        logger.debug("ls-remote re-resolution failed for %s branch %s", repo_url, branch, exc_info=True)
        return None
    cmd = ["git", "ls-remote", "--exit-code", repo_url, f"refs/heads/{branch}"]
    loop = asyncio.get_running_loop()
    sha: str | None = None
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            ),
        )
        if result.returncode == 0 and result.stdout.strip():
            sha = result.stdout.strip().split()[0]
    except Exception:  # noqa: BLE001
        logger.debug("ls-remote failed for %s branch %s", repo_url, branch, exc_info=True)

    now = time.monotonic()
    if len(_REMOTE_HEAD_CACHE) + len(_REMOTE_HEAD_NEG_CACHE) >= _REMOTE_HEAD_CACHE_MAX:
        _evict_remote_head_entries(now)

    if sha is not None:
        _REMOTE_HEAD_NEG_CACHE.pop(cache_key, None)
        _REMOTE_HEAD_CACHE[cache_key] = (now, sha)
    else:
        # Short-TTL negative entry: throttle failure storms without
        # poisoning refreshes for a full minute.
        _REMOTE_HEAD_NEG_CACHE[cache_key] = now
    return sha


def get_clone_head(clone_dir: str) -> str | None:
    """Read the HEAD commit SHA from a cloned repo.

    Args:
        clone_dir: Path to the cloned repository.

    Returns:
        40-char hex SHA, or ``None`` on failure.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=clone_dir,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        logger.debug("rev-parse HEAD failed in %s", clone_dir, exc_info=True)
    return None


async def clone_repo(
    repo_url: str,
    branch: str,
    dest: str,
    scm_token: str | None = None,
    *,
    scm_provider: str | None = None,
) -> None:
    """Shallow-clone an SCM repo into *dest*.

    Only ``https://`` URLs are permitted to prevent SSRF via ``file://``,
    ``ssh://``, or other git transports. The URL passes
    :func:`validate_repo_url_async`, the git subprocess env pins
    ``http.followRedirects=false`` (redirects fail closed instead of
    carrying the connection to an unvalidated host), and the host is
    re-resolved immediately before spawn
    (:func:`_revalidate_host_before_spawn`) so a DNS rebinding to a
    blocked address aborts. Residual TOCTOU (git re-resolves DNS itself)
    requires egress-firewall enforcement for the forge allowlist in
    production.

    The clone passes ``--filter=blob:limit=`` to reduce unneeded blob
    traffic, but a checking-out clone lazily fetches every blob HEAD
    needs — including oversized ones — so the filter is not a disk
    bound. The enforcing gate is the post-clone on-disk size walk
    (:func:`_temp_dir_disk_bytes`, surfaced via
    :func:`run_project_operation` caps), which rejects oversized clones
    before scanning.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch to check out.
        dest: Target directory (must not already exist).
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for auth username selection.

    Raises:
        GlobalTokenScopeError: If only a global SCM token is available
            for a non-allowlisted host (fail closed).
        ValueError: If *repo_url* uses a disallowed scheme or *branch* is
            not a valid git ref name.
        RuntimeError: If ``git clone`` fails or times out.
    """
    url_userpass = _url_embedded_userpass(repo_url) if not scm_token else None
    repo_url = _strip_url_userinfo(repo_url)
    try:
        validated_url = await prepare_clone_inputs(repo_url, branch, scm_token)
    except GlobalTokenScopeError:
        raise
    except ValueError as exc:
        if str(exc).startswith("Invalid branch name"):
            raise
        msg = f"Invalid repository URL: {exc}"
        raise ValueError(msg) from exc

    # Pass the token via a per-origin http.extraHeader env entry so it never
    # appears in argv; pre-existing GIT_CONFIG_* entries are preserved
    # (including the http.followRedirects=false redirect pinning).
    env = _git_subprocess_env()
    try:
        env = _apply_git_auth_env(
            env,
            repo_url,
            scm_token,
            url_userpass,
            scm_provider=scm_provider,
        )
    except ValueError as exc:
        msg = f"Invalid repository URL for authentication: {repo_url[:60]}"
        raise ValueError(msg) from exc
    try:
        await _revalidate_host_before_spawn(validated_url)
    except ValueError as exc:
        msg = f"Invalid repository URL: {exc}"
        raise ValueError(msg) from exc
    # Blob-filtered shallow clone (reduces unneeded blob traffic; NOT a
    # disk bound — checkout fetches every blob HEAD needs). Oversized
    # clones are rejected by the post-clone on-disk size walk.
    blob_limit = min(_scan_max_bytes(), 50 * 1024 * 1024)
    cmd = [
        "git",
        "clone",
        "--branch",
        branch,
        "--single-branch",
        "--depth",
        "1",
        f"--filter=blob:limit={blob_limit}",
        repo_url,
        dest,
    ]
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            ),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git clone timed out after 120s for branch {branch[:60]}") from exc
    if result.returncode != 0:
        safe_stderr = _redact_credentials(result.stderr)[:500]
        raise RuntimeError(f"git clone failed (exit {result.returncode}): {safe_stderr}")


def _temp_dir_disk_bytes(temp_dir: str) -> int:
    """Return total on-disk bytes under *temp_dir* (best-effort).

    Walks the cloned tree summing regular-file sizes. VCS metadata dirs
    (``.git``, ``.hg``) are pruned — they are not scanned content and must
    not consume the content cap. ``os.lstat`` never follows symlinks and
    only regular files are counted, so a symlink pointing at a large
    target cannot inflate the total. Errors on individual entries are
    ignored (fail-open per file — the streaming content caps below remain
    authoritative).

    Args:
        temp_dir: Cloned repository directory.

    Returns:
        Summed file sizes in bytes.
    """
    total = 0
    for root, dirs, files in os.walk(temp_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d not in (".git", ".hg")]
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            total += st.st_size
    return total


ProgressCallback = Callable[[engine_pb2.SessionEvent], Coroutine[Any, Any, None]]


def _collect_scan_chunks_capped(
    temp_dir: str,
    *,
    scan_id: str,
    session_id: str,
    ansible_version: str = "",
    collection_specs: list[str] | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
    max_files: int = 0,
    max_bytes: int = 0,
) -> list[Any]:
    """Walk the clone and collect scan chunks with incremental cap enforcement.

    Blocking FS I/O — callers run this off the event loop via
    ``run_in_executor`` (see :func:`run_project_operation`). Caps are
    enforced *during* generation: the ``yield_scan_chunks`` generator is
    consumed one chunk at a time and each file's content length is counted
    **before** its chunk is appended, so an over-cap scan raises before
    the offending chunk's bytes are retained (peak RAM stays bounded
    instead of materializing the full chunk list first).

    Args:
        temp_dir: Cloned repository directory.
        scan_id: Scan identifier stamped on chunks.
        session_id: Engine session identifier.
        ansible_version: Target ansible-core version.
        collection_specs: Collection install specs.
        galaxy_servers: Global Galaxy server defs (ADR-045).
        max_files: Aggregate file-count cap (``APME_SCAN_MAX_FILES``).
        max_bytes: Aggregate content-byte cap (``APME_SCAN_MAX_BYTES``).

    Returns:
        Collected chunk list, cap-checked.

    Raises:
        ScanCapExceeded: When the file or byte cap is exceeded mid-stream.
    """
    chunks: list[Any] = []
    file_count = 0
    byte_count = 0
    for chunk in yield_scan_chunks(
        temp_dir,
        scan_id=scan_id,
        project_root_name="project",
        ansible_core_version=ansible_version or None,
        collection_specs=collection_specs or None,
        session_id=session_id,
        galaxy_servers=galaxy_servers,
    ):
        for f in chunk.files:
            file_count += 1
            try:
                content_len = len(f.content)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — defensive; treat as empty
                content_len = 0
            byte_count += content_len
            if file_count > max_files:
                raise ScanCapExceeded(f"Scan file limit exceeded: {file_count} files (max {max_files})")
            if byte_count > max_bytes:
                raise ScanCapExceeded(f"Scan size limit exceeded: {byte_count} bytes (max {max_bytes} bytes)")
        chunks.append(chunk)
    return chunks


async def run_project_operation(
    *,
    project_id: str,
    repo_url: str,
    branch: str,
    engine_address: str,
    remediate: bool = False,
    ansible_version: str = "",
    collection_specs: list[str] | None = None,
    enable_ai: bool = True,
    ai_model: str = "",
    interactive: bool = False,
    assess_pause: bool = False,
    progress_callback: ProgressCallback | None = None,
    approval_queue: OperatorAnswerQueue[list[str]] | None = None,
    begin_remediate_queue: OperatorAnswerQueue[None] | None = None,
    escalate_ai_queue: OperatorAnswerQueue[list[dict[str, object]]] | None = None,
    on_begin_timeout: Callable[[], None] | None = None,
    on_escalate_timeout: Callable[[], None] | None = None,
    on_approve_timeout: Callable[[], Any] | None = None,
    scan_id: str | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
    scm_token: str | None = None,
    scm_provider: str | None = None,
) -> tuple[str, engine_pb2.SessionResult | None, str]:
    """Clone a project repo and run check or remediate via Engine ``FixSession``.

    Check mode (``remediate=False``) sends chunks without ``fix_options``
    unless ``assess_pause`` is set (ADR-064 — attaches FixOptions so the
    engine can pause with FindingsReady).

    Args:
        project_id: UUID of the project (used to derive session_id).
        repo_url: SCM clone URL.
        branch: Branch to clone.
        engine_address: ``host:port`` for the Engine gRPC service.
        remediate: When True, attach fix options and handle AI approval flow.
        ansible_version: Target ansible-core version.
        collection_specs: Collection install specs.
        enable_ai: Enable AI remediation tier (remediate mode only).
        ai_model: AI model identifier (remediate mode only).
        interactive: When True, Tier 1 fixes await approval (ADR-062 Phase 3).
            Independent of ``assess_pause`` — do not OR the flags.
        assess_pause: When True, pause after FindingsReady until
            ``begin_remediate_queue`` is signalled (ADR-064).
        progress_callback: Optional async callable for each ``SessionEvent``.
        approval_queue: Queue of approved proposal IDs for remediate mode when
            the engine emits ``ProposalsReady`` (Tier 1 ``t1-*`` when
            ``interactive=True``, and/or Tier 2 ``ai-*`` when AI proposes).
            If omitted, proposals are auto-declined so the stream does not hang.
        begin_remediate_queue: Signalled to leave assess pause (ADR-064).
            If omitted while ``assess_pause``, auto-begins on
            ``FindingsReady``. Proposal approve/decline is controlled
            separately by ``approval_queue`` (omitted → auto-decline).
        escalate_ai_queue: Queue of ``{path, rule_ids}`` target dicts to leave
            AI escalation triage. If omitted when ``AiTriageReady`` arrives,
            all candidate paths are escalated (allow-all).
        on_begin_timeout: Optional callback invoked when the begin wait
            times out and the driver auto-begins. The caller applies the
            same registry transition as ``POST /begin-remediate``
            (``scan_type`` → remediate, retire the pending future) so the
            run is reported truthfully and a late Begin cannot pair a new
            proposal list with an old gate. Invoking it is idempotent —
            when the wait was satisfied by the bridge instead, the
            transition is already applied and the callback is a no-op.
        on_escalate_timeout: Optional callback invoked when the AI-escalate
            wait times out and the driver falls back to allow-all. The
            caller retires the pending escalate future so a late
            ``POST /escalate-ai`` is rejected instead of silently dropped.
        on_approve_timeout: Optional callback (sync or async) invoked when
            the approval wait times out and the driver declines all
            proposals. The caller resolves and retires the current
            approval gate and gate-commits decline-all for the offered
            IDs, so a late ``POST /approve`` cannot commit decisions the
            Engine already declined.
        scan_id: Optional pre-generated scan ID; one is created if omitted.
        galaxy_servers: Global Galaxy server defs to inject into scan metadata (ADR-045).
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for clone auth selection.

    Returns:
        Tuple of (scan_id, SessionResult or None, clone_commit_sha).
        The commit SHA is the HEAD of the cloned repo (empty string on failure).

    Raises:
        asyncio.CancelledError: When the driving task is cancelled; closes the
            FixSession command stream before propagating.
        ScanCapExceeded: When aggregate scan caps (``APME_SCAN_MAX_FILES`` /
            ``APME_SCAN_MAX_BYTES``) are exceeded (a ``ValueError``
            subclass, so existing ``except ValueError`` handling is
            unchanged). The on-disk size check below fails fast before
            chunking (the blob filter only reduces traffic), and the
            streaming content counters inside
            :func:`_collect_scan_chunks_capped` bound RAM/gRPC held in the
            chunk list.

    Caps are enforced during streaming — inside the executor helper each
    file's content length is counted **before** its chunk is appended, so
    an over-cap scan raises before the offending chunk's bytes are
    retained for the FixSession stream.
    """
    if scan_id is None:
        scan_id = uuid.uuid4().hex
    session_id = derive_session_id(project_id)
    prefix = "apme_project_remediate_" if remediate or assess_pause else "apme_project_check_"
    temp_dir = tempfile.mkdtemp(prefix=prefix)

    try:
        await clone_repo(repo_url, branch, temp_dir, scm_token=scm_token, scm_provider=scm_provider)
        loop = asyncio.get_running_loop()
        clone_sha = await loop.run_in_executor(None, get_clone_head, temp_dir) or ""

        max_files = _scan_max_files()
        max_bytes = _scan_max_bytes()
        # Fail fast on oversized clones before chunking: this on-disk
        # aggregate check (best-effort — counting cannot prevent clone
        # disk use, only the downstream RAM/gRPC blowup) gates chunking.
        disk_bytes = await loop.run_in_executor(None, _temp_dir_disk_bytes, temp_dir)
        if disk_bytes > max_bytes:
            raise ScanCapExceeded(f"Scan size limit exceeded: {disk_bytes} bytes on disk (max {max_bytes} bytes)")

        # yield_scan_chunks walks the FS and reads file bytes synchronously;
        # collect off-loop so the event loop never blocks on clone I/O.
        # Caps (APME_SCAN_MAX_FILES / APME_SCAN_MAX_BYTES) are enforced
        # incrementally inside the helper during generation — each file is
        # counted before its chunk is appended, so an over-cap scan raises
        # before the offending chunk's bytes are retained (no full-RAM
        # materialization before the 413).
        chunks: list[Any] = await loop.run_in_executor(
            None,
            partial(
                _collect_scan_chunks_capped,
                temp_dir,
                scan_id=scan_id,
                session_id=session_id,
                ansible_version=ansible_version,
                collection_specs=collection_specs,
                galaxy_servers=galaxy_servers,
                max_files=max_files,
                max_bytes=max_bytes,
            ),
        )

        attach_fix = remediate or assess_pause
        if attach_fix and chunks:
            fix_opts = engine_pb2.FixOptions(
                ansible_core_version=ansible_version,
                collection_specs=collection_specs or [],
                enable_ai=enable_ai,
                ai_model=ai_model,
                galaxy_servers=galaxy_servers or [],
                interactive=interactive,
                assess_pause=assess_pause,
            )
            chunks[0].fix_options.CopyFrom(fix_opts)

        command_queue: asyncio.Queue[engine_pb2.SessionCommand | None] = asyncio.Queue()

        for chunk in chunks:
            await command_queue.put(engine_pb2.SessionCommand(upload=chunk))

        async def _command_stream() -> AsyncIterator[engine_pb2.SessionCommand]:
            while True:
                cmd = await command_queue.get()
                if cmd is None:
                    return
                yield cmd

        channel = grpc.aio.insecure_channel(
            engine_address,
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )
        try:
            stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]

            response_stream = stub.FixSession(_command_stream())

            result: engine_pb2.SessionResult | None = None
            # Operator-timeout fallbacks change spend/authorization semantics;
            # when *both* fire, the run paid full AI cost for zero applied
            # fixes — pair them into one degraded signal at result time so
            # the pairing is visible instead of two isolated timeouts.
            escalate_timed_out = False
            approve_timed_out = False
            async for event in response_stream:
                kind = event.WhichOneof("event")
                begin_generation: int | None = None
                escalate_generation: int | None = None
                approval_generation: int | None = None
                if kind == "findings" and begin_remediate_queue is not None:
                    begin_generation = begin_remediate_queue.begin_prompt()
                elif kind == "ai_triage" and escalate_ai_queue is not None:
                    escalate_generation = escalate_ai_queue.begin_prompt()
                elif kind == "proposals" and approval_queue is not None:
                    approval_generation = approval_queue.begin_prompt()

                if progress_callback:
                    await progress_callback(event)

                if kind == "findings":
                    if begin_remediate_queue is not None:
                        begin_timeout = get_env_float(
                            "APME_OP_BEGIN_TIMEOUT_S", _OP_BEGIN_TIMEOUT_DEFAULT_S, positive_only=True
                        )
                        # Timeout (None) mirrors the no-queue default: auto-begin.
                        # The signal carries no payload — a bridge-forwarded
                        # begin is None too — so invoke the timeout callback
                        # unconditionally. It is idempotent: a no-op when
                        # POST /begin-remediate already applied the registry
                        # transition.
                        await begin_remediate_queue.next_answer(
                            begin_timeout,
                            "BeginRemediate operator wait",
                            "auto-beginning",
                            expected_generation=begin_generation,
                        )
                        if on_begin_timeout is not None:
                            try:
                                on_begin_timeout()
                            except Exception:
                                logger.exception("on_begin_timeout callback failed")
                    await command_queue.put(
                        engine_pb2.SessionCommand(begin_remediate=engine_pb2.BeginRemediateRequest())
                    )
                elif kind == "ai_triage":
                    target_dicts: list[dict[str, object]] | None = None
                    if escalate_ai_queue is not None:
                        escalate_timeout = get_env_float(
                            "APME_OP_ESCALATE_TIMEOUT_S", _OP_ESCALATE_TIMEOUT_DEFAULT_S, positive_only=True
                        )
                        # Timeout (None) mirrors the no-queue default: allow-all.
                        target_dicts = await escalate_ai_queue.next_answer(
                            escalate_timeout,
                            "AI-escalate operator wait",
                            "escalating all candidates",
                            expected_generation=escalate_generation,
                        )
                        if target_dicts is None:
                            escalate_timed_out = True
                            if on_escalate_timeout is not None:
                                try:
                                    on_escalate_timeout()
                                except Exception:
                                    logger.exception("on_escalate_timeout callback failed")
                    if target_dicts is None:
                        # No queue (or timed out) — escalate every candidate path (allow-all).
                        paths = sorted({c.path for c in event.ai_triage.candidates if c.path})
                        target_dicts = [{"path": p, "rule_ids": []} for p in paths]
                    targets: list[engine_pb2.AiEscalateTarget] = []
                    for t in target_dicts:
                        path = str(t.get("path") or "")
                        if not path:
                            continue
                        raw_rules = t.get("rule_ids") or []
                        rule_ids = [str(r) for r in raw_rules] if isinstance(raw_rules, list) else []
                        targets.append(engine_pb2.AiEscalateTarget(path=path, rule_ids=rule_ids))
                    await command_queue.put(
                        engine_pb2.SessionCommand(ai_escalate=engine_pb2.AiEscalateRequest(targets=targets))
                    )
                elif kind == "proposals" and approval_queue is not None:
                    approve_timeout = get_env_float(
                        "APME_OP_APPROVE_TIMEOUT_S", _OP_APPROVE_TIMEOUT_DEFAULT_S, positive_only=True
                    )
                    # Timeout (None) mirrors the no-queue default: decline-all.
                    approved_ids = await approval_queue.next_answer(
                        approve_timeout,
                        "Approval operator wait",
                        "declining all proposals",
                        expected_generation=approval_generation,
                    )
                    if approved_ids is None:
                        approve_timed_out = True
                        approved_ids = []
                        if on_approve_timeout is not None:
                            try:
                                maybe_awaitable = on_approve_timeout()
                                if inspect.isawaitable(maybe_awaitable):
                                    await maybe_awaitable
                            except Exception:
                                logger.exception("on_approve_timeout callback failed")
                    await command_queue.put(
                        engine_pb2.SessionCommand(approve=engine_pb2.ApprovalRequest(approved_ids=approved_ids))
                    )
                elif kind == "proposals":
                    # No approval_queue — decline all proposals to avoid hanging.
                    await command_queue.put(
                        engine_pb2.SessionCommand(approve=engine_pb2.ApprovalRequest(approved_ids=[]))
                    )
                elif kind == "result":
                    result = event.result
                    if escalate_timed_out and approve_timed_out:
                        logger.warning(
                            "Operator timeouts paired (scan_id=%s): escalate allowed-all "
                            "then approve declined-all — full AI spend with zero applied "
                            "fixes (degraded)",
                            scan_id,
                        )
                    await command_queue.put(engine_pb2.SessionCommand(close=engine_pb2.CloseRequest()))
                    await command_queue.put(None)
                elif kind == "error":
                    await command_queue.put(engine_pb2.SessionCommand(close=engine_pb2.CloseRequest()))
                    await command_queue.put(None)
                    break

            return scan_id, result, clone_sha
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                command_queue.put_nowait(engine_pb2.SessionCommand(close=engine_pb2.CloseRequest()))
                command_queue.put_nowait(None)
            raise
        finally:
            with contextlib.suppress(Exception):
                command_queue.put_nowait(None)
            await channel.close(grace=None)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def run_project_scan(
    *,
    project_id: str,
    repo_url: str,
    branch: str,
    engine_address: str,
    ansible_version: str = "",
    collection_specs: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
    scan_id: str | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
    scm_token: str | None = None,
    scm_provider: str | None = None,
) -> tuple[str, engine_pb2.SessionResult | None, str]:
    """Backward-compatible alias for check mode.

    Delegates to :func:`run_project_operation` with ``remediate=False``.
    See that function for full parameter documentation.

    Args:
        project_id: UUID of the project.
        repo_url: SCM clone URL.
        branch: Branch to clone.
        engine_address: ``host:port`` for the Engine gRPC service.
        ansible_version: Target ansible-core version.
        collection_specs: Collection install specs.
        progress_callback: Optional async callable for each ``SessionEvent``.
        scan_id: Optional pre-generated scan ID.
        galaxy_servers: Global Galaxy server defs to inject (ADR-045).
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for clone auth selection.

    Returns:
        Tuple of (scan_id, SessionResult or None, clone_commit_sha).
    """
    return await run_project_operation(
        project_id=project_id,
        repo_url=repo_url,
        branch=branch,
        engine_address=engine_address,
        remediate=False,
        ansible_version=ansible_version,
        collection_specs=collection_specs,
        progress_callback=progress_callback,
        scan_id=scan_id,
        galaxy_servers=galaxy_servers,
        scm_token=scm_token,
        scm_provider=scm_provider,
    )


def _collect_format_files(root: Path, max_files: int, max_bytes: int) -> list[tuple[str, bytes]]:
    """Collect ``*.yml``/``*.yaml`` files under *root* with symlink + cap guards.

    Symlinks are skipped outright (never followed); every candidate's
    resolved path must stay within *root*. Each file's size is stat'ed
    (``st_size``) against *max_bytes* BEFORE ``read_bytes`` so a single
    huge file fails fast without loading it into RAM; aggregate file/byte
    counts are enforced as a second gate while appending.

    Blocking FS I/O — callers run this off the event loop (see
    :func:`run_project_format`).

    Args:
        root: Clone directory (resolved once for containment checks).
        max_files: Aggregate file-count cap (``APME_SCAN_MAX_FILES``).
        max_bytes: Per-file and aggregate byte cap (``APME_SCAN_MAX_BYTES``).

    Returns:
        List of ``(relative_path, content)`` tuples (``*.yml`` first, each
        group sorted — same order as the previous router inline walk).

    Raises:
        ScanCapExceeded: When the per-file or aggregate caps are exceeded.
    """
    base = root.resolve()
    candidates: list[Path] = []
    for pattern in ("*.yml", "*.yaml"):
        candidates.extend(sorted(base.rglob(pattern)))
    collected: list[tuple[str, bytes]] = []
    file_count = 0
    byte_count = 0
    for path in candidates:
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(base):
            continue
        if not resolved.is_file():
            continue
        try:
            size = resolved.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            raise ScanCapExceeded(f"Format file too large: {path.name} ({size} bytes, max {max_bytes} bytes)")
        try:
            content = resolved.read_bytes()
        except OSError:
            continue
        file_count += 1
        byte_count += len(content)
        if file_count > max_files:
            raise ScanCapExceeded(f"Format file limit exceeded: {file_count} files (max {max_files})")
        if byte_count > max_bytes:
            raise ScanCapExceeded(f"Format size limit exceeded: {byte_count} bytes (max {max_bytes} bytes)")
        collected.append((str(resolved.relative_to(base)), content))
    return collected


async def run_project_format(
    *,
    repo_url: str,
    branch: str,
    engine_address: str,
    scm_token: str | None = None,
    scm_provider: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Clone a project repo and return Engine Format diffs (read-only preview).

    Owns clone + cap-checked collection + ``Format`` gRPC + tempdir
    cleanup. The Gateway never writes: Engine ``Format`` (unary) returns
    per-file diffs only — apply via CLI ``apme format --apply`` or
    ``apme remediate``.

    Args:
        repo_url: SCM clone URL.
        branch: Branch to clone (validated by the caller / ``clone_repo``).
        engine_address: ``host:port`` for the Engine gRPC service.
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for clone auth selection.

    Returns:
        Tuple of (clone_commit_sha, diffs as ``[{path, diff}]`` with empty
        diffs omitted).

    Raises:
        ValueError: When the repo URL or branch is invalid.
        ScanCapExceeded: When format caps are exceeded (HTTP 413).
        RuntimeError: When ``git clone`` (``Clone failed: ...``) or Engine
            ``Format`` (``Engine Format failed: ...``) fails.
    """
    temp_dir = tempfile.mkdtemp(prefix="apme_project_format_")
    try:
        try:
            await clone_repo(
                repo_url,
                branch,
                temp_dir,
                scm_token=scm_token,
                scm_provider=scm_provider,
            )
        except ScanCapExceeded:
            raise
        except RuntimeError as exc:
            raise RuntimeError(f"Clone failed: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"Invalid repository or branch: {exc}") from exc
        loop = asyncio.get_running_loop()
        commit = await loop.run_in_executor(None, get_clone_head, temp_dir) or ""
        max_files = _scan_max_files()
        # Bound collection below the gRPC message ceiling (1 MiB headroom)
        # so oversized previews return 413 via ScanCapExceeded instead of
        # failing late with RESOURCE_EXHAUSTED (→ 502) at the Engine.
        max_bytes = min(_scan_max_bytes(), _GRPC_MAX_MSG - 1024 * 1024)
        collected = await loop.run_in_executor(
            None,
            partial(_collect_format_files, Path(temp_dir), max_files, max_bytes),
        )
        files = [ProtoFile(path=rel, content=content) for rel, content in collected]
        channel = grpc.aio.insecure_channel(
            engine_address,
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )
        try:
            stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]
            resp = await stub.Format(engine_pb2.FormatRequest(files=files), timeout=120)
        except Exception as exc:
            raise RuntimeError(f"Engine Format failed: {exc}") from exc
        finally:
            await channel.close(grace=None)
        return commit, [{"path": d.path, "diff": d.diff} for d in resp.diffs if d.diff]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
