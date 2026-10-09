"""Tests for Galaxy Proxy wheel cache path validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from galaxy_proxy.proxy.cache import ProxyCache, _safe_wheel_path


class TestSafeWheelPath:
    """Tests for _safe_wheel_path containment checks."""

    def test_valid_wheel_filename(self, tmp_path: Path) -> None:
        """A normal wheel filename resolves under the cache root.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        path = _safe_wheel_path(tmp_path, "ansible_collection-1.0.0-py3-none-any.whl")
        assert path == tmp_path / "ansible_collection-1.0.0-py3-none-any.whl"

    def test_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """Parent-directory traversal in the filename is rejected.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        with pytest.raises(ValueError, match=r"escapes cache directory|Invalid wheel filename"):
            _safe_wheel_path(tmp_path, "../outside.whl")

    def test_rejects_nested_path(self, tmp_path: Path) -> None:
        """Nested path components in the filename are rejected.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        with pytest.raises(ValueError, match=r"Invalid wheel filename"):
            _safe_wheel_path(tmp_path, "nested/outside.whl")

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        """Symlinks that resolve outside the cache root are rejected.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_wheel = outside / "escape.whl"
        outside_wheel.write_bytes(b"wheel")

        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        link_name = wheels_dir / "escape.whl"
        link_name.symlink_to(outside_wheel)

        with pytest.raises(ValueError, match=r"escapes cache directory|Invalid wheel filename"):
            _safe_wheel_path(wheels_dir, "escape.whl")


class TestProxyCacheWheelAccess:
    """Integration tests for ProxyCache wheel read/write paths."""

    def test_put_and_get_wheel_roundtrip(self, tmp_path: Path) -> None:
        """Cached wheels can be written and read back.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache = ProxyCache(cache_dir=tmp_path / "cache")
        data = b"PK\x03\x04fake-wheel"
        cache.put_wheel("demo-1.0.0-py3-none-any.whl", data)
        assert cache.get_wheel("demo-1.0.0-py3-none-any.whl") == data

    def test_clear_preserves_cache_root_and_clears_subdirs(self, tmp_path: Path) -> None:
        """clear() wipes cached wheels and leaves the cache root intact.

        The cache root is often a volume mount at ``/cache``; removing it
        fails with ``PermissionError`` on Podman/K8s.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        root = tmp_path / "cache"
        cache = ProxyCache(cache_dir=root)
        cache.put_wheel("demo-1.0.0-py3-none-any.whl", b"wheel")
        marker = root / ".volume-mount"
        marker.write_text("preserved", encoding="utf-8")

        cache.clear()

        assert root.is_dir()
        assert marker.read_text(encoding="utf-8") == "preserved"
        assert cache.wheels_dir.is_dir()
        assert cache.get_wheel("demo-1.0.0-py3-none-any.whl") is None

    def test_clear_replaces_directory_symlinks(self, tmp_path: Path) -> None:
        """clear() unlinks managed directory symlinks and recreates real dirs.

        ``shutil.rmtree`` rejects symlinks; a custom cache root with linked
        ``wheels`` must still clear successfully.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        root = tmp_path / "cache"
        root.mkdir()
        real_wheels = tmp_path / "real-wheels"
        real_wheels.mkdir()
        (real_wheels / "stale.whl").write_bytes(b"stale")
        (root / "wheels").symlink_to(real_wheels)

        cache = ProxyCache(cache_dir=root)
        cache.clear()

        assert root.is_dir()
        assert cache.wheels_dir.is_dir()
        assert not cache.wheels_dir.is_symlink()
        assert not (cache.wheels_dir / "stale.whl").exists()
        # Symlink targets are left intact; only the managed links are replaced.
        assert (real_wheels / "stale.whl").exists()

    def test_clear_replaces_dangling_symlinks(self, tmp_path: Path) -> None:
        """clear() removes dangling managed symlinks so mkdir can succeed.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        root = tmp_path / "cache"
        cache = ProxyCache(cache_dir=root)
        cache.wheels_dir.rmdir()
        cache.wheels_dir.symlink_to(tmp_path / "missing-wheels")

        cache.clear()

        assert cache.wheels_dir.is_dir()
        assert not cache.wheels_dir.is_symlink()

    def test_get_wheel_rejects_traversal(self, tmp_path: Path) -> None:
        """get_wheel rejects traversal attempts before reading the filesystem.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        cache = ProxyCache(cache_dir=tmp_path / "cache")
        secret = tmp_path / "secret.txt"
        secret.write_text("secret", encoding="utf-8")

        traversal = f"..{os.sep}secret.txt"
        with pytest.raises(ValueError, match=r"escapes cache directory|Invalid wheel filename"):
            cache.get_wheel(traversal)
