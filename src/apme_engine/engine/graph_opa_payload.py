"""OPA hierarchy payload from ContentGraph (ADR-044).

Produces the established OPA hierarchy fields from a ``ContentGraph`` instead
of ``AnsibleRunContext`` lists. It also carries task collection search order
for the Ansible validator; OPA rules ignore that additive field.

Public API
----------
- ``content_node_to_opa_dict``  — serialize one node to OPA-compatible dict
- ``build_hierarchy_from_graph`` — build the full OPA input from a graph
"""

from __future__ import annotations

import datetime
import json
import posixpath
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

from apme_engine.graph.content_graph import ContentGraph, ContentNode, EdgeType, NodeType
from apme_engine.graph.rules._module_risk_mapping import get_risk_profile, resolve_field
from apme_engine.graph.sensitivity import redact_urls_in_text

from .models import Annotation, Location, RiskAnnotation, YAMLDict, YAMLValue


class OpaPayloadSerializationError(RuntimeError):
    """Policy assessment cannot proceed with truncated serialized input."""


_MODULE_DEFAULTS_CONTEXT_LIMIT = 64
_MODULE_DEFAULTS_LAYER_LIMIT = 256
_JSON_SAFE_NODE_LIMIT = 20_000
_JSON_SAFE_BYTE_LIMIT = 8 * 1024 * 1024
_DEFAULTS_CACHE_NODE_LIMIT = 200_000
_HIERARCHY_DEFAULTS_BYTE_LIMIT = 32 * 1024 * 1024
_HIERARCHY_ARGUMENT_NODE_LIMIT = 200_000
_HIERARCHY_ARGUMENT_BYTE_LIMIT = 32 * 1024 * 1024
_DEFAULTS_SCOPE_TYPES = frozenset({NodeType.PLAY, NodeType.BLOCK, NodeType.TASK, NodeType.HANDLER})
_APPLY_INCLUDE_MODULES = frozenset(
    f"{prefix}{name}"
    for prefix in ("", "ansible.builtin.", "ansible.legacy.")
    for name in ("include_tasks", "include_role")
)
_ROLE_CALL_MODULES = frozenset(
    f"{prefix}{name}"
    for prefix in ("", "ansible.builtin.", "ansible.legacy.")
    for name in ("include_role", "import_role")
)


@dataclass(frozen=True)
class _ModuleDefaultsPath:
    """Defaults identity plus a role entry selection awaiting file traversal.

    Attributes:
        layers: Interned defaults layers on this invocation path.
        role_entries: Selected task and handler file IDs, or unknown selectors.
    """

    layers: tuple[int, ...]
    role_entries: tuple[str | None, str | None] | None = None


@dataclass
class _ModuleDefaultsContexts:
    """Ordered raw defaults layers for distinct positional paths.

    Attributes:
        paths: Interned layer signatures mapped to root-to-node raw layers.
        incomplete: Whether ancestry was truncated, cyclic, or has unknown apply data.
    """

    paths: dict[_ModuleDefaultsPath, tuple[YAMLValue, ...]] = field(default_factory=dict)
    incomplete: bool = False


@dataclass(frozen=True)
class _DefaultsSize:
    """Cached conversion cost and JSON size of a defaults layer or context.

    Attributes:
        nodes: Expanded value nodes visited by json_safe.
        conversion_bytes: Byte allowance charged by json_safe.
        serialized_bytes: Size using the payload limiter's JSON encoding.
    """

    nodes: int
    conversion_bytes: int
    serialized_bytes: int


def _measure_defaults_layer(value: YAMLValue) -> _DefaultsSize | None:
    """Measure an already-normalized layer without allocating another copy.

    Args:
        value: JSON-safe layer retained in the request-local context cache.

    Returns:
        Bounded conversion cost and JSON size, or None when it cannot fit.
    """
    pending = [value]
    nodes = conversion_bytes = serialized_bytes = 0
    while pending:
        current = pending.pop()
        nodes += 1
        conversion_bytes += 2
        if isinstance(current, dict | list):
            if len(current) > _JSON_SAFE_NODE_LIMIT - nodes - len(pending):
                return None
            serialized_bytes += 2 + 2 * max(0, len(current) - 1)
            if isinstance(current, dict):
                for name, child in current.items():
                    if len(name) > _JSON_SAFE_BYTE_LIMIT - conversion_bytes:
                        return None
                    key_bytes = len(json.dumps(name))
                    conversion_bytes += key_bytes
                    serialized_bytes += key_bytes + 2
                    pending.append(child)
            else:
                pending.extend(current)
        else:
            if isinstance(current, str) and len(current) > _JSON_SAFE_BYTE_LIMIT - conversion_bytes:
                return None
            encoded_bytes = len(json.dumps(current))
            conversion_bytes += encoded_bytes if isinstance(current, str) else len(str(current))
            serialized_bytes += encoded_bytes
        if nodes > _JSON_SAFE_NODE_LIMIT or conversion_bytes > _JSON_SAFE_BYTE_LIMIT:
            return None
    return _DefaultsSize(nodes, conversion_bytes, serialized_bytes)


@dataclass
class _DefaultsPayloadBudget:
    """Shared preflight sizes and remaining allowance for one serialized batch.

    Attributes:
        remaining: Hierarchy byte allowance, charged by the final payload limiter.
        arguments: Shared work allowance for arguments, options, and annotations.
        layers: Bounded sizes of interned defaults layers, including failures.
        contexts: Bounded sizes of distinct ordered contexts, including failures.
    """

    remaining: list[int]
    arguments: list[int] = field(
        default_factory=lambda: [_HIERARCHY_ARGUMENT_NODE_LIMIT, _HIERARCHY_ARGUMENT_BYTE_LIMIT]
    )
    layers: dict[int, _DefaultsSize | None] = field(default_factory=dict)
    contexts: dict[tuple[int, ...], _DefaultsSize | None] = field(default_factory=dict)

    def fits(self, defaults: _ModuleDefaultsContexts) -> bool:
        """Check individual and aggregate limits before copying task defaults.

        Args:
            defaults: Distinct positional contexts for one task.

        Returns:
            Whether conversion and serialization can fit the remaining budgets.
        """
        if all(not values for values in defaults.paths.values()):
            return True
        nodes, conversion_bytes = 1, 2
        serialized_bytes = 2 + 2 * max(0, len(defaults.paths) - 1)
        if serialized_bytes + 2 * len(defaults.paths) > self.remaining[0]:
            return False
        for path, values in defaults.paths.items():
            if path.layers not in self.contexts:
                context_nodes, context_bytes = 1, 2
                context_size = 2 + 2 * max(0, len(values) - 1)
                self.contexts[path.layers] = None
                for layer_id, value in zip(path.layers, values, strict=True):
                    if layer_id not in self.layers:
                        self.layers[layer_id] = _measure_defaults_layer(value)
                    size = self.layers[layer_id]
                    if size is None:
                        break
                    context_nodes += size.nodes
                    context_bytes += size.conversion_bytes
                    context_size += size.serialized_bytes
                    if context_nodes > _JSON_SAFE_NODE_LIMIT or context_bytes > _JSON_SAFE_BYTE_LIMIT:
                        break
                else:
                    self.contexts[path.layers] = _DefaultsSize(context_nodes, context_bytes, context_size)
            context = self.contexts[path.layers]
            if context is None:
                return False
            nodes += context.nodes
            conversion_bytes += context.conversion_bytes
            serialized_bytes += context.serialized_bytes
            if (
                nodes > _JSON_SAFE_NODE_LIMIT
                or conversion_bytes > _JSON_SAFE_BYTE_LIMIT
                or serialized_bytes > self.remaining[0]
            ):
                return False
        return serialized_bytes <= self.remaining[0]


def _include_apply_defaults(
    node: ContentNode, *, _budget: list[int] | None = None
) -> tuple[tuple[YAMLValue, ...], bool]:
    """Read the synthetic block defaults applied only to an include's descendants.

    Args:
        node: Include task retaining its authored module arguments.
        _budget: Shared remaining node and byte allowance for cached defaults.

    Returns:
        Raw applied layer, if present, and whether apply cannot be inspected.
    """
    arguments = node.effective_module_options
    apply = arguments.get("apply", {})
    if not isinstance(apply, dict):
        return (), True
    if "module_defaults" in apply:
        try:
            return (json_safe(apply["module_defaults"], _budget=_budget),), False
        except RecursionError:
            return (), True
    if isinstance(node.options.get("args"), str):
        return (), True
    return (), False


def _build_module_defaults_contexts(graph: ContentGraph) -> dict[str, _ModuleDefaultsContexts]:
    """Compute bounded defaults contexts once in positional topological order.

    Preserve alternatives rather than merging defaults from unrelated callers.
    Layer identities preserve mapping order, which can affect group precedence.
    Non-positional edges cannot contribute inheritance or introduce cycles.

    Args:
        graph: Graph supplying authored defaults and positional parent edges.

    Returns:
        Context cache for every node; cyclic ancestry is marked incomplete.
    """
    nodes = {node.node_id: node for node in graph.nodes()}
    role_names: dict[str, set[str]] = {}
    role_ids: set[str] = set()
    for nid, node in nodes.items():
        if node.node_type == NodeType.ROLE:
            role_ids.add(nid)
            for name in (node.name, node.role_fqcn, node.role_fqcn.rsplit(".", 1)[-1]):
                if name:
                    role_names.setdefault(name, set()).add(nid)
    stale_by_source: dict[str, set[str]] = {}
    dynamic_sources: set[str] = set()
    for source, node in nodes.items():
        if node.node_type not in {NodeType.TASK, NodeType.HANDLER}:
            continue
        role_edges = [
            (target, attrs)
            for target, attrs in graph.edges_from(source)
            if target in role_ids and attrs.get("edge_type") in {EdgeType.INCLUDE.value, EdgeType.IMPORT.value}
        ]
        stale_roles = stale_by_source.setdefault(source, set())
        if node.module not in _ROLE_CALL_MODULES:
            stale_roles.update(target for target, _attrs in role_edges)
            continue
        arguments = node.effective_module_options
        role_name = arguments.get("name")
        dynamic_name = isinstance(role_name, str) and any(marker in role_name for marker in ("{{", "{%", "{#"))
        if dynamic_name:
            dynamic_sources.add(source)
        elif isinstance(role_name, str) and role_name:
            candidates = role_names.get(role_name, set())
            targets = {target for target, _attrs in role_edges}
            stale_roles.update(candidates - targets)
            for target in targets:
                role = nodes[target]
                if (role.name or role.role_fqcn) and target not in candidates:
                    stale_roles.add(target)
        expected_edge = EdgeType.IMPORT.value if node.module.endswith("import_role") else EdgeType.INCLUDE.value
        stale_roles.update(target for target, attrs in role_edges if attrs.get("edge_type") != expected_edge)
    parents: dict[str, list[tuple[str, dict[str, object]]]] = {nid: [] for nid in nodes}
    children: dict[str, set[str]] = {nid: set() for nid in nodes}
    role_files: dict[str, dict[str, dict[str, str]]] = {}
    for source in nodes:
        for target, attrs in graph.edges_from(source):
            if target in nodes and graph.is_positional_edge(source, target, attrs.get("edge_type")):
                parents[target].append((source, attrs))
                children[source].add(target)
                section, file = attrs.get("role_section"), attrs.get("role_file")
                if section in {"tasks", "handlers"} and isinstance(file, str):
                    role_files.setdefault(source, {}).setdefault(str(section), {})[file] = target
    remaining = {nid: len({parent for parent, _attrs in parent_ids}) for nid, parent_ids in parents.items()}
    ready = deque(sorted(nid for nid, count in remaining.items() if count == 0))
    cache: dict[str, _ModuleDefaultsContexts] = {}
    layers: dict[str, int] = {}
    applied_layers: dict[str, tuple[tuple[YAMLValue, ...], bool]] = {}
    defaults_budget = [_DEFAULTS_CACHE_NODE_LIMIT, _HIERARCHY_DEFAULTS_BYTE_LIMIT]

    def role_entries(role_id: str, options: dict[str, object]) -> tuple[tuple[str | None, str | None] | None, bool]:
        """Select role entry files without treating the full inventory as execution.

        Args:
            role_id: Role whose file inventory is indexed.
            options: One invocation's literal entry-file selectors.

        Returns:
            Task/handler entry IDs and whether a selector is unavailable.
        """
        if role_id not in role_files:
            return None, False
        selected: list[str | None] = []
        incomplete = False
        for section in ("tasks", "handlers"):
            key = "tasks_from" if section == "tasks" else "handlers_from"
            entry = options.get(key, "main")
            if not isinstance(entry, str) or any(marker in entry for marker in ("{{", "{%", "{#")):
                selected.append(None)
                incomplete = True
                continue
            files = role_files[role_id].get(section, {})
            entry = posixpath.normpath(entry)
            candidates = (
                (entry, entry + ".yml", entry + ".yaml") if key in options else ("main.yml", "main.yaml", "main")
            )
            target = next((files[name] for name in candidates if name in files), "")
            if not target and key in options:
                selected.append(None)
                incomplete = True
            else:
                selected.append(target)
        return (selected[0], selected[1]), incomplete

    def signature(values: tuple[YAMLValue, ...]) -> tuple[int, ...]:
        """Intern layers without discarding authored mapping order.

        Args:
            values: Local or applied raw defaults layers.

        Returns:
            Hashable layer identities for context deduplication.
        """
        keys = [json.dumps(value, ensure_ascii=False, separators=(",", ":")) for value in values]
        return tuple(layers.setdefault(key, len(layers)) for key in keys)

    while ready:
        nid = ready.popleft()
        node = nodes[nid]
        local: tuple[YAMLValue, ...] = ()
        local_unknown = False
        try:
            if (
                (not parents[nid] or any(cache[parent].paths for parent, _attrs in parents[nid]))
                and node.node_type in _DEFAULTS_SCOPE_TYPES
                and "module_defaults" in node.options
            ):
                local = (json_safe(node.options["module_defaults"], _budget=defaults_budget),)
            local_signature = signature(local)
        except RecursionError:
            local, local_signature, local_unknown = (), (), True
        state = _ModuleDefaultsContexts(
            incomplete=local_unknown or any(cache[parent].incomplete for parent, _attrs in parents[nid])
        )
        if not parents[nid]:
            entries, incomplete = role_entries(nid, {}) if node.node_type == NodeType.ROLE else (None, False)
            state.paths[_ModuleDefaultsPath(local_signature, entries)] = local
            state.incomplete |= incomplete
        for parent, attrs in sorted(parents[nid], key=lambda item: (item[0], int(str(item[1].get("position", 0))))):
            if not cache[parent].paths:
                continue
            applied: tuple[YAMLValue, ...] = ()
            applies = (
                attrs.get("edge_type") == EdgeType.INCLUDE.value and nodes[parent].module in _APPLY_INCLUDE_MODULES
            )
            if applies:
                if parent not in applied_layers:
                    applied_layers[parent] = _include_apply_defaults(nodes[parent], _budget=defaults_budget)
                applied, unknown_apply = applied_layers[parent]
                state.incomplete |= unknown_apply
            # Task arguments are mutable through apply_yaml; edge metadata is
            # only a snapshot from graph construction. Static role and role
            # dependency declarations instead have distinct per-edge options.
            parent_node = nodes[parent]
            invocation: object
            if (
                node.node_type == NodeType.ROLE
                and attrs.get("edge_type") in {EdgeType.INCLUDE.value, EdgeType.IMPORT.value}
                and parent_node.module in _ROLE_CALL_MODULES
            ):
                invocation = parent_node.effective_module_options
                if isinstance(parent_node.options.get("args"), str):
                    state.incomplete = True
            else:
                invocation = attrs.get("role_invocation", {})
            invocation_options = cast(dict[str, object], invocation) if isinstance(invocation, dict) else {}
            try:
                edge_defaults = (
                    (json_safe(cast(YAMLValue, invocation_options["module_defaults"]), _budget=defaults_budget),)
                    if "module_defaults" in invocation_options
                    else ()
                )
                edge_signature = signature(applied + edge_defaults)
            except RecursionError:
                applied, edge_defaults, edge_signature = (), (), ()
                state.incomplete = True
            entries, incomplete = (
                role_entries(nid, invocation_options) if node.node_type == NodeType.ROLE else (None, False)
            )
            state.incomplete |= incomplete
            suffix = edge_signature + local_signature
            for path, values in cache[parent].paths.items():
                section = attrs.get("role_section")
                if section in {"tasks", "handlers"} and path.role_entries is not None:
                    selected_entry = path.role_entries[0 if section == "tasks" else 1]
                    if selected_entry is not None and selected_entry != nid:
                        continue
                if len(path.layers) + len(suffix) > _MODULE_DEFAULTS_LAYER_LIMIT:
                    state.incomplete = True
                    continue
                current = _ModuleDefaultsPath(path.layers + suffix, entries)
                if current in state.paths:
                    continue
                if len(state.paths) >= _MODULE_DEFAULTS_CONTEXT_LIMIT:
                    state.incomplete = True
                    break
                state.paths[current] = values + applied + edge_defaults + local
        cache[nid] = state
        for child in sorted(children[nid]):
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    for nid in nodes.keys() - cache.keys():
        cache[nid] = _ModuleDefaultsContexts(incomplete=True)
    # A stale edge matters only if its caller is reachable or its reachability
    # could not be assessed. Propagate after path selection so inventory-only
    # files cannot invalidate unrelated active role invocations.
    stale = deque(
        role
        for source, roles in stale_by_source.items()
        if cache[source].paths or cache[source].incomplete
        for role in roles
    )
    all_roles_added = any(cache[source].paths or cache[source].incomplete for source in dynamic_sources)
    if all_roles_added:
        stale.extend(role_ids)
    visited: set[str] = set()
    while stale:
        nid = stale.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        cache[nid].incomplete = True
        stale.extend(children[nid])
        stale.extend(stale_by_source.get(nid, ()))
        if nid in dynamic_sources and not all_roles_added:
            stale.extend(role_ids)
            all_roles_added = True
    return cache


_PLAY_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "connection",
        "strategy",
        "gather_facts",
        "any_errors_fatal",
        "max_fail_percentage",
        "serial",
        "order",
        "throttle",
        "timeout",
        "ignore_errors",
        "ignore_unreachable",
        "no_log",
        "run_once",
        "environment",
    }
)

_TASK_OPTION_KEYS: frozenset[str] = frozenset(
    {
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
        "loop",
        "vars",
        "connection",
        "environment",
        "no_log",
    }
)

_L066_TASK_TYPES: frozenset[NodeType] = frozenset({NodeType.TASK, NodeType.BLOCK})
_REMOTE_URL_SCHEMES = frozenset(
    {
        "ftp",
        "ftps",
        "git",
        "git+http",
        "git+https",
        "git+ssh",
        "http",
        "https",
        "ssh",
        "svn",
        "svn+http",
        "svn+https",
        "svn+ssh",
    }
)
_SCP_SOURCE_MODULES = frozenset({"git", "subversion"})


def _inbound_risk_annotation(node: ContentNode, module_options: YAMLDict) -> YAMLDict | None:
    """Build the inbound transfer annotation consumed by R118, when applicable.

    The graph serializer is the active risk annotation boundary. It derives
    this annotation from the same module risk profiles used by graph rules,
    while keeping the annotation shape expected by the OPA bundle.

    Args:
        node: Task or handler node being serialized.
        module_options: Effective module options including task-level args.

    Returns:
        Serialized inbound risk annotation, or ``None`` when the task is not
        an inbound-transfer module.
    """
    module_name = node.module.rsplit(".", 1)[-1]
    profile = get_risk_profile(node.module)
    if profile is None and node.module.startswith("ansible.legacy."):
        profile = get_risk_profile(module_name)
    if profile is None or profile.risk_type != "inbound":
        return None

    src = resolve_field(module_options, profile, "src")
    dest = resolve_field(module_options, profile, "dest")
    if not src or not _is_remote_source(src, module_name):
        return None

    # unarchive only reads from the managed node when explicitly told to;
    # scheme-less templated sources are retained as potentially remote.
    if module_name == "unarchive":
        remote_src = module_options.get("remote_src", False)
        remote_src_enabled = (
            remote_src is True
            or remote_src == 1
            or (isinstance(remote_src, str) and remote_src.strip().lower() in {"1", "true", "yes", "y", "on"})
        )
        if not remote_src_enabled:
            return None

    annotation: YAMLDict = {
        "type": "risk_annotation",
        "key": "",
        "risk_type": "inbound_transfer",
    }
    if src is not None:
        is_mutable = "{{" in src or "{%" in src
        src = redact_urls_in_text(src)
        is_url = (
            "{{" in src
            or "{%" in src
            or _has_remote_url_scheme(src)
            or module_name == "get_url"
            or (module_name in _SCP_SOURCE_MODULES and _is_scp_style_remote_source(src))
        )
        annotation["src"] = {
            "type": "url" if is_url else "file",
            "value": src,
            "is_mutable": is_mutable,
        }
    if dest is not None:
        annotation["dest"] = {
            "type": "file",
            "value": redact_urls_in_text(dest),
            "is_mutable": "{{" in dest or "{%" in dest,
        }
    return annotation


def _is_remote_source(source: str, module_name: str) -> bool:
    """Return whether a source is remote or dynamically resolved.

    Templated values without a URI scheme are treated as potentially remote.
    Explicit ``file://`` URLs and ordinary paths are local; supported remote
    URI schemes and SCP-style Git/SVN locations are remote.

    Args:
        source: Source string from a task's module options.
        module_name: Unqualified Ansible module name.

    Returns:
        True for a potentially remote source, otherwise False.
    """
    if len(source) >= 3 and source[0].isalpha() and source[1] == ":" and source[2] in "/\\":
        return False

    try:
        scheme = urlsplit(source).scheme.lower()
    except ValueError:
        return False
    if scheme in _REMOTE_URL_SCHEMES:
        return True
    # urlsplit interprets a bare SCP hostname as a URI scheme. Preserve
    # explicit local/unsupported URLs, but allow host:path through below.
    if scheme == "file" or "://" in source:
        return False

    if "{{" in source or "{%" in source:
        return True

    # Git/SVN commonly accept scp-like forms such as ``git@host:org/repo``.
    return module_name in _SCP_SOURCE_MODULES and _is_scp_style_remote_source(source)


def _has_remote_url_scheme(source: str) -> bool:
    """Return whether a source uses a known remote URL scheme.

    Args:
        source: Source string to inspect.

    Returns:
        True when the URI scheme is supported as a remote source.
    """
    try:
        return urlsplit(source).scheme.lower() in _REMOTE_URL_SCHEMES
    except ValueError:
        return False


def _is_scp_style_remote_source(source: str) -> bool:
    """Return whether a source uses a remote scp-style host:path form.

    Args:
        source: Source string to inspect.

    Returns:
        True when the source has a host and path separated by a colon.
    """
    host, separator, path = source.partition(":")
    return bool(
        separator
        and host
        and path
        and not any(character.isspace() for character in host)
        and "/" not in host
        and not (len(host) == 1 and host.isalpha())
        and not source.startswith(("./", "../"))
    )


def json_safe(v: YAMLValue, *, _budget: list[int] | None = None) -> YAMLValue:
    """Coerce value to a JSON-serializable form.

    Args:
        v: Value to coerce (str, int, float, bool, list, dict, or other).
        _budget: Optional shared remaining node and byte allowance, charged even
            when conversion stops before completing a value.

    Returns:
        JSON-serializable value; non-primitives are stringified.

    Nesting or expanded aliases exceeding safe limits raise ``RecursionError``.
    """
    node_allowance = min(_JSON_SAFE_NODE_LIMIT, _budget[0]) if _budget is not None else _JSON_SAFE_NODE_LIMIT
    byte_allowance = min(_JSON_SAFE_BYTE_LIMIT, _budget[1]) if _budget is not None else _JSON_SAFE_BYTE_LIMIT
    remaining = node_allowance
    remaining_bytes = byte_allowance

    def count_string(value: str) -> None:
        nonlocal remaining_bytes
        if len(value) > remaining_bytes:
            raise RecursionError("Expanded JSON exceeds the byte limit")
        remaining_bytes -= len(json.dumps(value))
        if remaining_bytes < 0:
            raise RecursionError("Expanded JSON exceeds the byte limit")

    def convert(value: YAMLValue) -> YAMLValue:
        nonlocal remaining, remaining_bytes
        remaining -= 1
        remaining_bytes -= 2  # delimiters and separators
        if remaining < 0 or remaining_bytes < 0:
            raise RecursionError("Expanded JSON exceeds the assessment limit")
        if isinstance(value, str):
            count_string(value)
            return value
        if value is None or isinstance(value, int | float | bool):
            remaining_bytes -= len(str(value))
            if remaining_bytes < 0:
                raise RecursionError("Expanded JSON exceeds the byte limit")
            return value
        if isinstance(value, list | tuple):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            result: YAMLDict = {}
            for key, item in value.items():
                name = str(key)
                count_string(name)
                result[name] = convert(item)
            return result
        text = str(value)
        count_string(text)
        return text

    try:
        return convert(v)
    finally:
        if _budget is not None:
            _budget[0] -= node_allowance - max(0, remaining)
            _budget[1] -= byte_allowance - max(0, remaining_bytes)


def _location_to_dict(loc: Location | None, *, _budget: list[int] | None = None) -> YAMLDict | None:
    """Serialize a Location to a JSON-safe dict for OPA.

    Args:
        loc: Location to serialize, or None.
        _budget: Shared remaining conversion work allowance.

    Returns:
        Dict with type, value, is_mutable, or None if loc is empty/None.
    """
    if loc is None or getattr(loc, "is_empty", False):
        return None
    location_type = getattr(loc, "type", "") or ""
    result = cast(
        YAMLDict,
        json_safe(
            {
                "type": location_type,
                "value": getattr(loc, "value", ""),
                "is_mutable": getattr(loc, "is_mutable", False),
            },
            _budget=_budget,
        ),
    )
    value = result["value"] or ""
    if location_type == "url" and isinstance(value, str):
        value = redact_urls_in_text(value)
    result["value"] = value
    return result


def annotation_to_dict(an: Annotation, *, _budget: list[int] | None = None) -> YAMLDict:
    """Serialize an Annotation (including RiskAnnotation detail) for OPA input.

    Args:
        an: Annotation to serialize.
        _budget: Shared remaining conversion work allowance.

    Returns:
        Dict with type, key, risk_type, and detail-specific fields.
    """
    d = cast(
        YAMLDict,
        json_safe({"type": getattr(an, "type", ""), "key": getattr(an, "key", "")}, _budget=_budget),
    )
    if not isinstance(an, RiskAnnotation):
        d["risk_type"] = ""
        return d

    d["risk_type"] = json_safe(getattr(an, "risk_type", "") or "", _budget=_budget)

    cmd = getattr(an, "command", None)
    if cmd is not None:
        d["command"] = json_safe(getattr(cmd, "raw", None), _budget=_budget) or ""
    exec_files = getattr(an, "exec_files", None)
    if exec_files:
        d["exec_files"] = [_location_to_dict(ef, _budget=_budget) for ef in exec_files if ef]

    src = getattr(an, "src", None)
    dest = getattr(an, "dest", None)
    if isinstance(src, Location):
        d["src"] = _location_to_dict(src, _budget=_budget)
    if isinstance(dest, Location):
        d["dest"] = _location_to_dict(dest, _budget=_budget)
    for flag in ("is_mutable_src", "is_mutable_dest"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    pkg = getattr(an, "pkg", None)
    if pkg is not None and pkg != "":
        d["pkg"] = json_safe(pkg, _budget=_budget)
    version = getattr(an, "version", None)
    if version is not None and version != "":
        d["version"] = json_safe(version, _budget=_budget)
    for flag in ("is_mutable_pkg", "disable_validate_certs", "allow_downgrade"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    path_loc = getattr(an, "path", None)
    if isinstance(path_loc, Location):
        d["path_loc"] = _location_to_dict(path_loc, _budget=_budget)
    for flag in ("is_mutable_path", "is_mutable_src", "is_unsafe_write", "is_deletion", "is_insecure_permissions"):
        val = getattr(an, flag, None)
        if val is not None:
            d[flag] = bool(val)

    config_key = getattr(an, "key", None)
    if config_key and d.get("key") != config_key:
        d["config_key"] = json_safe(config_key, _budget=_budget)
    if getattr(an, "is_mutable_key", None) is not None:
        d["is_mutable_key"] = bool(getattr(an, "is_mutable_key", False))

    return d


# ---------------------------------------------------------------------------
# Per-node serialization
# ---------------------------------------------------------------------------


def content_node_to_opa_dict(
    node: ContentNode,
    graph: ContentGraph | None = None,
    *,
    _module_defaults_contexts: dict[str, _ModuleDefaultsContexts] | None = None,
    _defaults_payload_budget: _DefaultsPayloadBudget | None = None,
) -> YAMLDict:
    """Serialize a ``ContentNode`` to the OPA node dict format.

    The output retains ``opa_payload.node_to_dict(RunTarget)`` fields and adds
    collection search context for the Ansible validator's plugin resolution.
    Task defaults are separate root-to-task layers for each distinct positional
    context; explicit arguments remain unchanged. Without a graph, only the
    node's local defaults can be serialized. Policy-visible fields are atomic:
    conversion failure aborts serialization instead of presenting omitted data
    as absent to OPA, raising ``OpaPayloadSerializationError``. Defaults-only
    coverage markers remain separate.

    Args:
        node: ContentNode to serialize.
        graph: Optional graph for structural queries (e.g. edge inspection).
        _module_defaults_contexts: Shared context cache for full hierarchy builds.
        _defaults_payload_budget: Shared defaults preflight sizes and hierarchy
            defaults/argument allowances.

    Returns:
        Dict with type, key, file, line, defined_in, and type-specific fields.

    """
    node_type = node.node_type
    opa_type = _OPA_TYPE_MAP.get(node_type, "")
    if not opa_type:
        return {}

    d: YAMLDict = {
        "type": opa_type,
        "key": node.node_id,
        "file": node.file_path,
        "line": [node.line_start, node.line_end] if node.line_start else None,
        "defined_in": node.file_path,
    }
    work_budget = _defaults_payload_budget.arguments if _defaults_payload_budget is not None else None

    def serialize(value: YAMLValue, field_name: str) -> YAMLValue:
        """Convert a policy field without silently weakening policy input.

        Args:
            value: Authored policy input.
            field_name: Field path used only to identify a failed assessment.

        Returns:
            Complete JSON-compatible field value.

        Raises:
            OpaPayloadSerializationError: Conversion failed or exhausted a limit.
        """
        try:
            return json_safe(value, _budget=work_budget)
        except Exception as exc:
            raise OpaPayloadSerializationError(
                f"OPA policy assessment incomplete: cannot serialize {field_name} on {node.node_id} "
                "within serialization limits"
            ) from exc

    if opa_type == "playcall":
        d["name"] = node.name if node.name else None
        opts: YAMLDict = {}
        if node.become:
            for bkey in ("become", "become_user", "become_method", "become_flags"):
                bval = node.become.get(bkey)
                if bval is not None:
                    opts[bkey] = serialize(bval, f"options.{bkey}")
        for pkey in _PLAY_OPTION_KEYS:
            pval = node.options.get(pkey)
            if pval is not None:
                opts[pkey] = serialize(pval, f"options.{pkey}")
        if node.variables:
            opts["vars"] = serialize(node.variables, "options.vars")
        d["options"] = opts

        if graph is not None:
            if graph.has_edge_from(node.node_id, EdgeType.DEPENDENCY):
                opts["roles"] = True
            for target_id, _attrs in graph.edges_from(node.node_id, EdgeType.CONTAINS):
                target = graph.get_node(target_id)
                if target is not None and target.node_type in _L066_TASK_TYPES:
                    opts["tasks"] = True
                    break

        if d["line"] is None and node.options:
            play_index = node.options.get("index", 0)
            if isinstance(play_index, int):
                d["line"] = [max(1, play_index + 1), max(1, play_index + 1)]

    elif opa_type == "taskcall":
        if graph is not None:
            contexts = _module_defaults_contexts
            if contexts is None:
                contexts = _build_module_defaults_contexts(graph)
            defaults = contexts.get(node.node_id, _ModuleDefaultsContexts(incomplete=True))
            if _defaults_payload_budget is not None and (
                defaults.incomplete or not _defaults_payload_budget.fits(defaults)
            ):
                d["module_defaults_contexts"] = []
                d["module_defaults_incomplete"] = True
            else:
                try:
                    d["module_defaults_contexts"] = json_safe([list(values) for values in defaults.paths.values()])
                except RecursionError:
                    d["module_defaults_contexts"] = []
                    d["module_defaults_incomplete"] = True
                if defaults.incomplete:
                    d["module_defaults_incomplete"] = True
                elif not defaults.paths:
                    d["module_defaults_inactive"] = True
        else:
            try:
                local_defaults = (
                    [json_safe(node.options["module_defaults"])] if "module_defaults" in node.options else []
                )
                d["module_defaults_contexts"] = [local_defaults]
            except RecursionError:
                d["module_defaults_contexts"] = []
                d["module_defaults_incomplete"] = True
        module_options = node.effective_module_options
        mo = cast(YAMLDict, serialize(module_options, "module_options")) if module_options else {}
        d["module"] = node.module
        d["original_module"] = node.module
        d["name"] = node.name if node.name else None
        task_args = node.options.get("args")
        if isinstance(task_args, str) and ("{{" in task_args or "{%" in task_args):
            d["args_templated"] = True

        anns: list[YAMLValue] = []
        has_inbound_annotation = False
        for an in node.annotations:
            if getattr(an, "type", "") == "risk_annotation" and getattr(an, "risk_type", "") == "inbound_transfer":
                has_inbound_annotation = True
            try:
                anns.append(annotation_to_dict(cast(Annotation, an), _budget=work_budget))
            except Exception as exc:
                raise OpaPayloadSerializationError(
                    f"OPA policy assessment incomplete: cannot serialize annotations on {node.node_id} "
                    "within serialization limits"
                ) from exc
        if not has_inbound_annotation:
            inbound_annotation = _inbound_risk_annotation(node, mo)
            if inbound_annotation is not None:
                anns.append(serialize(inbound_annotation, "annotations.inbound_transfer"))
        d["annotations"] = anns

        task_opts: YAMLDict = {}
        for key, val in node.options.items():
            if key in _TASK_OPTION_KEYS or key.startswith("with_"):
                task_opts[key] = serialize(val, f"options.{key}")
        d["options"] = task_opts

        if "_raw" in mo and "cmd" not in mo:
            raw_val = mo.get("_raw")
            if isinstance(raw_val, str):
                mo["cmd"] = raw_val
        if "_raw" in mo and "_raw_params" not in mo:
            mo["_raw_params"] = mo["_raw"]
        d["module_options"] = mo
        if node.collections_in_play:
            d["collections_in_play"] = list(node.collections_in_play)

    elif opa_type == "blockcall":
        d["name"] = node.name if node.name else None

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

    The hierarchy retains the established OPA payload shape, with additive
    task collection context for the Ansible validator. OPA Rego rules consume
    the payload without modification. Unserializable policy input raises
    ``OpaPayloadSerializationError`` instead of returning a partial hierarchy.

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
        scan_id = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")

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


def _limit_defaults_payload(payload: YAMLDict, remaining: list[int]) -> None:
    """Bound defaults across a hierarchy while preserving explicit coverage gaps.

    Args:
        payload: One serialized node, updated if its defaults exceed the budget.
        remaining: Mutable remaining byte budget shared by the hierarchy.
    """
    contexts = payload.get("module_defaults_contexts")
    if not isinstance(contexts, list):
        return
    if payload.get("module_defaults_incomplete"):
        payload["module_defaults_contexts"] = []
        return
    if all(context == [] for context in contexts):
        return
    size = len(json.dumps(contexts))
    if size > remaining[0]:
        payload["module_defaults_contexts"] = []
        payload["module_defaults_incomplete"] = True
    else:
        remaining[0] -= size


def content_nodes_to_opa_dicts(nodes: Iterable[ContentNode], graph: ContentGraph) -> list[YAMLDict]:
    """Serialize a node batch with one shared defaults-context calculation.

    Unserializable policy input raises ``OpaPayloadSerializationError`` instead
    of returning a partial batch.

    Args:
        nodes: Nodes selected for a full scan or incremental rescan.
        graph: Complete graph providing inherited invocation context.

    Returns:
        OPA-compatible dictionaries for the serializable nodes.

    """
    contexts = _build_module_defaults_contexts(graph)
    result: list[YAMLDict] = []
    remaining = [_HIERARCHY_DEFAULTS_BYTE_LIMIT]
    defaults_budget = _DefaultsPayloadBudget(remaining)
    for node in nodes:
        payload = content_node_to_opa_dict(
            node, graph=graph, _module_defaults_contexts=contexts, _defaults_payload_budget=defaults_budget
        )
        if payload:
            _limit_defaults_payload(payload, remaining)
            result.append(payload)
    return result


def _build_trees(graph: ContentGraph) -> list[YAMLDict]:
    """Build one tree dict per root (playbook or role without parents).

    Args:
        graph: ContentGraph to extract trees from.

    Returns:
        List of tree dicts, each with root_key, root_type, root_path, nodes.
    """
    trees: list[YAMLDict] = []
    module_defaults_contexts = _build_module_defaults_contexts(graph)
    remaining = [_HIERARCHY_DEFAULTS_BYTE_LIMIT]
    defaults_budget = _DefaultsPayloadBudget(remaining)

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
            d = content_node_to_opa_dict(
                content_node,
                graph=graph,
                _module_defaults_contexts=module_defaults_contexts,
                _defaults_payload_budget=defaults_budget,
            )
            if d:
                _limit_defaults_payload(d, remaining)
                nodes_list.append(d)

        root_key = root.node_id
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
                    if prefix not in {"ansible.builtin", "ansible.legacy"}:
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
    NodeType.BLOCK: "blockcall",
}
