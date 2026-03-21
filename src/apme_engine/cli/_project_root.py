"""Discover the project root and derive a deterministic session ID.

Walks upward from the scan target looking for project-root markers,
similar to how ruff discovers ``pyproject.toml``.  The resolved project
root is hashed to produce a short, stable session ID that survives
across CLI invocations from the same project.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_PROJECT_MARKERS = (
    ".git",
    "galaxy.yml",
    "requirements.yml",
    "ansible.cfg",
    "pyproject.toml",
)


def discover_project_root(target: str | Path) -> Path:
    """Walk upward from *target* to find the nearest project root.

    A directory is considered a project root if it contains any of the
    following markers (checked in order):

    1. ``.git`` — git repository root
    2. ``galaxy.yml`` — Ansible collection root
    3. ``requirements.yml`` — Ansible project root
    4. ``ansible.cfg`` — Ansible configuration root
    5. ``pyproject.toml`` — Python project root

    If no marker is found, the resolved *target* directory itself is
    returned (or its parent, if *target* is a file).

    Args:
        target: File or directory the user passed on the command line.

    Returns:
        Absolute path to the discovered project root.
    """
    current = Path(target).resolve()
    if current.is_file():
        current = current.parent
    anchor = current

    while True:
        for marker in _PROJECT_MARKERS:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    return anchor


def derive_session_id(project_root: Path) -> str:
    """Derive a deterministic session ID from a project root path.

    Uses the first 16 hex characters of the SHA-256 of the resolved
    absolute path.  This is short enough for filesystem paths yet
    collision-resistant for practical use.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        16-character hex string.
    """
    digest = hashlib.sha256(str(project_root).encode()).hexdigest()
    return digest[:16]
