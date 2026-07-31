"""Shared filesystem helpers for APME daemon gRPC services."""

from __future__ import annotations

import tempfile
from pathlib import Path

from apme.v1.common_pb2 import File


def write_chunked_fs(files: list[File], *, prefix: str = "apme_") -> Path:
    """Write request files into a temp directory; return path to that directory.

    File paths are sanitised: absolute paths and ``..`` segments are rejected
    to prevent writes outside the temp directory.

    Args:
        files: List of File protos with path and content.
        prefix: Prefix for ``tempfile.mkdtemp``.

    Returns:
        Path to the created temp directory.

    Raises:
        ValueError: If a file path is absolute or escapes the temp root.
    """
    tmp = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    for f in files:
        rel = Path(f.path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe file path rejected: {f.path!r}")
        path = (tmp / rel).resolve()
        if not path.is_relative_to(tmp):
            raise ValueError(f"Path escapes temp root: {f.path!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.content)
    return tmp
