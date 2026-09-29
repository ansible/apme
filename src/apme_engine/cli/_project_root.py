"""Discover the project root and derive a deterministic session ID.

Walks upward from the scan target looking for project-root markers,
similar to how ruff discovers ``pyproject.toml``.  The resolved project
root is hashed to produce a short, stable session ID that survives
across CLI invocations from the same project.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
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


def normalize_targets(raw: str | Path | Sequence[str | Path] | None) -> list[str]:
    """Normalize the CLI ``target`` value to a list of path strings.

    Accepts the historical single-string form (used across unit tests) and
    the multi-target list form (``nargs="*"``). Empty values fall back to ``["."]``.

    Args:
        raw: Raw ``args.target`` value.

    Returns:
        List of target path strings (never empty).
    """
    if raw is None:
        return ["."]
    if isinstance(raw, (str, Path)):
        text = str(raw)
        return [text] if text else ["."]
    items = [str(t) for t in raw if str(t)]
    return items or ["."]


def common_scan_base(targets: Sequence[str | Path] | str | Path) -> Path:
    """Return the common ancestor directory for one or more scan targets.

    Single targets preserve historical behavior (the directory itself, or
    the parent for files). Multiple targets resolve to their common path so
    bundled file paths stay unique.

    Args:
        targets: Normalized target paths, or a single target string.

    Returns:
        Absolute path to the common scan base directory.

    Raises:
        FileNotFoundError: If targets span filesystem roots with no common path.
    """
    items: list[str | Path] = [targets] if isinstance(targets, (str, Path)) else [t for t in targets if str(t)]
    if not items:
        return Path(".").resolve()
    resolved = [Path(t).resolve() for t in items]
    if len(resolved) == 1:
        single = resolved[0]
        return single.parent if single.is_file() else single
    anchors = [str(p.parent if p.is_file() else p) for p in resolved]
    try:
        return Path(os.path.commonpath(anchors)).resolve()
    except ValueError as e:
        raise FileNotFoundError(f"Targets share no common path: {e}") from e


def path_within_targets(targets: Sequence[str | Path], path: Path) -> bool:
    """Return True if *path* is a selected file or under a selected directory.

    Args:
        targets: Normalized CLI target paths.
        path: Resolved absolute path to check.

    Returns:
        True when *path* lies inside the user-selected scan targets.
    """
    resolved = path.resolve()
    for raw in targets:
        target = Path(raw).resolve()
        if target.is_file():
            if resolved == target:
                return True
        elif resolved == target or target in resolved.parents:
            return True
    return False


def resolve_within_base(base: Path, rel_path: str | Path) -> Path:
    """Join a bundle-relative path to a base directory, blocking escape.

    Bundle paths must be relative and resolve inside ``base``. Absolute
    paths and ``..`` escapes raise instead of writing outside the scan tree.

    Args:
        base: Scan base directory (must be a directory).
        rel_path: Bundle-relative file path.

    Returns:
        Resolved absolute path inside ``base``.

    Raises:
        ValueError: If the path is absolute or escapes ``base``.
    """
    rel = Path(rel_path)
    if rel.is_absolute():
        raise ValueError(f"Refusing absolute bundle path: {rel_path}")
    resolved_base = base.resolve()
    out = (resolved_base / rel).resolve()
    if out != resolved_base and resolved_base not in out.parents:
        raise ValueError(f"Refusing bundle path outside scan base: {rel_path}")
    return out


def discover_project_root_for_targets(targets: str | Path | Sequence[str | Path]) -> Path:
    """Discover the project root for one or more scan targets.

    Args:
        targets: Single target or list of targets.

    Returns:
        Absolute path to the discovered project root.
    """
    normalized: list[str] = normalize_targets(targets)
    if len(normalized) == 1:
        return discover_project_root(normalized[0])
    return discover_project_root(common_scan_base(normalized))
