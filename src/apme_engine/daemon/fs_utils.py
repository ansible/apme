"""Shared filesystem helpers for APME daemon gRPC services."""

from __future__ import annotations

import posixpath
import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from apme.v1.common_pb2 import File

_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:")


def canonicalize_upload_relpath(path: str) -> str:
    r"""Canonicalize an upload-relative path to a session-relative posix key.

    Backslashes become forward slashes (so ``a\\b.yml`` equals ``a/b.yml``),
    ``.``/``//`` segments collapse via ``posixpath.normpath``, and absolute,
    drive-absolute (``C:...``), blank/whitespace-only, or root-escaping paths
    are rejected. ``./a.yml`` and ``a.yml`` share one key so last-wins dedup
    works identically on every ingress path.

    Args:
        path: Raw ``File.path`` from an upload chunk or WebSocket message.

    Returns:
        Normalized relative posix path.

    Raises:
        ValueError: If the path is empty/whitespace-only, absolute,
            drive-absolute, or escapes the session root.
    """
    if not isinstance(path, str):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    posix = path.replace("\\", "/")
    # Reject absolute paths (leading slash after backslash normalization)
    # before stripping: ``/etc/passwd`` and ``\\a\\b.yml``-style rooted
    # inputs fail closed instead of silently collapsing to a sibling key.
    if posix.startswith("/"):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    # Also reject drive-absolute / rooted Windows spellings that survived
    # the backslash pass (e.g. ``C:/a.yml`` is absolute on Windows).
    stripped = posix.strip()
    if not stripped:
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    if stripped.startswith("/"):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    if _DRIVE_ABSOLUTE_RE.match(stripped):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    normalized = posixpath.normpath(posix.lstrip("/"))
    if not normalized or normalized == "." or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"Upload path escapes session root or is empty: {path!r}")
    return normalized


def is_trivial_respelling(first_raw: str, second_raw: str) -> bool:
    r"""Return whether two raw paths are trivial spelling variants (#17).

    Collapses leading ``./`` segments and duplicate slashes WITHOUT
    backslash conversion: ``a.yml`` vs ``./a.yml`` (or ``a//b.yml`` vs
    ``a/b.yml``) is the same file re-sent and stays last-wins, while
    ``a\\\\b.yml`` vs ``a/b.yml`` — united only by backslash
    normalization — is a genuine collision that must fail fast instead
    of silently dropping a file.

    Args:
        first_raw: First raw upload path.
        second_raw: Second raw upload path.

    Returns:
        True when both spell the same path modulo ``.``/``//`` segments.
    """
    if not isinstance(first_raw, str) or not isinstance(second_raw, str):
        return False

    def _collapse(raw: str) -> str:
        parts = [seg for seg in raw.replace("\\", "\x00").split("/") if seg not in ("", ".")]
        return "/".join(parts).replace("\x00", "\\")

    return _collapse(first_raw) == _collapse(second_raw)


def canonicalize_upload_entries[ContentT](entries: Iterable[tuple[str, ContentT]]) -> list[tuple[str, ContentT]]:
    r"""Canonicalize ``(path, content)`` upload entries for every ingress.

    One implementation is used by the gRPC ``Scan``/``Format``/``FixSession``
    ingresses and by the Gateway WebSocket upload so a payload is
    accepted-or-rejected identically everywhere:

    * keys are produced by :func:`canonicalize_upload_relpath`, so ``./a.yml``,
      ``a.yml`` and ``a//b.yml`` share one key;
    * a repeated key is last-wins (idempotent re-uploads keep working);
    * two *distinct* raw spellings that unify only because backslashes were
      converted (``a\\b.yml`` versus ``a/b.yml``) are rejected, because on
      POSIX those are genuinely different files and silently keeping one
      would drop a file from the scan.

    Args:
        entries: Raw ``(path, content)`` pairs in arrival order.

    Returns:
        Deduplicated ``(canonical_path, content)`` pairs in first-seen order
        with the last content for each key.

    Raises:
        ValueError: If a path escapes the upload root, is empty, or collides
            with a different raw path after canonicalization.
    """
    order: list[str] = []
    kept: dict[str, tuple[str, ContentT]] = {}
    for raw_path, content in entries:
        key = canonicalize_upload_relpath(raw_path)
        previous = kept.get(key)
        if previous is not None and previous[0] != raw_path and not is_trivial_respelling(previous[0], raw_path):
            msg = (
                f"Upload paths collide after canonicalization: {previous[0]!r} and {raw_path!r} "
                f"both map to {key!r}; rename one file"
            )
            raise ValueError(msg)
        if previous is None:
            order.append(key)
        kept[key] = (raw_path, content)
    return [(key, kept[key][1]) for key in order]


def write_chunked_fs(files: list[File], *, prefix: str = "apme_") -> Path:
    """Write request files into a temp directory; return path to that directory.

    File paths are canonicalized with :func:`canonicalize_upload_relpath`
    (backslash-tolerant, ``./``/``//``-collapsing) and absolute/``..``
    escapes are rejected to prevent writes outside the temp directory.
    On any failure the temp directory is removed before re-raising.

    Args:
        files: List of File protos with path and content.
        prefix: Prefix for ``tempfile.mkdtemp``.

    Returns:
        Path to the created temp directory.

    Raises:
        ValueError: If a file path is absolute or escapes the temp root,
            or if two distinct raw paths collapse to one canonical key
            (silent file-drop); exact duplicate paths stay last-wins so
            idempotent re-uploads keep working.
    """
    tmp = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    success = False
    try:
        for rel, content in canonicalize_upload_entries((f.path, f.content) for f in files):
            path = (tmp / rel).resolve()
            if not path.is_relative_to(tmp):
                raise ValueError(f"Path escapes temp root: {rel!r}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        success = True
        return tmp
    finally:
        if not success:
            shutil.rmtree(tmp, ignore_errors=True)
