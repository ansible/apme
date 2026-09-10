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
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any
from urllib.parse import quote, unquote, urlparse, urlunparse

import grpc
import grpc.aio

from apme.v1 import engine_pb2, engine_pb2_grpc
from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_gateway.scm.redaction import redact_credentials as _redact_credentials
from apme_gateway.scm.repo_url import normalize_repo_url

logger = logging.getLogger(__name__)

_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — matches Engine

# ADR-068: server enforces adaptive deadlines; no fixed client gRPC timeout.

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

    Args:
        project_id: UUID hex of the project.

    Returns:
        First 16 hex characters of the SHA-256 hash.
    """
    return hashlib.sha256(project_id.encode()).hexdigest()[:16]


_ALLOWED_SCHEMES = ("https://",)

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

    Returns:
        Copy of ``os.environ`` with ``GIT_SSL_CAINFO`` populated when a CA bundle
        path is available via another standard environment variable.
    """
    env = os.environ.copy()
    if env.get("GIT_SSL_CAINFO"):
        return env

    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        candidate = env.get(key, "").strip()
        if candidate:
            env["GIT_SSL_CAINFO"] = candidate
            break
    return env


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

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch name to resolve.
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for auth username selection.

    Returns:
        40-char hex SHA, or ``None`` if the lookup fails.
    """
    url_userpass = _url_embedded_userpass(repo_url) if not scm_token else None
    repo_url = _strip_url_userinfo(repo_url)
    if not any(repo_url.startswith(scheme) for scheme in _ALLOWED_SCHEMES):
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
    # appears in argv; pre-existing GIT_CONFIG_* entries are preserved.
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
    ``ssh://``, or other git transports.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch to check out.
        dest: Target directory (must not already exist).
        scm_token: Optional SCM token for private repository access.
        scm_provider: Optional explicit SCM provider for auth username selection.

    Raises:
        ValueError: If *repo_url* uses a disallowed scheme.
        RuntimeError: If ``git clone`` fails or times out.
    """
    url_userpass = _url_embedded_userpass(repo_url) if not scm_token else None
    repo_url = _strip_url_userinfo(repo_url)
    if not any(repo_url.startswith(scheme) for scheme in _ALLOWED_SCHEMES):
        msg = f"Only https:// clone URLs are allowed, got: {repo_url[:60]}"
        raise ValueError(msg)

    if not branch.replace("-", "").replace("_", "").replace("/", "").replace(".", "").isalnum():
        msg = f"Invalid branch name: {branch[:60]}"
        raise ValueError(msg)

    # Pass the token via a per-origin http.extraHeader env entry so it never
    # appears in argv; pre-existing GIT_CONFIG_* entries are preserved.
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
    cmd = [
        "git",
        "clone",
        "--branch",
        branch,
        "--single-branch",
        "--depth",
        "1",
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


ProgressCallback = Callable[[engine_pb2.SessionEvent], Coroutine[Any, Any, None]]


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
    approval_queue: asyncio.Queue[list[str]] | None = None,
    begin_remediate_queue: asyncio.Queue[None] | None = None,
    escalate_ai_queue: asyncio.Queue[list[dict[str, object]]] | None = None,
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
    """
    if scan_id is None:
        scan_id = uuid.uuid4().hex
    session_id = derive_session_id(project_id)
    prefix = "apme_project_remediate_" if remediate or assess_pause else "apme_project_check_"
    temp_dir = tempfile.mkdtemp(prefix=prefix)

    try:
        await clone_repo(repo_url, branch, temp_dir, scm_token=scm_token, scm_provider=scm_provider)
        clone_sha = await asyncio.get_running_loop().run_in_executor(None, get_clone_head, temp_dir) or ""

        chunks = list(
            yield_scan_chunks(
                temp_dir,
                scan_id=scan_id,
                project_root_name="project",
                ansible_core_version=ansible_version or None,
                collection_specs=collection_specs or None,
                session_id=session_id,
                galaxy_servers=galaxy_servers,
            )
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
            chunks[0].fix_options.CopyFrom(fix_opts)  # type: ignore[union-attr]

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
            async for event in response_stream:
                if progress_callback:
                    await progress_callback(event)

                kind = event.WhichOneof("event")
                if kind == "findings":
                    if begin_remediate_queue is not None:
                        await begin_remediate_queue.get()
                    await command_queue.put(
                        engine_pb2.SessionCommand(begin_remediate=engine_pb2.BeginRemediateRequest())
                    )
                elif kind == "ai_triage":
                    target_dicts: list[dict[str, object]]
                    if escalate_ai_queue is not None:
                        target_dicts = await escalate_ai_queue.get()
                    else:
                        # No queue — escalate every candidate path (allow-all).
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
                    approved_ids = await approval_queue.get()
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
