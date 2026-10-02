"""Session upload ingress: path normalization, hashing, and append with caps.

Extracted from :mod:`apme_engine.daemon.engine_server` so upload admission
has a focused owner and test target. Behavior-preserving move: per-session
aggregate caps (``APME_SESSION_MAX_UPLOAD_BYTES`` /
``APME_SESSION_MAX_UPLOAD_FILES``) are enforced before mutating any state.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
from collections.abc import Mapping

from apme.v1.engine_pb2 import ScanChunk
from apme_engine.config_env import get_env_int
from apme_engine.daemon.session import SessionState

logger = logging.getLogger("apme.engine")

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

    Upload keys must match materialized file paths: posix-style separators,
    no leading slash, ``.``/``..`` collapsed. Paths escaping the session
    root are rejected so ``original_files`` keys always match the files
    written by ``write_chunked_fs``.

    Args:
        path: Raw ``File.path`` from an upload chunk.

    Returns:
        Normalized relative posix path.

    Raises:
        ValueError: If the path is empty or escapes the session root.
    """
    posix = path.replace("\\", "/").lstrip("/")
    normalized = posixpath.normpath(posix)
    if not normalized or normalized == "." or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
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
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(files[path])
        digest.update(b"\x00")
    return digest.hexdigest()


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
        ValueError: If a path escapes the session root.
    """  # noqa: DOC502 -- the raise lives in _normalize_upload_path
    staged: dict[str, bytes] = {}
    for f in chunk.files:
        staged[_normalize_upload_path(f.path)] = f.content  # type: ignore[attr-defined]
    return _hash_upload_files(staged)


def _session_upload_append(session: SessionState, chunk: ScanChunk) -> None:
    """Append upload chunk files using normalized session-relative keys.

    Paths are normalized with :func:`_normalize_upload_path` — the same
    helper applied on the materialization path — so ``original_files``
    keys always match materialized files. Per-session aggregate caps
    (``APME_SESSION_MAX_UPLOAD_BYTES`` / ``APME_SESSION_MAX_UPLOAD_FILES``)
    are enforced before mutating any state.

    Args:
        session: Active session whose ``original_files``/``working_files``
            receive the uploaded content.
        chunk: Upload chunk carrying ``File`` entries.

    Raises:
        ValueError: If the session already processed its terminal chunk
            (post-seal uploads are rejected — start a new session), if a
            path escapes the session root, or if an aggregate upload cap
            would be exceeded.
    """
    if session.upload_sealed:
        raise ValueError("Session upload already processed: start a new session for additional files")
    staged = [(_normalize_upload_path(f.path), f.content) for f in chunk.files]  # type: ignore[attr-defined]
    # One chunk may repeat a path (e.g. `a.yml` + `./a.yml` normalize
    # alike): last copy wins, accounted once so byte math stays exact.
    deduped: dict[str, bytes] = {}
    for path, content in staged:
        deduped[path] = content
    staged = list(deduped.items())
    new_keys = {path for path, _ in staged} - set(session.original_files)
    file_total = len(session.original_files) + len(new_keys)
    if file_total > _SESSION_MAX_UPLOAD_FILES:
        raise ValueError(f"Session upload file limit exceeded: {file_total} files (max {_SESSION_MAX_UPLOAD_FILES})")
    current_bytes = sum(len(content) for content in session.original_files.values())
    replaced_bytes = sum(len(session.original_files[path]) for path, _ in staged if path in session.original_files)
    incoming_bytes = sum(len(content) for _, content in staged)
    byte_total = current_bytes - replaced_bytes + incoming_bytes
    if byte_total > _SESSION_MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Session upload size limit exceeded: {byte_total} bytes (max {_SESSION_MAX_UPLOAD_BYTES} bytes)"
        )
    for path, content in staged:
        session.original_files[path] = content
        session.working_files[path] = content
