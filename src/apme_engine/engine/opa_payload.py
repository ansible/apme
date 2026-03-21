"""OPA hierarchy payload serialization for ARI scanner.

Converts ARI scan contexts (trees of RunTargets with annotations) into
JSON-serializable dicts suitable for OPA policy evaluation.
"""

from __future__ import annotations

import contextlib
import datetime
from typing import cast

from .keyutil import detect_type as key_detect_type
from .models import (
    Annotation,
    AnsibleRunContext,
    Location,
    RiskAnnotation,
    RunTarget,
    YAMLDict,
    YAMLValue,
)


def json_safe(v: YAMLValue) -> YAMLValue:
    """Coerce value to a JSON-serializable form.

    Args:
        v: Value to coerce (str, int, float, bool, list, dict, or other).

    Returns:
        JSON-serializable value; non-primitives are stringified.
    """
    if v is None:
        return None
    if isinstance(v, str | int | float | bool):
        return v
    if isinstance(v, list | tuple):
        return [json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    return str(v)


def opts_for_opa(opts: YAMLDict, keys: list[str]) -> YAMLDict:
    """Return a JSON-serializable subset of opts for OPA (only listed keys that exist).

    Args:
        opts: Full options dict.
        keys: Keys to include if present.

    Returns:
        Subset of opts with only the listed keys, JSON-safe values.
    """
    out = {}
    for k in keys:
        if k not in opts:
            continue
        v = opts[k]
        with contextlib.suppress(Exception):
            out[k] = json_safe(v)
    return out


def location_to_dict(loc: Location | None) -> YAMLDict | None:
    """Serialize a Location to a JSON-safe dict for OPA.

    Args:
        loc: Location to serialize, or None.

    Returns:
        Dict with type, value, is_mutable, or None if loc is empty/None.
    """
    if loc is None or getattr(loc, "is_empty", False):
        return None
    return {
        "type": getattr(loc, "type", "") or "",
        "value": json_safe(getattr(loc, "value", "")) or "",
        "is_mutable": getattr(loc, "is_mutable", False),
    }


def annotation_to_dict(an: Annotation) -> YAMLDict:
    """Serialize a full Annotation (including RiskAnnotation detail) for OPA input.

    Args:
        an: Annotation to serialize.

    Returns:
        Dict with type, key, risk_type, and detail-specific fields.
    """
    d = {
        "type": getattr(an, "type", ""),
        "key": getattr(an, "key", ""),
    }
    if not isinstance(an, RiskAnnotation):
        d["risk_type"] = ""
        return d

    d["risk_type"] = getattr(an, "risk_type", "") or ""

    # CommandExecDetail
    cmd = getattr(an, "command", None)
    if cmd is not None:
        d["command"] = json_safe(getattr(cmd, "raw", None)) or ""
    exec_files = getattr(an, "exec_files", None)
    if exec_files:
        d["exec_files"] = [location_to_dict(ef) for ef in exec_files if ef]

    # NetworkTransferDetail (Inbound / Outbound)
    src = getattr(an, "src", None)
    dest = getattr(an, "dest", None)
    if isinstance(src, Location):
        d["src"] = location_to_dict(src)
    if isinstance(dest, Location):
        d["dest"] = location_to_dict(dest)
    for flag in ("is_mutable_src", "is_mutable_dest"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    # PackageInstallDetail
    pkg = getattr(an, "pkg", None)
    if pkg is not None and pkg != "":
        d["pkg"] = json_safe(pkg)
    version = getattr(an, "version", None)
    if version is not None and version != "":
        d["version"] = json_safe(version)
    for flag in ("is_mutable_pkg", "disable_validate_certs", "allow_downgrade"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    # FileChangeDetail
    path_loc = getattr(an, "path", None)
    if isinstance(path_loc, Location):
        d["path_loc"] = location_to_dict(path_loc)
    for flag in ("is_mutable_path", "is_mutable_src", "is_unsafe_write", "is_deletion", "is_insecure_permissions"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    # KeyConfigChangeDetail
    config_key = getattr(an, "key", None)
    if config_key and d.get("key") != config_key:
        d["config_key"] = json_safe(config_key)
    if getattr(an, "is_mutable_key", None) is not None:
        d["is_mutable_key"] = bool(getattr(an, "is_mutable_key", False))

    return d


def node_to_dict(node: RunTarget) -> YAMLDict:
    """Serialize a RunTarget (playcall, rolecall, taskcall, etc.) to a JSON-serializable dict for OPA input.

    Args:
        node: RunTarget to serialize.

    Returns:
        Dict with type, key, file, line, defined_in, and node-specific fields.
    """
    d = {"type": getattr(node, "type", ""), "key": getattr(node, "key", "")}
    spec = getattr(node, "spec", None)
    if spec:
        d["file"] = getattr(spec, "defined_in", "") or ""
        line_num = getattr(spec, "line_num_in_file", None) or getattr(spec, "line_number", None)
        if line_num and isinstance(line_num, list | tuple) and len(line_num) >= 2:
            d["line"] = [int(line_num[0]), int(line_num[1])]
        else:
            d["line"] = None
        d["defined_in"] = getattr(spec, "defined_in", "") or ""
    else:
        d["file"] = ""
        d["line"] = None
        d["defined_in"] = ""
    node_type = getattr(node, "type", "")
    # Play has no line_num_in_file in loader; give playcall a fallback line so OPA L003 can fire
    if node_type == "playcall" and d.get("line") is None and spec:
        play_index = getattr(spec, "index", 0)
        d["line"] = [max(1, play_index + 1), max(1, play_index + 1)]
    # Playcall: name + options (become, become_user) for partial-become and play-name
    # Use null for missing name so OPA L003 (play should have name) can fire
    if node_type == "playcall" and spec:
        name_val = getattr(spec, "name", "") or ""
        d["name"] = name_val if name_val else None
        opts = getattr(spec, "options", None)
        if isinstance(opts, dict):
            d["options"] = opts_for_opa(opts, ["become", "become_user"])
        else:
            d["options"] = {}
    if node_type == "taskcall":
        original_module = (getattr(spec, "module", "") if spec else "") or ""
        d["module"] = getattr(node, "resolved_name", "") or getattr(node, "resolved_action", "") or original_module
        d["original_module"] = original_module
        anns = []
        for an in getattr(node, "annotations", []) or []:
            anns.append(annotation_to_dict(an))
        d["annotations"] = anns
        d["name"] = None
        d["options"] = {}
        d["module_options"] = {}
        if spec:
            name_val = getattr(spec, "name", "") or ""
            d["name"] = name_val if name_val else None
            opts = getattr(spec, "options", None)
            if isinstance(opts, dict):
                d["options"] = opts_for_opa(
                    opts,
                    [
                        "when",
                        "tags",
                        "ignore_errors",
                        "ignore_unreachable",
                        "register",
                        "changed_when",
                        "become",
                        "become_user",
                        "run_once",
                        "local_action",
                        # with_* for M009 (deprecated loops)
                        "with_items",
                        "with_dict",
                        "with_fileglob",
                        "with_subelements",
                        "with_sequence",
                        "with_nested",
                        "with_first_found",
                        "with_indexed_items",
                        "with_flattened",
                        "with_together",
                        "with_random_choice",
                        "with_lines",
                        "with_ini",
                        "with_inventory_hostnames",
                        "with_cartesian",
                    ],
                )
            mo = getattr(spec, "module_options", None)
            if isinstance(mo, dict):
                mo_dict: dict[str, object] = {str(k): json_safe(v) for k, v in mo.items()}
                # OPA L006/L013/L022 expect "cmd"; loader stores free-form shell/command as "_raw"
                if "_raw" in mo_dict and "cmd" not in mo_dict:
                    raw_val = mo_dict.get("_raw")
                    if isinstance(raw_val, str):
                        mo_dict["cmd"] = raw_val
                d["module_options"] = mo_dict
    return d


_FQCN_REJECT = frozenset("/\\: \t\n")


def _looks_like_fqcn(value: str) -> bool:
    """Return True if *value* looks like a valid Ansible FQCN module name.

    Rejects taskfile paths, URLs, and other non-FQCN strings that happen
    to contain 3+ dot-separated parts (e.g. ``roles/my.task.yml``).

    Args:
        value: Candidate module string from a taskcall node.

    Returns:
        True if *value* has >= 3 dot-separated identifier parts and contains
        no path/URL characters.
    """
    if any(ch in value for ch in _FQCN_REJECT):
        return False
    parts = value.split(".")
    return len(parts) >= 3 and all(parts)


def _extract_collection_set(trees_data: list[dict[str, object]]) -> list[str]:
    """Derive unique namespace.collection pairs from FQCN module names in the tree.

    Walks all taskcall nodes and extracts the ``namespace.collection`` prefix
    from any module name with 3+ dot-separated parts (the FQCN pattern).
    ``ansible.builtin`` is excluded — it ships with ansible-core and never
    needs a Galaxy install.  Values that look like file paths or URLs are
    rejected to avoid false positives from ``include_tasks`` / ``import_role``.

    Args:
        trees_data: List of tree dicts, each with a ``nodes`` list of node dicts.

    Returns:
        Sorted, deduplicated list of ``namespace.collection`` strings.
    """
    collections: set[str] = set()
    for tree in trees_data:
        nodes = tree.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "taskcall":
                continue
            for field in ("module", "original_module"):
                mod = node.get(field, "") or ""
                if not isinstance(mod, str) or not _looks_like_fqcn(mod):
                    continue
                parts = mod.split(".")
                fqcn_prefix = f"{parts[0]}.{parts[1]}"
                if fqcn_prefix != "ansible.builtin":
                    collections.add(fqcn_prefix)
    return sorted(collections)


def build_hierarchy_payload(
    contexts: list[AnsibleRunContext],
    scan_type: str,
    scan_name: str,
    collection_name: str,
    role_name: str,
    scan_id: str = "",
) -> YAMLDict:
    """Build OPA input: hierarchy (collection/role/playbook/play/task) + annotations. No native rules.

    Args:
        contexts: AnsibleRunContext list from the scan.
        scan_type: Scan target type (collection, role, playbook, etc.).
        scan_name: Target name.
        collection_name: Collection name if applicable.
        role_name: Role name if applicable.
        scan_id: Optional scan ID; defaults to current UTC timestamp.

    Returns:
        Dict with scan_id, hierarchy (trees with nodes), collection_set, and metadata.
    """
    if not scan_id:
        scan_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    trees_data = []
    for ctx in contexts:
        if not ctx:
            continue
        root_key = getattr(ctx, "root_key", "")
        root_type = key_detect_type(root_key) if root_key else ""
        root_path = ""
        if root_key and " " in root_key:
            root_path = root_key.split(" ", 1)[-1].lstrip(":")
        nodes = []
        for item in getattr(ctx, "sequence", None) or []:
            nodes.append(node_to_dict(item))
        trees_data.append({"root_key": root_key, "root_type": root_type, "root_path": root_path, "nodes": nodes})
    return cast(
        YAMLDict,
        {
            "scan_id": scan_id,
            "hierarchy": trees_data,
            "collection_set": _extract_collection_set(trees_data),
            "metadata": {
                "type": scan_type,
                "name": scan_name,
                "collection_name": collection_name or "",
                "role_name": role_name or "",
            },
        },
    )
