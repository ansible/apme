"""Tests for shared daemon filesystem helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme.v1.common_pb2 import File
from apme_engine.daemon.fs_utils import write_chunked_fs


def test_write_chunked_fs_writes_relative_paths() -> None:
    """Relative paths are written under the temp root."""
    files = [File(path="playbooks/site.yml", content=b"---\n- hosts: all\n")]
    tmp = write_chunked_fs(files, prefix="apme_test_")
    try:
        written = tmp / "playbooks" / "site.yml"
        assert written.is_file()
        assert written.read_bytes() == b"---\n- hosts: all\n"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def test_write_chunked_fs_rejects_absolute_path() -> None:
    """Absolute paths are rejected."""
    files = [File(path="/etc/passwd", content=b"x")]
    with pytest.raises(ValueError, match=r"Unsafe file path rejected|Path escapes temp root"):
        write_chunked_fs(files, prefix="apme_test_")


def test_write_chunked_fs_rejects_parent_segment() -> None:
    """Parent-directory segments are rejected."""
    files = [File(path="../escape.yml", content=b"x")]
    with pytest.raises(ValueError, match=r"Unsafe file path rejected|Path escapes temp root"):
        write_chunked_fs(files, prefix="apme_test_")


def test_write_chunked_fs_rejects_nested_escape() -> None:
    """Nested traversal paths are rejected."""
    files = [File(path="foo/../../outside.yml", content=b"x")]
    with pytest.raises(ValueError, match=r"Unsafe file path rejected|Path escapes temp root"):
        write_chunked_fs(files, prefix="apme_test_")


def test_write_chunked_fs_cleans_up_on_reject() -> None:
    """Rejected paths leave no orphan temp directory under the system temp root."""
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("apme_test_cleanup_*"))
    files = [File(path="/etc/passwd", content=b"x")]
    with pytest.raises(ValueError):
        write_chunked_fs(files, prefix="apme_test_cleanup_")
    after = set(Path(tempfile.gettempdir()).glob("apme_test_cleanup_*"))
    assert after == before
