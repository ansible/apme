"""Project operation driver — clone, chunk, check/remediate via gRPC (ADR-037, ADR-039).

The gateway acts as a gRPC client to Primary for project-initiated operations.
On each invocation the project repo is shallow-cloned into a temporary directory,
chunked via the engine's ``yield_scan_chunks``, and streamed to Primary via
``FixSession`` (check mode omits ``fix_options`` on chunks; remediate mode sets
them on the first chunk).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import grpc
import grpc.aio

from apme.v1 import primary_pb2, primary_pb2_grpc
from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.daemon.chunked_fs import yield_scan_chunks

logger = logging.getLogger(__name__)

_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — matches Primary

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

_CRED_REDACT_RE = re.compile(r"(https?://)[^@]+@")


def _redact_credentials(text: str) -> str:
    """Redact embedded credentials from URLs in text.

    Replaces ``https://user:token@host`` with ``https://[REDACTED]@host``
    to prevent token exposure in logs or error messages.

    Args:
        text: Text potentially containing URLs with credentials.

    Returns:
        Text with credentials redacted.
    """
    return _CRED_REDACT_RE.sub(r"\1[REDACTED]@", text)


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


def _inject_token_in_url(repo_url: str, token: str) -> str:
    """Inject an authentication token into an HTTPS git URL.

    Supports multiple SCM providers with their respective auth schemes:
    - GitHub: ``x-access-token:TOKEN``
    - GitLab: ``oauth2:TOKEN``
    - Bitbucket: ``x-token-auth:TOKEN``
    - Others: ``git:TOKEN`` (generic fallback)

    Args:
        repo_url: Original HTTPS clone URL.
        token: SCM token (e.g., PAT, OAuth token).

    Returns:
        URL with embedded credentials.
    """
    parsed = urlparse(repo_url)
    hostname = parsed.hostname or ""

    if "github" in hostname:
        username = "x-access-token"
    elif "gitlab" in hostname:
        username = "oauth2"
    elif "bitbucket" in hostname:
        username = "x-token-auth"
    else:
        username = "git"

    # Percent-encode token to handle special characters (@, :, /, etc.)
    encoded_token = quote(token, safe="")
    netloc_with_auth = f"{username}:{encoded_token}@{hostname}"
    if parsed.port:
        netloc_with_auth += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc_with_auth))


async def fetch_remote_head(repo_url: str, branch: str, scm_token: str | None = None) -> str | None:
    """Query the remote for the HEAD commit SHA of *branch* without cloning.

    Uses ``git ls-remote`` which only contacts the server for ref advertisement.
    Results are cached for 60 seconds per (repo_url, branch) to avoid repeated
    outbound calls on frequent UI refreshes.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch name to resolve.
        scm_token: Optional SCM token for private repository access.

    Returns:
        40-char hex SHA, or ``None`` if the lookup fails.
    """
    if not any(repo_url.startswith(scheme) for scheme in _ALLOWED_SCHEMES):
        return None

    # Include token presence in cache key to avoid mixing authenticated/unauthenticated results
    token_marker = ":auth" if scm_token else ""
    cache_key = f"{repo_url}:{branch}{token_marker}"
    now = time.monotonic()
    cached = _REMOTE_HEAD_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _REMOTE_HEAD_TTL:
        return cached[1]

    # Inject token for private repo access
    effective_url = _inject_token_in_url(repo_url, scm_token) if scm_token else repo_url
    cmd = ["git", "ls-remote", "--exit-code", effective_url, f"refs/heads/{branch}"]
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
                env=_git_subprocess_env(),
            ),
        )
        if result.returncode == 0 and result.stdout.strip():
            sha = result.stdout.strip().split()[0]
    except Exception:  # noqa: BLE001
        logger.debug("ls-remote failed for %s branch %s", repo_url, branch, exc_info=True)

    if len(_REMOTE_HEAD_CACHE) >= _REMOTE_HEAD_CACHE_MAX:
        expired = [k for k, (ts, _) in _REMOTE_HEAD_CACHE.items() if (now - ts) >= _REMOTE_HEAD_TTL]
        for k in expired:
            del _REMOTE_HEAD_CACHE[k]
        if len(_REMOTE_HEAD_CACHE) >= _REMOTE_HEAD_CACHE_MAX:
            _REMOTE_HEAD_CACHE.clear()

    _REMOTE_HEAD_CACHE[cache_key] = (now, sha)
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


async def clone_repo(repo_url: str, branch: str, dest: str, scm_token: str | None = None) -> None:
    """Shallow-clone an SCM repo into *dest*.

    Only ``https://`` URLs are permitted to prevent SSRF via ``file://``,
    ``ssh://``, or other git transports.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch to check out.
        dest: Target directory (must not already exist).
        scm_token: Optional SCM token for private repository access.

    Raises:
        ValueError: If *repo_url* uses a disallowed scheme.
        RuntimeError: If ``git clone`` fails.
    """
    if not any(repo_url.startswith(scheme) for scheme in _ALLOWED_SCHEMES):
        msg = f"Only https:// clone URLs are allowed, got: {repo_url[:60]}"
        raise ValueError(msg)

    if not branch.replace("-", "").replace("_", "").replace("/", "").replace(".", "").isalnum():
        msg = f"Invalid branch name: {branch[:60]}"
        raise ValueError(msg)

    # Inject token for private repo access
    effective_url = _inject_token_in_url(repo_url, scm_token) if scm_token else repo_url

    cmd = [
        "git",
        "clone",
        "--branch",
        branch,
        "--single-branch",
        "--depth",
        "1",
        effective_url,
        dest,
    ]
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=_git_subprocess_env(),
        ),
    )
    if result.returncode != 0:
        safe_stderr = _redact_credentials(result.stderr[:500])
        raise RuntimeError(f"git clone failed (exit {result.returncode}): {safe_stderr}")


ProgressCallback = Callable[[primary_pb2.SessionEvent], Coroutine[Any, Any, None]]


async def run_project_operation(
    *,
    project_id: str,
    repo_url: str,
    branch: str,
    primary_address: str,
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
) -> tuple[str, primary_pb2.SessionResult | None, str]:
    """Clone a project repo and run check or remediate via Primary ``FixSession``.

    Check mode (``remediate=False``) sends chunks without ``fix_options``
    unless ``assess_pause`` is set (ADR-064 — attaches FixOptions so the
    engine can pause with FindingsReady).

    Args:
        project_id: UUID of the project (used to derive session_id).
        repo_url: SCM clone URL.
        branch: Branch to clone.
        primary_address: ``host:port`` for the Primary gRPC service.
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
        await clone_repo(repo_url, branch, temp_dir, scm_token=scm_token)
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
            fix_opts = primary_pb2.FixOptions(
                ansible_core_version=ansible_version,
                collection_specs=collection_specs or [],
                enable_ai=enable_ai,
                ai_model=ai_model,
                galaxy_servers=galaxy_servers or [],
                interactive=interactive,
                assess_pause=assess_pause,
            )
            chunks[0].fix_options.CopyFrom(fix_opts)  # type: ignore[union-attr]

        command_queue: asyncio.Queue[primary_pb2.SessionCommand | None] = asyncio.Queue()

        for chunk in chunks:
            await command_queue.put(primary_pb2.SessionCommand(upload=chunk))

        async def _command_stream() -> AsyncIterator[primary_pb2.SessionCommand]:
            while True:
                cmd = await command_queue.get()
                if cmd is None:
                    return
                yield cmd

        channel = grpc.aio.insecure_channel(
            primary_address,
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )
        try:
            stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

            response_stream = stub.FixSession(_command_stream())

            result: primary_pb2.SessionResult | None = None
            async for event in response_stream:
                if progress_callback:
                    await progress_callback(event)

                kind = event.WhichOneof("event")
                if kind == "findings":
                    if begin_remediate_queue is not None:
                        await begin_remediate_queue.get()
                    await command_queue.put(
                        primary_pb2.SessionCommand(begin_remediate=primary_pb2.BeginRemediateRequest())
                    )
                elif kind == "ai_triage":
                    target_dicts: list[dict[str, object]]
                    if escalate_ai_queue is not None:
                        target_dicts = await escalate_ai_queue.get()
                    else:
                        # No queue — escalate every candidate path (allow-all).
                        paths = sorted({c.path for c in event.ai_triage.candidates if c.path})
                        target_dicts = [{"path": p, "rule_ids": []} for p in paths]
                    targets: list[primary_pb2.AiEscalateTarget] = []
                    for t in target_dicts:
                        path = str(t.get("path") or "")
                        if not path:
                            continue
                        raw_rules = t.get("rule_ids") or []
                        rule_ids = [str(r) for r in raw_rules] if isinstance(raw_rules, list) else []
                        targets.append(primary_pb2.AiEscalateTarget(path=path, rule_ids=rule_ids))
                    await command_queue.put(
                        primary_pb2.SessionCommand(ai_escalate=primary_pb2.AiEscalateRequest(targets=targets))
                    )
                elif kind == "proposals" and approval_queue is not None:
                    approved_ids = await approval_queue.get()
                    await command_queue.put(
                        primary_pb2.SessionCommand(approve=primary_pb2.ApprovalRequest(approved_ids=approved_ids))
                    )
                elif kind == "proposals":
                    # No approval_queue — decline all proposals to avoid hanging.
                    await command_queue.put(
                        primary_pb2.SessionCommand(approve=primary_pb2.ApprovalRequest(approved_ids=[]))
                    )
                elif kind == "result":
                    result = event.result
                    await command_queue.put(primary_pb2.SessionCommand(close=primary_pb2.CloseRequest()))
                    await command_queue.put(None)
                elif kind == "error":
                    await command_queue.put(primary_pb2.SessionCommand(close=primary_pb2.CloseRequest()))
                    await command_queue.put(None)
                    break

            return scan_id, result, clone_sha
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                command_queue.put_nowait(primary_pb2.SessionCommand(close=primary_pb2.CloseRequest()))
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
    primary_address: str,
    ansible_version: str = "",
    collection_specs: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
    scan_id: str | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
    scm_token: str | None = None,
) -> tuple[str, primary_pb2.SessionResult | None, str]:
    """Backward-compatible alias for check mode.

    Delegates to :func:`run_project_operation` with ``remediate=False``.
    See that function for full parameter documentation.

    Args:
        project_id: UUID of the project.
        repo_url: SCM clone URL.
        branch: Branch to clone.
        primary_address: ``host:port`` for the Primary gRPC service.
        ansible_version: Target ansible-core version.
        collection_specs: Collection install specs.
        progress_callback: Optional async callable for each ``SessionEvent``.
        scan_id: Optional pre-generated scan ID.
        galaxy_servers: Global Galaxy server defs to inject (ADR-045).
        scm_token: Optional SCM token for private repository access.

    Returns:
        Tuple of (scan_id, SessionResult or None, clone_commit_sha).
    """
    return await run_project_operation(
        project_id=project_id,
        repo_url=repo_url,
        branch=branch,
        primary_address=primary_address,
        remediate=False,
        ansible_version=ansible_version,
        collection_specs=collection_specs,
        progress_callback=progress_callback,
        scan_id=scan_id,
        galaxy_servers=galaxy_servers,
        scm_token=scm_token,
    )
