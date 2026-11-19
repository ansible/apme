"""Session upload ingress: path normalization, hashing, and append with caps.

Extracted from :mod:`apme_engine.daemon.engine_server` so upload admission
has a focused owner and test target. Behavior-preserving move: per-session
aggregate caps (``APME_SESSION_MAX_UPLOAD_BYTES`` /
``APME_SESSION_MAX_UPLOAD_FILES``) are enforced before mutating any state.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from typing import cast

from apme.v1.common_pb2 import File
from apme.v1.engine_pb2 import ScanChunk
from apme_engine.config_env import get_env_int
from apme_engine.daemon.fs_utils import (
    canonicalize_upload_entries,
    canonicalize_upload_relpath,
    is_trivial_respelling,
)
from apme_engine.daemon.session import SessionState

logger = logging.getLogger("apme.engine")
_DEPENDENCY_FILES_FIELD = "dependency_files"


def dependency_upload_files(chunk: ScanChunk) -> list[File]:
    """Access the additive field absent from the legacy partial protobuf hints.

    Args:
        chunk: Upload message.

    Returns:
        Repeated dependency files.
    """
    return cast(list[File], getattr(chunk, _DEPENDENCY_FILES_FIELD))


# Per-session aggregate upload caps enforced in _session_upload_append and
# _accumulate_chunks (PE-35).
# Parsed safely: env typos fall back to defaults with a warning instead of
# crashing the daemon at import or inverting a guard.
_SESSION_MAX_UPLOAD_BYTES = get_env_int("APME_SESSION_MAX_UPLOAD_BYTES", 256 * 1024 * 1024)
_SESSION_MAX_UPLOAD_FILES = get_env_int("APME_SESSION_MAX_UPLOAD_FILES", 2000)
if _SESSION_MAX_UPLOAD_BYTES <= 0 or _SESSION_MAX_UPLOAD_FILES <= 0:
    logger.warning("Non-positive upload caps are meaningless — using defaults (bytes=256MiB, files=2000)")
    _SESSION_MAX_UPLOAD_BYTES = 256 * 1024 * 1024
    _SESSION_MAX_UPLOAD_FILES = 2000


def _normalize_upload_path(path: str) -> str:
    """Normalize an upload path to a session-relative posix key.

    Single source of truth: delegates to
    :func:`apme_engine.daemon.fs_utils.canonicalize_upload_relpath`, the same
    helper the Gateway WebSocket upload and ``write_chunked_fs`` use, so a
    path is accepted or rejected identically on every ingress. Backslashes
    become slashes, ``.``/``//`` collapse, and absolute, drive-absolute
    (``C:...``), blank, or root-escaping paths are rejected so
    ``original_files`` keys always match the files written by
    ``write_chunked_fs``.

    Args:
        path: Raw ``File.path`` from an upload chunk.

    Returns:
        Normalized relative posix path.

    Raises:
        ValueError: If the path is empty, absolute, or escapes the session root.
    """  # noqa: DOC502 -- the raise lives in canonicalize_upload_relpath
    return canonicalize_upload_relpath(path)


def validate_dependency_path(path: str) -> str:
    """Validate dependency layout and identifiers before filesystem access.

    Args:
        path: Uploaded dependency path.

    Returns:
        Normalized dependency path.

    Raises:
        ValueError: Invalid collection, role or install layout.
    """
    normalized = _normalize_upload_path(path)
    parts = normalized.split("/")
    collection = (
        parts[0] == "ansible_collections"
        and len(parts) >= 4
        and all(re.fullmatch(r"[a-z][a-z0-9_]*", part) for part in parts[1:3])
    )
    role = parts[0] == "roles" and len(parts) >= 3 and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", parts[1])
    if not (collection or role):
        raise ValueError("Invalid dependency install path")
    return normalized


def _hash_upload_files(files: Mapping[str, bytes]) -> str:
    """SHA-256 over sorted ``(path, content)`` pairs for payload comparison.

    Args:
        files: Normalized path-to-content mapping (e.g. ``original_files``).

    Returns:
        Hex digest identifying the exact payload bytes.
    """
    digest = hashlib.sha256()
    for path in sorted(files):
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(files[path]).to_bytes(8, "big"))
        digest.update(files[path])
    return digest.hexdigest()


def plan_upload_raw_path_provenance(provenance: Mapping[str, str], raw_paths: list[str]) -> dict[str, str]:
    r"""Reject cross-chunk raw-path collisions and return the paths to record.

    Each chunk is canonicalized on its own, so ``a\b.yml`` in one chunk and
    ``a/b.yml`` in the next both pass the per-chunk check — yet on POSIX they
    are different files, and ``original_files`` keeps only the canonical key,
    so the later chunk would silently replace the earlier file (the Gateway
    rejects that same sequence, so the engine must agree). Raw provenance is
    retained on the session for the whole upload so the check spans chunks.
    Exact repeats and trivial respellings (``./a.yml`` vs ``a.yml``,
    ``a//b.yml`` vs ``a/b.yml``) stay last-wins, matching the intra-chunk
    rule in :func:`canonicalize_upload_entries`. Checks run for every path
    before anything is returned, and the result is *not* applied to
    *provenance*: an ingress must commit it only once its own validation
    (dependency paths, byte/file caps) passes, otherwise a rejected chunk
    poisons the retained session and a valid retry collides with a file the
    engine never accepted.

    Args:
        provenance: Canonical-key to first-raw-spelling map for this upload
            (``SessionState.upload_raw_paths``, or a local map for one-shot
            stream ingresses). Read only.
        raw_paths: Raw ``File.path`` values from this chunk, in arrival order.

    Returns:
        Canonical-key to first-raw-spelling entries to merge into
        *provenance* once the chunk is accepted (empty for known keys).

    Raises:
        ValueError: If a raw path maps to a canonical key already claimed by a
            genuinely different raw spelling, or escapes the session root.
    """
    planned: dict[str, str] = {}
    for raw_path in raw_paths:
        key = canonicalize_upload_relpath(raw_path)
        previous = planned.get(key, provenance.get(key))
        if previous is not None and previous != raw_path and not is_trivial_respelling(previous, raw_path):
            msg = (
                f"Upload paths collide after canonicalization: {previous!r} and {raw_path!r} "
                f"both map to {key!r} across upload chunks; rename one file"
            )
            raise ValueError(msg)
        planned.setdefault(key, raw_path)
    return planned


def _hash_upload_chunk(chunk: ScanChunk) -> str:
    """Hash a chunk's payload with the same normalize+dedup as append.

    The digest covers only this chunk's files — not the accumulated
    session payload. Compare retried terminal chunks against the
    terminal-chunk hash captured at seal time
    (``SessionState.upload_sealed_chunk_hash``), never against the
    full-payload ``upload_sealed_hash``.

    Args:
        chunk: Upload chunk carrying ``File`` entries.

    Returns:
        Hex digest of the chunk payload.

    Raises:
        ValueError: If a path escapes the session root or two distinct raw
            paths collapse onto one canonical key.
    """  # noqa: DOC502 -- the raise lives in canonicalize_upload_entries
    entries = canonicalize_upload_entries([(f.path, f.content) for f in chunk.files])  # type: ignore[attr-defined]
    staged: dict[str, bytes] = dict(entries)
    for f in dependency_upload_files(chunk):
        staged[".git-dependencies/" + _normalize_upload_path(f.path)] = f.content
    return _hash_upload_files(staged)


def _session_upload_append(session: SessionState, chunk: ScanChunk) -> None:
    """Append upload chunk files using normalized session-relative keys.

    Paths are canonicalized with
    :func:`apme_engine.daemon.fs_utils.canonicalize_upload_entries` — the same
    helper applied on the materialization path and on the Gateway upload — so
    ``original_files`` keys always match materialized files and a genuine
    post-canonicalization collision is rejected instead of silently dropping
    a file. Per-session aggregate caps
    (``APME_SESSION_MAX_UPLOAD_BYTES`` / ``APME_SESSION_MAX_UPLOAD_FILES``)
    are enforced before mutating any state.

    Args:
        session: Active session whose ``original_files``/``working_files``
            receive the uploaded content.
        chunk: Upload chunk carrying ``File`` entries.

    Raises:
        ValueError: If the session already processed its terminal chunk
            (post-seal uploads are rejected — start a new session), if a
            path escapes the session root, if two distinct raw paths collapse
            onto one canonical key (within this chunk or across chunks of the
            same upload), or if an aggregate upload cap would be exceeded.
    """
    if session.upload_sealed:
        raise ValueError("Session upload already processed: start a new session for additional files")
    if any(_normalize_upload_path(f.path).startswith(".git-dependencies/") for f in chunk.files):  # type: ignore[attr-defined]
        raise ValueError("Reserved dependency upload path")
    planned_provenance = plan_upload_raw_path_provenance(
        session.upload_raw_paths,
        [f.path for f in chunk.files],  # type: ignore[attr-defined]
    )
    dependency_files = dependency_upload_files(chunk)
    if dependency_files:
        planned_provenance.update(
            plan_upload_raw_path_provenance(
                session.upload_raw_paths, [".git-dependencies/" + f.path for f in dependency_files]
            )
        )
    staged = canonicalize_upload_entries([(f.path, f.content) for f in chunk.files])  # type: ignore[attr-defined]
    for f in dependency_upload_files(chunk):
        path = validate_dependency_path(f.path)
        staged.append((".git-dependencies/" + path, f.content))
    existing = dict(session.original_files)
    existing.update({".git-dependencies/" + p: c for p, c in session.dependency_files.items()})
    # One chunk may repeat a path (e.g. `a.yml` + `./a.yml` normalize
    # alike): last copy wins, accounted once so byte math stays exact.
    deduped: dict[str, bytes] = {}
    for path, content in staged:
        deduped[path] = content
    staged = list(deduped.items())
    new_keys = {path for path, _ in staged} - set(existing)
    file_total = len(existing) + len(new_keys)
    if file_total > _SESSION_MAX_UPLOAD_FILES:
        raise ValueError(f"Session upload file limit exceeded: {file_total} files (max {_SESSION_MAX_UPLOAD_FILES})")
    current_bytes = sum(len(content) for content in existing.values())
    replaced_bytes = sum(len(existing[path]) for path, _ in staged if path in existing)
    incoming_bytes = sum(len(content) for _, content in staged)
    byte_total = current_bytes - replaced_bytes + incoming_bytes
    if byte_total > _SESSION_MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Session upload size limit exceeded: {byte_total} bytes (max {_SESSION_MAX_UPLOAD_BYTES} bytes)"
        )
    for path, content in staged:
        if path.startswith(".git-dependencies/"):
            session.dependency_files[path.removeprefix(".git-dependencies/")] = content
            continue
        session.original_files[path] = content
        session.working_files[path] = content
    # Provenance is committed only now: the chunk passed dependency-path
    # validation and both aggregate caps, so an over-limit chunk can no
    # longer poison a retained unsealed session against a valid retry.
    for key, raw_path in planned_provenance.items():
        session.upload_raw_paths.setdefault(key, raw_path)
