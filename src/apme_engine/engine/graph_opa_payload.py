"""OPA hierarchy payload from ContentGraph (ADR-044).

Produces the same JSON structure as ``opa_payload.build_hierarchy_payload()``
but derived from a ``ContentGraph`` instead of ``AnsibleRunContext`` lists.
Used during Phase 1 for shadow-run validation: the output of this function
must match the old pipeline's output.

Public API
----------
- ``content_node_to_opa_dict``  — serialize one node to OPA-compatible dict
- ``build_hierarchy_from_graph`` — build the full OPA input from a graph
"""

from __future__ import annotations

import contextlib
import datetime
from typing import cast

from .content_graph import ContentGraph, ContentNode, EdgeType, NodeType
from .models import Annotation, Location, RiskAnnotation, YAMLDict, YAMLValue


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


def _location_to_dict(loc: Location | None) -> YAMLDict | None:
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
    """Serialize an Annotation (including RiskAnnotation detail) for OPA input.

    Args:
        an: Annotation to serialize.

    Returns:
        Dict with type, key, risk_type, and detail-specific fields.
    """
    d: YAMLDict = {
        "type": getattr(an, "type", ""),
        "key": getattr(an, "key", ""),
    }
    if not isinstance(an, RiskAnnotation):
        d["risk_type"] = ""
        return d

    d["risk_type"] = getattr(an, "risk_type", "") or ""

    cmd = getattr(an, "command", None)
    if cmd is not None:
        d["command"] = json_safe(getattr(cmd, "raw", None)) or ""
    exec_files = getattr(an, "exec_files", None)
    if exec_files:
        d["exec_files"] = [_location_to_dict(ef) for ef in exec_files if ef]

    src = getattr(an, "src", None)
    dest = getattr(an, "dest", None)
    if isinstance(src, Location):
        d["src"] = _location_to_dict(src)
    if isinstance(dest, Location):
        d["dest"] = _location_to_dict(dest)
    for flag in ("is_mutable_src", "is_mutable_dest"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

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

    path_loc = getattr(an, "path", None)
    if isinstance(path_loc, Location):
        d["path_loc"] = _location_to_dict(path_loc)
    for flag in ("is_mutable_path", "is_mutable_src", "is_unsafe_write", "is_deletion", "is_insecure_permissions"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    config_key = getattr(an, "key", None)
    if config_key and d.get("key") != config_key:
        d["config_key"] = json_safe(config_key)
    if getattr(an, "is_mutable_key", None) is not None:
        d["is_mutable_key"] = bool(getattr(an, "is_mutable_key", False))

    return d


# ---------------------------------------------------------------------------
# Per-node serialization
# ---------------------------------------------------------------------------


def content_node_to_opa_dict(node: ContentNode) -> YAMLDict:
    """Serialize a ``ContentNode`` to the OPA node dict format.

    The output shape matches ``opa_payload.node_to_dict(RunTarget)`` so
    that OPA rules see identical input regardless of which pipeline
    produced it.

    Args:
        node: ContentNode to serialize.

    Returns:
        Dict with type, key, file, line, defined_in, and type-specific fields.
    """
    node_type = node.node_type
    opa_type = _OPA_TYPE_MAP.get(node_type, "")
    if not opa_type:
        return {}

    d: YAMLDict = {
        "type": opa_type,
        "key": node.ari_key or node.node_id,
        "file": node.file_path,
        "line": [node.line_start, node.line_end] if node.line_start else None,
        "defined_in": node.file_path,
    }

    if opa_type == "playcall":
        d["name"] = node.name if node.name else None
        become_opts: YAMLDict = {}
        if node.become:
            if node.become.get("become"):
                become_opts["become"] = node.become["become"]
            if node.become.get("become_user"):
                become_opts["become_user"] = node.become["become_user"]
        d["options"] = become_opts
        if d["line"] is None and node.options:
            play_index = node.options.get("index", 0)
            if isinstance(play_index, int):
                d["line"] = [max(1, play_index + 1), max(1, play_index + 1)]

    elif opa_type == "taskcall":
        d["module"] = node.module
        d["original_module"] = node.module
        d["name"] = node.name if node.name else None

        anns: list[YAMLValue] = []
        for an in node.annotations:
            with contextlib.suppress(Exception):
                anns.append(annotation_to_dict(cast(Annotation, an)))
        d["annotations"] = anns

        opts: YAMLDict = {}
        for key in (
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
        ):
            val = node.options.get(key)
            if val is not None:
                with contextlib.suppress(Exception):
                    opts[key] = json_safe(val)
        d["options"] = opts

        mo: YAMLDict = {}
        for k, v in node.module_options.items():
            with contextlib.suppress(Exception):
                mo[str(k)] = json_safe(v)
        if "_raw" in mo and "cmd" not in mo:
            raw_val = mo.get("_raw")
            if isinstance(raw_val, str):
                mo["cmd"] = raw_val
        d["module_options"] = mo

    return d


# ---------------------------------------------------------------------------
# Full hierarchy payload
# ---------------------------------------------------------------------------

_FQCN_REJECT = frozenset("/\\: \t\n")


def build_hierarchy_from_graph(
    graph: ContentGraph,
    scan_type: str,
    scan_name: str,
    collection_name: str = "",
    role_name: str = "",
    scan_id: str = "",
) -> YAMLDict:
    """Build OPA hierarchy payload from a ContentGraph.

    The output is structurally identical to
    ``opa_payload.build_hierarchy_payload()`` so that OPA Rego rules
    consume it without modification.

    Args:
        graph: ContentGraph to serialize.
        scan_type: Scan target type (collection, role, playbook, etc.).
        scan_name: Target name.
        collection_name: Collection name if applicable.
        role_name: Role name if applicable.
        scan_id: Optional scan ID.

    Returns:
        Dict with scan_id, hierarchy, collection_set, and metadata.
    """
    if not scan_id:
        scan_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")

    trees_data = _build_trees(graph)

    return cast(
        YAMLDict,
        {
            "scan_id": scan_id,
            "hierarchy": trees_data,
            "collection_set": _extract_collections(trees_data),
            "metadata": {
                "type": scan_type,
                "name": scan_name,
                "collection_name": collection_name,
                "role_name": role_name,
            },
        },
    )


def _build_trees(graph: ContentGraph) -> list[YAMLDict]:
    """Build one tree dict per root (playbook or role without parents).

    Args:
        graph: ContentGraph to extract trees from.

    Returns:
        List of tree dicts, each with root_key, root_type, root_path, nodes.
    """
    trees: list[YAMLDict] = []

    roots: list[ContentNode] = []
    for node in graph.nodes():
        if node.node_type in (NodeType.PLAYBOOK, NodeType.ROLE):
            in_edges = graph.edges_to(node.node_id, EdgeType.CONTAINS)
            dep_edges = graph.edges_to(node.node_id, EdgeType.DEPENDENCY)
            if not in_edges and not dep_edges:
                roots.append(node)

    for root in roots:
        nodes_list: list[YAMLDict] = []
        sub = graph.subgraph(root.node_id)
        for nid in sub.topological_order():
            content_node = sub.get_node(nid)
            if content_node is None:
                continue
            d = content_node_to_opa_dict(content_node)
            if d:
                nodes_list.append(d)

        root_key = root.ari_key or root.node_id
        root_type = root.node_type.value
        root_path = root.file_path

        trees.append(
            cast(
                YAMLDict,
                {
                    "root_key": root_key,
                    "root_type": root_type,
                    "root_path": root_path,
                    "nodes": nodes_list,
                },
            )
        )

    return trees


def _extract_collections(trees_data: list[YAMLDict]) -> list[str]:
    """Derive unique namespace.collection pairs from FQCN module names.

    Args:
        trees_data: List of tree dicts with nodes lists.

    Returns:
        Sorted, deduplicated list of namespace.collection strings.
    """
    collections: set[str] = set()
    for tree in trees_data:
        nodes = tree.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "taskcall":
                continue
            for field_name in ("module", "original_module"):
                mod = node.get(field_name, "")
                if not isinstance(mod, str):
                    continue
                if any(ch in mod for ch in _FQCN_REJECT):
                    continue
                parts = mod.split(".")
                if len(parts) >= 3 and all(parts):
                    prefix = f"{parts[0]}.{parts[1]}"
                    if prefix != "ansible.builtin":
                        collections.add(prefix)
    return sorted(collections)


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_OPA_TYPE_MAP: dict[NodeType, str] = {
    NodeType.PLAYBOOK: "playbookcall",
    NodeType.PLAY: "playcall",
    NodeType.ROLE: "rolecall",
    NodeType.TASKFILE: "taskfilecall",
    NodeType.TASK: "taskcall",
    NodeType.HANDLER: "taskcall",
    NodeType.BLOCK: "taskcall",
}
