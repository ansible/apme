"""Project operation driver — clone, chunk, check/remediate via gRPC (ADR-037, ADR-039).

The gateway acts as a gRPC client to Primary for project-initiated operations.
On each invocation the project repo is shallow-cloned into a temporary directory,
chunked via the engine's ``yield_scan_chunks``, and streamed to Primary via
``FixSession`` (check mode omits ``fix_options`` on chunks; remediate mode sets
them on the first chunk).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import grpc
import grpc.aio

from apme.v1 import primary_pb2, primary_pb2_grpc
from apme.v1.common_pb2 import GalaxyServerDef
from apme_engine.daemon.chunked_fs import yield_scan_chunks

logger = logging.getLogger(__name__)

_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — matches Primary


def derive_session_id(project_id: str) -> str:
    """Deterministic session ID so the engine reuses venvs across operations.

    Args:
        project_id: UUID hex of the project.

    Returns:
        First 16 hex characters of the SHA-256 hash.
    """
    return hashlib.sha256(project_id.encode()).hexdigest()[:16]


_ALLOWED_SCHEMES = ("https://",)


async def clone_repo(repo_url: str, branch: str, dest: str) -> None:
    """Shallow-clone an SCM repo into *dest*.

    Only ``https://`` URLs are permitted to prevent SSRF via ``file://``,
    ``ssh://``, or other git transports.

    Args:
        repo_url: HTTPS clone URL.
        branch: Branch to check out.
        dest: Target directory (must not already exist).

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
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120),  # noqa: S603
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed (exit {result.returncode}): {result.stderr[:500]}")


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
    progress_callback: ProgressCallback | None = None,
    approval_queue: asyncio.Queue[list[str]] | None = None,
    scan_id: str | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
) -> tuple[str, primary_pb2.SessionResult | None]:
    """Clone a project repo and run check or remediate via Primary ``FixSession``.

    Check mode (``remediate=False``) sends chunks without ``fix_options``.
    Remediate mode attaches ``FixOptions`` on the first chunk.

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
        progress_callback: Optional async callable for each ``SessionEvent``.
        approval_queue: Queue of approved proposal IDs (remediate mode, when AI proposes).
        scan_id: Optional pre-generated scan ID; one is created if omitted.
        galaxy_servers: Global Galaxy server defs to inject into scan metadata (ADR-045).

    Returns:
        Tuple of (scan_id, SessionResult or None).
    """
    if scan_id is None:
        scan_id = uuid.uuid4().hex
    session_id = derive_session_id(project_id)
    prefix = "apme_project_remediate_" if remediate else "apme_project_check_"
    temp_dir = tempfile.mkdtemp(prefix=prefix)

    try:
        await clone_repo(repo_url, branch, temp_dir)

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

        if remediate and chunks:
            fix_opts = primary_pb2.FixOptions(
                ansible_core_version=ansible_version,
                collection_specs=collection_specs or [],
                enable_ai=enable_ai,
                ai_model=ai_model,
                galaxy_servers=galaxy_servers or [],
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

            response_stream = stub.FixSession(_command_stream(), timeout=600)

            result: primary_pb2.SessionResult | None = None
            async for event in response_stream:
                if progress_callback:
                    await progress_callback(event)

                kind = event.WhichOneof("event")
                if kind == "proposals" and approval_queue is not None:
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

            return scan_id, result
        finally:
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
) -> tuple[str, primary_pb2.SessionResult | None]:
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

    Returns:
        Tuple of (scan_id, SessionResult or None).
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
    )


async def run_project_fix(
    *,
    project_id: str,
    repo_url: str,
    branch: str,
    primary_address: str,
    ansible_version: str = "",
    collection_specs: list[str] | None = None,
    enable_ai: bool = True,
    ai_model: str = "",
    progress_callback: ProgressCallback | None = None,
    approval_queue: asyncio.Queue[list[str]] | None = None,
    scan_id: str | None = None,
    galaxy_servers: list[GalaxyServerDef] | None = None,
) -> tuple[str, primary_pb2.SessionResult | None]:
    """Backward-compatible alias for remediate mode.

    Delegates to :func:`run_project_operation` with ``remediate=True``.
    See that function for full parameter documentation.

    Args:
        project_id: UUID of the project.
        repo_url: SCM clone URL.
        branch: Branch to clone.
        primary_address: ``host:port`` for the Primary gRPC service.
        ansible_version: Target ansible-core version.
        collection_specs: Collection install specs.
        enable_ai: Enable AI remediation tier.
        ai_model: AI model identifier.
        progress_callback: Optional async callable for each ``SessionEvent``.
        approval_queue: Queue of approved proposal IDs.
        scan_id: Optional pre-generated scan ID.
        galaxy_servers: Global Galaxy server defs to inject (ADR-045).

    Returns:
        Tuple of (scan_id, SessionResult or None).
    """
    return await run_project_operation(
        project_id=project_id,
        repo_url=repo_url,
        branch=branch,
        primary_address=primary_address,
        remediate=True,
        ansible_version=ansible_version,
        collection_specs=collection_specs,
        enable_ai=enable_ai,
        ai_model=ai_model,
        progress_callback=progress_callback,
        approval_queue=approval_queue,
        scan_id=scan_id,
        galaxy_servers=galaxy_servers,
    )
