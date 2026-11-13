"""Materialize gateway-fetched dependency snapshots in Engine-owned venvs."""

import fcntl
from collections.abc import Mapping
from pathlib import Path

from apme_engine.daemon.engine_upload import _hash_upload_files, _normalize_upload_path
from apme_engine.venv_manager.session import _venv_site_packages


def install_git_dependencies(venv_root: Path, files: Mapping[str, bytes]) -> None:
    """Install a content-addressed Git snapshot without fetching or running installers.

    Args:
        venv_root: Venv dedicated to this dependency payload hash.
        files: Validated dependency layout and bytes received over gRPC.

    Raises:
        ValueError: A dependency path escapes the venv or its install layout.
    """
    site = _venv_site_packages(venv_root).resolve()
    with (venv_root / ".git-dependencies.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = venv_root / ".git-dependencies-ready"
        if marker.exists():
            return
        for path, content in files.items():
            normalized = _normalize_upload_path(path)
            parts = normalized.split("/")
            if not (
                (parts[0] == "ansible_collections" and len(parts) >= 4) or (parts[0] == "roles" and len(parts) >= 3)
            ):
                raise ValueError("Invalid dependency install layout")
            dest = site / normalized
            if not dest.resolve().is_relative_to(site):
                raise ValueError("Dependency path escapes venv")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        marker.write_text(_hash_upload_files(files))
