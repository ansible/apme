"""Collection wheel cache backed by XDG_CACHE_HOME."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ansible-collection-proxy"


def _safe_wheel_path(base: Path, filename: str) -> Path:
    """Resolve a wheel filename under *base*, rejecting traversal attempts.

    Resolves the path first (eliminating symlinks and ``..`` components), then
    validates each component of the relative suffix and reconstructs the result
    purely from the trusted cache root so static analysis can verify containment.

    Args:
        base: Base directory the file must reside under.
        filename: Untrusted wheel filename to resolve.

    Returns:
        Resolved path confirmed to be under base.

    Raises:
        ValueError: When the filename is invalid or escapes the base directory.
    """
    base_resolved = base.resolve()
    resolved = Path(os.path.realpath(base_resolved / filename))
    if not resolved.is_relative_to(base_resolved):
        msg = f"Path escapes cache directory: {filename!r}"
        raise ValueError(msg)

    relative_parts = resolved.relative_to(base_resolved).parts
    for part in relative_parts:
        if part in (".", "..") or os.sep in part or (os.altsep and os.altsep in part):
            msg = f"Invalid wheel filename: {filename!r}"
            raise ValueError(msg)

    if len(relative_parts) != 1:
        msg = f"Invalid wheel filename: {filename!r}"
        raise ValueError(msg)

    return base_resolved.joinpath(relative_parts[0])


class ProxyCache:
    """Manages cached collection wheels on disk."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        """Initialise cache directories under *cache_dir* (or XDG default).

        Args:
            cache_dir: Root directory for cached data. If None, uses XDG cache default.
        """
        self.root = cache_dir or _default_cache_dir()
        self.wheels_dir = self.root / "wheels"
        self.enabled = True
        try:
            self.wheels_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.enabled = False
            logger.warning(
                "wheel_cache_unavailable error_type=%s; downloads will bypass disk cache", type(exc).__name__
            )

    def config_identity(self) -> tuple[str, bool] | None:
        """Read the persisted source digest without loading credentials.

        Returns:
            The SHA-256 digest and admin-managed flag, or None when unavailable.
        """
        try:
            path = _safe_wheel_path(self.root, ".galaxy-source.sha256")
            with path.open(encoding="ascii") as stream:
                raw = stream.read(256)
            value = json.loads(raw)
            fingerprint = value.get("fingerprint") if isinstance(value, dict) else None
            managed = value.get("managed") if isinstance(value, dict) else None
            if (
                isinstance(fingerprint, str)
                and len(fingerprint) == 64
                and all(char in "0123456789abcdef" for char in fingerprint)
                and isinstance(managed, bool)
            ):
                return fingerprint, managed
        except (OSError, ValueError, UnicodeError):
            return None
        return None

    def bind_config(self, fingerprint: str, *, managed: bool = False) -> None:
        """Retain wheels only when their persisted source identity matches.

        Storage failure disables disk caching for this process, allowing fresh
        Hub downloads without serving artifacts from an unverified old source.

        Args:
            fingerprint: SHA-256 digest of effective source configuration.
            managed: Whether the Gateway must restore this configuration after restart.

        Raises:
            ValueError: When the fingerprint is not a SHA-256 hex digest.
        """
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("Invalid Galaxy source fingerprint")
        self.enabled = False
        try:
            identity = self.config_identity()
            if identity != (fingerprint, managed):
                if identity is None or identity[0] != fingerprint:
                    self.clear()
                path = _safe_wheel_path(self.root, ".galaxy-source.sha256")
                fd, filename = tempfile.mkstemp(dir=self.root, suffix=".tmp")
                temporary = Path(filename)
                try:
                    with os.fdopen(fd, "w", encoding="ascii") as stream:
                        json.dump({"fingerprint": fingerprint, "managed": managed}, stream)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            logger.warning(
                "wheel_cache_binding_unavailable error_type=%s; downloads will bypass disk cache", type(exc).__name__
            )
            return
        self.enabled = True

    def get_wheel(self, filename: str) -> bytes | None:
        """Return cached wheel bytes, or None if not cached.

        Args:
            filename: Wheel filename under the wheels cache directory.

        Returns:
            Cached wheel bytes, or None if the file is not present.
        """
        if not self.enabled:
            return None
        path = _safe_wheel_path(self.wheels_dir, filename)
        if path.exists():
            return path.read_bytes()
        return None

    def put_wheel(self, filename: str, data: bytes) -> Path:
        """Write a wheel to the cache atomically and return its path.

        Args:
            filename: Destination filename under the wheels cache.
            data: Raw wheel bytes to write.

        Returns:
            Path to the written wheel file.

        Raises:
            OSError: When the temporary file cannot be written or renamed.
        """
        if not self.enabled:
            raise OSError("Wheel cache is unavailable for the current Galaxy configuration")
        path = _safe_wheel_path(self.wheels_dir, filename)
        fd, tmp = tempfile.mkstemp(dir=self.wheels_dir, suffix=".tmp")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            tmp_path.rename(path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        return path

    def wheel_path(self, filename: str) -> Path | None:
        """Return the path to a cached wheel if it exists.

        Args:
            filename: Wheel filename to look up.

        Returns:
            Path to the cached file, or None if it does not exist.
        """
        if not self.enabled:
            return None
        path = _safe_wheel_path(self.wheels_dir, filename)
        return path if path.exists() else None

    def clear(self) -> None:
        """Remove cached wheels while preserving the cache root mount."""
        subdir = self.wheels_dir
        if subdir.is_symlink():
            subdir.unlink()
        elif subdir.exists():
            shutil.rmtree(subdir)
        self.wheels_dir.mkdir(parents=True, exist_ok=True)
