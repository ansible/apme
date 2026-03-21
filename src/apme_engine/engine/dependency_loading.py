"""Path resolution helpers for ARI scanner."""

from __future__ import annotations

import os

from .models import LoadType, YAMLDict
from .utils import escape_url, is_local_path, is_url


def make_target_path(
    root_dir: str,
    src_root: str,
    typ: str,
    target_name: str,
    dep_dir: str = "",
) -> str:
    """Resolve the filesystem path for a target (collection, role, playbook, etc.).

    Args:
        root_dir: Root data directory.
        src_root: Source root directory from path mappings.
        typ: Load type (collection, role, playbook, project, taskfile).
        target_name: Target name (FQCN, path, or URL).
        dep_dir: Optional dependency directory to search first.

    Returns:
        Absolute path to the target on disk.
    """
    target_path = ""

    if dep_dir:
        parts = target_name.split(".")
        if len(parts) == 1:
            parts.append("")
        dep_dir_target_path_candidates = [
            os.path.join(dep_dir, target_name),
            os.path.join(dep_dir, parts[0], parts[1]),
            os.path.join(dep_dir, "ansible_collections", parts[0], parts[1]),
        ]
        for cand_path in dep_dir_target_path_candidates:
            if os.path.exists(cand_path):
                target_path = cand_path
                break
    if target_path != "":
        return target_path

    if typ == LoadType.COLLECTION:
        parts = target_name.split(".")
        if is_local_path(target_name):
            target_path = target_name
        else:
            target_path = os.path.join(root_dir, typ + "s", "src", "ansible_collections", parts[0], parts[1])
    elif typ == LoadType.ROLE:
        if is_local_path(target_name):
            target_path = target_name
        else:
            target_path = os.path.join(root_dir, typ + "s", "src", target_name)
    elif typ == LoadType.PROJECT or typ == LoadType.PLAYBOOK or typ == LoadType.TASKFILE:
        target_path = os.path.join(src_root, escape_url(target_name)) if is_url(target_name) else target_name
    return target_path


def get_source_path(
    root_dir: str,
    path_mappings: YAMLDict,
    ext_type: str,
    ext_name: str,
    is_ext_for_project: bool = False,
) -> str:
    """Return the source path for an external role or collection.

    Args:
        root_dir: Root data directory.
        path_mappings: Path mappings dict from SingleScan.
        ext_type: External type (role or collection).
        ext_name: External name (role name or collection FQCN).
        is_ext_for_project: If True, use project dependencies dir.

    Returns:
        Absolute path to the role or collection source.

    Raises:
        ValueError: If ext_type is not role or collection.
    """
    base_dir = ""
    if is_ext_for_project:
        dep_val = path_mappings.get("dependencies")
        base_dir = str(dep_val) if isinstance(dep_val, str) else ""
    else:
        if ext_type == LoadType.ROLE:
            base_dir = os.path.join(root_dir, "roles", "src")
        elif ext_type == LoadType.COLLECTION:
            base_dir = os.path.join(root_dir, "collections", "src")

    target_path = ""
    if ext_type == LoadType.ROLE:
        target_path = os.path.join(base_dir, ext_name)
    elif ext_type == LoadType.COLLECTION:
        parts = ext_name.split(".")
        target_path = os.path.join(
            base_dir,
            "ansible_collections",
            parts[0],
            parts[1],
        )
    else:
        raise ValueError("Invalid ext_type")
    return target_path


def get_definition_path(path_mappings: YAMLDict, ext_type: str, ext_name: str) -> str:
    """Return the path where external definitions for a role/collection are stored.

    Args:
        path_mappings: Path mappings dict from SingleScan.
        ext_type: External type (role or collection).
        ext_name: External name (role name or collection FQCN).

    Returns:
        Path to the definitions directory.

    Raises:
        ValueError: If ext_type is not role or collection.
    """
    target_path = ""
    ext_defs = path_mappings.get("ext_definitions")
    if isinstance(ext_defs, dict):
        if ext_type == LoadType.ROLE:
            base = ext_defs.get(LoadType.ROLE)
            target_path = os.path.join(str(base), ext_name) if isinstance(base, str) else ""
        elif ext_type == LoadType.COLLECTION:
            base = ext_defs.get(LoadType.COLLECTION)
            target_path = os.path.join(str(base), ext_name) if isinstance(base, str) else ""
    else:
        raise ValueError("Invalid ext_type")
    return target_path
