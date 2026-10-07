"""Tests for unified upload path canonicalization (N4)."""

from __future__ import annotations

import pytest

from apme.v1.common_pb2 import File
from apme_engine.daemon.fs_utils import canonicalize_upload_relpath, write_chunked_fs


def test_canonicalize_backslash_equivalence() -> None:
    """Backslash and forward-slash spellings share one key."""
    assert canonicalize_upload_relpath("a\\b.yml") == "a/b.yml"
    assert canonicalize_upload_relpath("a\\b.yml") == canonicalize_upload_relpath("a/b.yml")


def test_canonicalize_dot_forms_share_one_key() -> None:
    """``./a.yml`` and ``a.yml`` normalize identically."""
    assert canonicalize_upload_relpath("./a.yml") == "a.yml"
    assert canonicalize_upload_relpath("./a.yml") == canonicalize_upload_relpath("a.yml")
    assert canonicalize_upload_relpath("a//b.yml") == "a/b.yml"
    assert canonicalize_upload_relpath("a/./b.yml") == "a/b.yml"


def test_canonicalize_rejects_escapes() -> None:
    """Absolute and parent-escaping paths are rejected."""
    for raw in ["", ".", "./", "..", "../evil.yml", "/etc/passwd", "a/../../evil.yml"]:
        with pytest.raises(ValueError, match="escapes session root or is empty"):
            canonicalize_upload_relpath(raw)


def test_canonicalize_rejects_whitespace_only() -> None:
    """Blank/whitespace-only names are rejected, not stored as keys."""
    for raw in ["   ", "\t", " \n "]:
        with pytest.raises(ValueError, match="escapes session root or is empty"):
            canonicalize_upload_relpath(raw)


def test_canonicalize_rejects_drive_absolute() -> None:
    """Windows drive-absolute spellings are rejected."""
    for raw in ["C:/a.yml", "C:a.yml", "c:\\a\\b.yml", "D:/evil.yml"]:
        with pytest.raises(ValueError, match="escapes session root or is empty"):
            canonicalize_upload_relpath(raw)


def test_write_chunked_fs_handles_backslash() -> None:
    """Backslash uploads materialize under the collapsed posix path."""
    import shutil

    files = [File(path="a\\b.yml", content=b"---\n")]
    tmp = write_chunked_fs(files, prefix="apme_test_backslash_")
    try:
        assert (tmp / "a" / "b.yml").is_file()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sanitize_path_matches_canonicalizer() -> None:
    """Gateway WebSocket sanitizer agrees with the engine canonicalizer."""
    from apme_gateway.session_client import _sanitize_path

    assert _sanitize_path("a\\b.yml") == "a/b.yml"
    assert _sanitize_path("./a.yml") == "a.yml"
    assert _sanitize_path("./a.yml") == _sanitize_path("a.yml")
    with pytest.raises(ValueError, match="traversal|Invalid file path"):
        _sanitize_path("../evil.yml")


def test_sanitize_path_empty_vs_traversal_fragments() -> None:
    """Blank names keep the 'Invalid file path' fragment; escapes keep 'traversal'."""
    from apme_gateway.session_client import _sanitize_path

    with pytest.raises(ValueError, match="Invalid file path"):
        _sanitize_path("   ")
    with pytest.raises(ValueError, match="Path traversal detected"):
        _sanitize_path("C:/evil.yml")


def test_write_chunked_fs_rejects_genuine_collision() -> None:
    """Backslash-united distinct raws fail fast instead of dropping a file (#17)."""
    files = [File(path="a\\b.yml", content=b"one"), File(path="a/b.yml", content=b"two")]
    with pytest.raises(ValueError, match="collide after canonicalization"):
        write_chunked_fs(files, prefix="apme_test_collision_")


def test_write_chunked_fs_allows_trivial_respelling() -> None:
    """``a.yml`` + ``./a.yml`` stays last-wins (same file re-sent) (#17)."""
    import shutil

    files = [File(path="a.yml", content=b"one"), File(path="./a.yml", content=b"two")]
    tmp = write_chunked_fs(files, prefix="apme_test_respelling_")
    try:
        assert (tmp / "a.yml").read_bytes() == b"two"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_is_trivial_respelling() -> None:
    """Respelling check ignores dot segments but not backslashes (#17)."""
    from apme_engine.daemon.fs_utils import is_trivial_respelling

    assert is_trivial_respelling("a.yml", "a.yml")
    assert is_trivial_respelling("a.yml", "./a.yml")
    assert is_trivial_respelling("a//b.yml", "a/b.yml")
    assert not is_trivial_respelling("a\\b.yml", "a/b.yml")
    assert not is_trivial_respelling("a.yml", "b.yml")
    assert not is_trivial_respelling("", "./a.yml")
    assert not is_trivial_respelling("a.yml", "")
