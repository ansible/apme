"""Scan orchestration — file discovery, gRPC streaming, and DB queries.

Persistence of scan results is handled exclusively by the event subscriber
(see ``event_subscriber.py``).  ``trigger_scan()`` only calls Primary's
ScanStream and returns the scan_id; the caller polls the DB until the event
subscriber has persisted the result.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apme.v1.common_pb2 import File
from apme.v1.primary_pb2 import ScanChunk, ScanOptions
from apme_gateway.models.tables import Scan

if TYPE_CHECKING:
    from apme_gateway.services.grpc_client import PrimaryClient

logger = logging.getLogger(__name__)

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", "htmlcov", ".github"}
MAX_FILE_SIZE = 2 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".yml",
    ".yaml",
    ".json",
    ".j2",
    ".jinja2",
    ".md",
    ".py",
    ".sh",
    ".cfg",
    ".ini",
    ".toml",
    ".tf",
    ".tfvars",
}
CHUNK_MAX_BYTES = 1024 * 1024


def _discover_files(root: Path) -> list[tuple[str, bytes]]:
    """Walk *root* and return (relative_path, content) for Ansible-relevant files.

    Args:
        root: Absolute path to the project directory.

    Returns:
        List of (relative_path, file_bytes) tuples.
    """
    results: list[tuple[str, bytes]] = []
    if root.is_file():
        try:
            content = root.read_bytes()
        except OSError:
            return results
        results.append((root.name, content))
        return results

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dirpath = Path(dirpath_str)
        for name in filenames:
            fp = dirpath / name
            try:
                if fp.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            suffix = fp.suffix.lower()
            if suffix not in TEXT_EXTENSIONS:
                continue
            try:
                content = fp.read_bytes()
            except OSError:
                continue
            if b"\x00" in content[:8192]:
                continue
            rel = str(fp.relative_to(root))
            results.append((rel, content))
    return results


async def _chunk_iter(
    files: list[tuple[str, bytes]],
    scan_id: str,
    project_root: str,
    options: ScanOptions,
) -> AsyncIterator[ScanChunk]:
    """Yield ScanChunk messages sized to stay under gRPC limits.

    Args:
        files: Discovered (relative_path, content) pairs.
        scan_id: Unique scan identifier.
        project_root: Logical project root name.
        options: Scan options protobuf.

    Yields:
        ScanChunk messages for the streaming RPC.
    """
    if not files:
        yield ScanChunk(
            scan_id=scan_id,
            project_root=project_root,
            options=options,
            files=[],
            last=True,
        )
        return

    batch: list[File] = []
    batch_bytes = 0
    first = True

    for rel, content in files:
        msg_size = len(rel.encode()) + len(content)
        if batch and batch_bytes + msg_size > CHUNK_MAX_BYTES:
            kwargs: dict[str, object] = {"files": batch, "last": False}
            if first:
                kwargs.update(scan_id=scan_id, project_root=project_root, options=options)
                first = False
            yield ScanChunk(**kwargs)
            batch = []
            batch_bytes = 0
        batch.append(File(path=rel, content=content))
        batch_bytes += msg_size

    final: dict[str, object] = {"files": batch, "last": True}
    if first:
        final.update(scan_id=scan_id, project_root=project_root, options=options)
    yield ScanChunk(**final)


_POLL_INTERVAL = 0.25
_POLL_TIMEOUT = 30.0


async def trigger_scan(
    project_path: str,
    client: PrimaryClient,
    ansible_core_version: str | None = None,
    collection_specs: list[str] | None = None,
) -> str:
    """Discover files, stream to Primary, return scan_id.

    Does NOT persist results — the event subscriber handles that
    when it receives the ``ScanCompletedEvent`` from Primary.

    Args:
        project_path: Local directory or file to scan.
        client: gRPC client to Primary.
        ansible_core_version: Optional ansible-core version pin.
        collection_specs: Optional Galaxy collection specs.

    Returns:
        The scan_id that Primary assigned to this scan.

    Raises:
        FileNotFoundError: If project_path does not exist.
        grpc.RpcError: On gRPC failure (propagated to caller).
    """
    root = Path(project_path).resolve()
    if not root.exists():
        msg = f"Target does not exist: {project_path}"
        raise FileNotFoundError(msg)

    scan_id = uuid.uuid4().hex
    files = _discover_files(root)

    opts = ScanOptions()
    if ansible_core_version:
        opts.ansible_core_version = ansible_core_version
    if collection_specs:
        opts.collection_specs.extend(collection_specs)

    await client.scan_stream(
        _chunk_iter(files, scan_id, "project", opts),
    )

    return scan_id


async def wait_for_scan(
    session: AsyncSession,
    scan_id: str,
    timeout: float = _POLL_TIMEOUT,
) -> Scan | None:
    """Poll until the event subscriber has persisted *scan_id*.

    Args:
        session: Async DB session.
        scan_id: The scan_id returned by ``trigger_scan()``.
        timeout: Maximum seconds to wait.

    Returns:
        The persisted Scan or None if the timeout expires.
    """
    elapsed = 0.0
    while elapsed < timeout:
        await session.expire_all()
        scan = await session.get(Scan, scan_id)
        if scan is not None:
            return scan
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
    return None


async def list_scans(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Scan], int]:
    """Return a page of scans ordered by creation time descending.

    Args:
        session: Async DB session.
        page: 1-based page number.
        page_size: Items per page.

    Returns:
        (list_of_scans, total_count) tuple.
    """
    from sqlalchemy import func

    total_q = await session.execute(select(func.count()).select_from(Scan))
    total: int = total_q.scalar_one()

    offset = (page - 1) * page_size
    q = select(Scan).order_by(Scan.created_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(q)
    return list(result.scalars().all()), total


async def get_scan(session: AsyncSession, scan_id: str) -> Scan | None:
    """Fetch a single scan by ID with violations eager-loaded.

    Args:
        session: Async DB session.
        scan_id: UUID hex of the scan.

    Returns:
        Scan ORM object or None.
    """
    return await session.get(Scan, scan_id)


async def delete_scan(session: AsyncSession, scan_id: str) -> bool:
    """Delete a scan and its cascade children.

    Args:
        session: Async DB session.
        scan_id: UUID hex of the scan.

    Returns:
        True if deleted, False if not found.
    """
    scan = await session.get(Scan, scan_id)
    if scan is None:
        return False
    await session.delete(scan)
    await session.commit()
    return True
