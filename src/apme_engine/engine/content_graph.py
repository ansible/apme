"""ContentGraph — DAG-backed model for Ansible content (ADR-044).

Replaces the stateless ARI snapshot with a stable identity + relationship
graph.  Built on ``networkx.MultiDiGraph`` so that the same role included
from three playbooks exists once with three incoming edges, not three copies.

Public API
----------
- ``NodeIdentity`` — stable YAML-path-based ID for a content unit
- ``ContentNode``  — immutable snapshot of a node's content + metadata
- ``ContentGraph`` — top-level graph container with query helpers
- ``GraphBuilder`` — constructs a ``ContentGraph`` from ARI definitions
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, cast

import networkx as nx  # type: ignore[import-untyped]

from .models import YAMLDict, YAMLValue

if TYPE_CHECKING:
    from .models import (
        ObjectList,
        Play,
        Playbook,
        Role,
        RoleInPlay,
        Task,
        TaskFile,
    )

# ---------------------------------------------------------------------------
# Node and edge type enumerations
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    """Content unit types tracked in the graph.

    Attributes:
        PLAYBOOK: Top-level playbook file.
        PLAY: Play within a playbook.
        ROLE: Role definition or directory.
        TASKFILE: Task file (standalone or under a role).
        TASK: Executable task.
        HANDLER: Handler task.
        BLOCK: Block with nested tasks.
        MODULE: Module invocation metadata.
        MODULE_UTILS: Collection module_utils Python unit.
        FILTER_PLUGIN: Jinja filter plugin.
        ACTION_PLUGIN: Action plugin.
        LOOKUP_PLUGIN: Lookup plugin.
        VARS_FILE: Variables file node.
        COLLECTION: Collection metadata node.
    """

    PLAYBOOK = "playbook"
    PLAY = "play"
    ROLE = "role"
    TASKFILE = "taskfile"
    TASK = "task"
    HANDLER = "handler"
    BLOCK = "block"
    MODULE = "module"
    MODULE_UTILS = "module_utils"
    FILTER_PLUGIN = "filter_plugin"
    ACTION_PLUGIN = "action_plugin"
    LOOKUP_PLUGIN = "lookup_plugin"
    VARS_FILE = "vars_file"
    COLLECTION = "collection"


class EdgeType(str, Enum):
    """Relationship types between content nodes.

    Attributes:
        IMPORT: Static import (e.g. import_tasks, import_playbook).
        INCLUDE: Dynamic include.
        NOTIFY: Task notifies a handler by name.
        LISTEN: Handler listens for a notify topic.
        DEPENDENCY: Role or meta dependency.
        DATA_FLOW: Producer register/set_fact to consumer.
        RESCUE: Block rescue section edge.
        ALWAYS: Block always section edge.
        INVOKES: Caller invokes callee (e.g. module).
        PY_IMPORTS: Python import relationship.
        VARS_INCLUDE: Scope pulls in a vars file.
        CONTAINS: Parent structurally contains child.
    """

    IMPORT = "import"
    INCLUDE = "include"
    NOTIFY = "notify"
    LISTEN = "listen"
    DEPENDENCY = "dependency"
    DATA_FLOW = "data_flow"
    RESCUE = "rescue"
    ALWAYS = "always"
    INVOKES = "invokes"
    PY_IMPORTS = "py_imports"
    VARS_INCLUDE = "vars_include"
    CONTAINS = "contains"


class NodeScope(str, Enum):
    """Ownership scope for violations and remediation eligibility.

    Attributes:
        OWNED: Primary project content under the scan root.
        REFERENCED: External or transitive content loaded for context.
    """

    OWNED = "owned"
    REFERENCED = "referenced"


# ---------------------------------------------------------------------------
# NodeIdentity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    """Stable identifier derived from a node's structural position.

    Identity is assigned once at parse time and never changes, even as
    line numbers shift through formatting or remediation.  Two parses of
    identical content produce identical identities.

    The ``path`` is the YAML-path-style string that uniquely identifies
    the node within the project.  Examples::

        site.yml                                  # playbook
        site.yml/plays[0]                         # first play
        site.yml/plays[0]/tasks[2]                # third task in first play
        site.yml/plays[0]/handlers[0]             # first handler
        roles/webserver/tasks/main.yml            # taskfile
        roles/webserver/tasks/main.yml/tasks[1]   # second task in that file

    Attributes:
        path: YAML-path-style unique location string.
        node_type: Kind of content this identity refers to.
    """

    path: str
    node_type: NodeType

    def __str__(self) -> str:
        """Return the YAML-path string.

        Returns:
            This identity's ``path`` value.
        """
        return self.path

    @property
    def parent_path(self) -> str | None:
        """Return the parent's path, or None if this is a root node."""
        sep = self.path.rfind("/")
        if sep <= 0:
            return None
        return self.path[:sep]


# ---------------------------------------------------------------------------
# ContentNode
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContentNode:
    """Snapshot of a content unit's data at a point in time.

    Mutable only during graph construction; treat as read-only afterward.

    Attributes:
        identity: Stable node identity (path + type).
        file_path: Source file for this unit.
        line_start: Starting line in ``file_path`` (0 if unknown).
        line_end: Ending line in ``file_path`` (0 if unknown).
        name: Display name from YAML when present.
        module: Declared Ansible module name.
        resolved_module_name: Fully resolved module FQCN when known.
        module_options: Raw module arguments from YAML.
        resolved_module_options: Normalized module arguments when known.
        options: Task/play options (when, tags, etc.).
        variables: Inline vars dict for this scope.
        become: Become settings at this scope.
        when_expr: When condition string or list.
        tags: Declared tags.
        loop: Loop expression or iterable.
        loop_control: Loop control options dict.
        register: Variable name registered from task output.
        set_facts: Facts set by set_fact-style tasks.
        notify: Handler names notified by this task.
        listen: Listen topics for handlers.
        environment: Task environment dict.
        no_log: Whether no_log is set.
        ignore_errors: Whether errors are ignored.
        changed_when: changed_when expression.
        failed_when: failed_when expression.
        delegate_to: delegate_to target string.
        yaml_lines: Raw YAML source fragment for this node's span.
        role_fqcn: Role FQCN when this node is role-related.
        default_variables: Role defaults mapping.
        role_variables: Role vars mapping.
        role_metadata: Role meta/main.yml contents (galaxy_info, dependencies, etc.).
        collection_namespace: Declaring collection namespace.
        collection_name: Declaring collection name.
        ari_key: Legacy ARI object key for cross-checks.
        annotations: Annotator payloads (risk, module hints, etc.).
        scope: Owned vs referenced content classification.
    """

    identity: NodeIdentity

    # Source location
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0

    # Content extracted from YAML
    name: str | None = None
    module: str = ""
    resolved_module_name: str = ""
    module_options: YAMLDict = field(default_factory=dict)
    resolved_module_options: YAMLDict = field(default_factory=dict)
    options: YAMLDict = field(default_factory=dict)
    variables: YAMLDict = field(default_factory=dict)

    # Ansible-specific attributes
    become: YAMLDict | None = None
    when_expr: str | list[str] | None = None
    tags: list[str] = field(default_factory=list)
    loop: YAMLValue | None = None
    loop_control: YAMLDict | None = None
    register: str | None = None
    set_facts: YAMLDict = field(default_factory=dict)
    notify: list[str] = field(default_factory=list)
    listen: list[str] = field(default_factory=list)
    environment: YAMLDict | None = None
    no_log: bool | None = None
    ignore_errors: bool | None = None
    changed_when: YAMLValue | None = None
    failed_when: YAMLValue | None = None
    delegate_to: str | None = None

    # Raw YAML source
    yaml_lines: str = ""

    # Role metadata
    role_fqcn: str = ""
    default_variables: YAMLDict = field(default_factory=dict)
    role_variables: YAMLDict = field(default_factory=dict)
    role_metadata: YAMLDict = field(default_factory=dict)

    # Collection metadata
    collection_namespace: str = ""
    collection_name: str = ""

    # ARI cross-reference (populated during build for validation)
    ari_key: str = ""

    # Annotations from risk/module annotators
    annotations: list[object] = field(default_factory=list)

    # Scope
    scope: NodeScope = NodeScope.OWNED

    @property
    def node_type(self) -> NodeType:
        """Return the node's type from its identity."""
        return self.identity.node_type

    @property
    def node_id(self) -> str:
        """Return the node's stable string identifier."""
        return str(self.identity)


# ---------------------------------------------------------------------------
# ContentGraph
# ---------------------------------------------------------------------------


class ContentGraph:
    """DAG of identified Ansible content nodes and their relationships.

    Backed by a ``networkx.MultiDiGraph`` to support multiple typed edges
    between the same pair of nodes (e.g. a task can both ``import`` a
    taskfile and ``notify`` a handler defined in it).
    """

    def __init__(self) -> None:
        """Initialize an empty content graph."""
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._nodes_by_ari_key: dict[str, str] = {}

    # -- Serialization (ADR-044 Phase 2 switchover) -------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialize the graph to a JSON-compatible dict.

        Produces a deterministic representation suitable for transmission
        over gRPC as JSON bytes.  Nodes carry all serializable
        ``ContentNode`` fields (``annotations`` is excluded because its
        elements are not guaranteed to be JSON-safe).  Edges carry typed
        attributes and are sorted by ``(source, target)`` for stability.

        Returns:
            Dict with ``nodes`` and ``edges`` lists plus metadata.
        """
        nodes: list[dict[str, object]] = []
        for nid in sorted(self.g.nodes):
            node = self.get_node(nid)
            if node is not None:
                nodes.append({"id": nid, "data": _node_to_dict(node)})

        edges: list[dict[str, object]] = []
        for src, tgt, data in self.g.edges(data=True):
            edge: dict[str, object] = {
                "source": src,
                "target": tgt,
            }
            edge.update(data)
            edges.append(edge)
        edges.sort(key=lambda e: (str(e["source"]), str(e["target"])))

        return {
            "version": 1,
            "nodes": nodes,
            "edges": edges,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> ContentGraph:
        """Reconstruct a ContentGraph from a serialized dict.

        Args:
            d: Dict produced by ``to_dict()``.

        Returns:
            A new ``ContentGraph`` with identical topology and node data.

        Raises:
            ValueError: If the dict version is unsupported or the payload
                is malformed (missing keys, unexpected types).
        """
        try:
            version = d.get("version", 0)
            if version != 1:
                msg = f"Unsupported ContentGraph serialization version: {version}"
                raise ValueError(msg)

            graph = cls()
            for raw_node in cast(list[dict[str, object]], d["nodes"]):
                nid = str(raw_node["id"])
                node = _node_from_dict(cast(dict[str, object], raw_node["data"]))
                graph.g.add_node(nid, node=node)
                if node.ari_key:
                    graph._nodes_by_ari_key[node.ari_key] = nid

            for raw_edge in cast(list[dict[str, object]], d["edges"]):
                src = str(raw_edge["source"])
                tgt = str(raw_edge["target"])
                attrs = {k: v for k, v in raw_edge.items() if k not in ("source", "target")}
                graph.g.add_edge(src, tgt, **attrs)

        except ValueError:
            raise
        except (KeyError, TypeError, AttributeError) as exc:
            msg = f"Malformed ContentGraph payload: {exc}"
            raise ValueError(msg) from exc

        return graph

    # -- Node operations ----------------------------------------------------

    def add_node(self, node: ContentNode) -> None:
        """Add a node to the graph.

        Args:
            node: The content node to register (indexed by ``node_id`` and optional ``ari_key``).
        """
        nid = node.node_id
        self.g.add_node(nid, node=node)
        if node.ari_key:
            self._nodes_by_ari_key[node.ari_key] = nid

    def get_node(self, node_id: str) -> ContentNode | None:
        """Return the ContentNode for a given node_id, or None.

        Args:
            node_id: Stable graph identifier (same as ``ContentNode.node_id``).

        Returns:
            The attached ``ContentNode``, or ``None`` if missing.
        """
        data = self.g.nodes.get(node_id)
        if data is None:
            return None
        return cast(ContentNode, data.get("node"))

    def get_node_by_ari_key(self, ari_key: str) -> ContentNode | None:
        """Lookup by ARI key (for validation against old pipeline).

        Args:
            ari_key: ARI object key from the legacy scan pipeline.

        Returns:
            The matching ``ContentNode``, or ``None`` if not indexed.
        """
        nid = self._nodes_by_ari_key.get(ari_key)
        if nid is None:
            return None
        return self.get_node(nid)

    def nodes(self, node_type: NodeType | None = None) -> Iterator[ContentNode]:
        """Iterate over all nodes, optionally filtered by type.

        Args:
            node_type: If set, yield only nodes of this type; otherwise yield all.

        Yields:
            ContentNode: Payload for each graph vertex, optionally filtered by ``node_type``.
        """
        for _, data in self.g.nodes(data=True):
            node = cast(ContentNode, data.get("node"))
            if node is None:
                continue
            if node_type is not None and node.node_type != node_type:
                continue
            yield node

    def node_count(self) -> int:
        """Return the number of nodes in the graph.

        Returns:
            Vertex count of the backing ``MultiDiGraph``.
        """
        return int(self.g.number_of_nodes())

    # -- Edge operations ----------------------------------------------------

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        *,
        conditional: bool = False,
        dynamic: bool = False,
        position: int = 0,
        when_expr: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Add a typed, directed edge between two nodes.

        Args:
            source_id: Tail node id.
            target_id: Head node id.
            edge_type: Relationship kind (import, contains, etc.).
            conditional: Whether the edge is conditional (e.g. ``when``).
            dynamic: Whether the target was resolved from a templated path.
            position: Ordering among sibling edges from the same source.
            when_expr: Serialized ``when`` expression when relevant.
            tags: Tags copied onto the edge for diagnostics.
        """
        self.g.add_edge(
            source_id,
            target_id,
            edge_type=edge_type.value,
            conditional=conditional,
            dynamic=dynamic,
            position=position,
            when_expr=when_expr or "",
            tags=tags or [],
        )

    def edges_from(self, node_id: str, edge_type: EdgeType | None = None) -> list[tuple[str, dict[str, object]]]:
        """Return outgoing edges from a node as (target_id, attrs) pairs.

        Args:
            node_id: Source vertex id.
            edge_type: If set, filter to this edge type only.

        Returns:
            List of ``(target_id, attribute_dict)`` for outgoing edges.
        """
        result: list[tuple[str, dict[str, object]]] = []
        if node_id not in self.g:
            return result
        for _, target, data in self.g.out_edges(node_id, data=True):
            if edge_type is not None and data.get("edge_type") != edge_type.value:
                continue
            result.append((target, dict(data)))
        return result

    def edges_to(self, node_id: str, edge_type: EdgeType | None = None) -> list[tuple[str, dict[str, object]]]:
        """Return incoming edges to a node as (source_id, attrs) pairs.

        Args:
            node_id: Target vertex id.
            edge_type: If set, filter to this edge type only.

        Returns:
            List of ``(source_id, attribute_dict)`` for incoming edges.
        """
        result: list[tuple[str, dict[str, object]]] = []
        if node_id not in self.g:
            return result
        for source, _, data in self.g.in_edges(node_id, data=True):
            if edge_type is not None and data.get("edge_type") != edge_type.value:
                continue
            result.append((source, dict(data)))
        return result

    def edge_count(self) -> int:
        """Return the number of edges in the graph.

        Returns:
            Edge count of the backing ``MultiDiGraph``.
        """
        return int(self.g.number_of_edges())

    # -- Graph queries ------------------------------------------------------

    def ancestors(self, node_id: str) -> list[ContentNode]:
        """Return ancestor nodes (parent-first, root-last).

        Args:
            node_id: Node whose ``CONTAINS`` chain is walked upward.

        Returns:
            Ancestor ``ContentNode`` instances from immediate parent toward root.
        """
        result: list[ContentNode] = []
        visited: set[str] = set()
        current = node_id
        while True:
            parents = [
                src
                for src, _, data in self.g.in_edges(current, data=True)
                if data.get("edge_type") == EdgeType.CONTAINS.value
            ]
            if not parents:
                break
            parent_id = parents[0]
            if parent_id in visited:
                break
            visited.add(parent_id)
            parent_node = self.get_node(parent_id)
            if parent_node is not None:
                result.append(parent_node)
            current = parent_id
        return result

    def children(self, node_id: str) -> list[ContentNode]:
        """Return direct children (nodes with CONTAINS edge from this node).

        Args:
            node_id: Parent node id.

        Returns:
            Child nodes sorted by ``line_start``.
        """
        result: list[ContentNode] = []
        for target, _attrs in self.edges_from(node_id, EdgeType.CONTAINS):
            child = self.get_node(target)
            if child is not None:
                result.append(child)
        result.sort(key=lambda n: n.line_start)
        return result

    def descendants(self, node_id: str) -> set[str]:
        """Return all descendant node IDs (transitive children).

        Args:
            node_id: Root of the descendant subgraph.

        Returns:
            All reachable node ids, or an empty set if ``node_id`` is absent.
        """
        if node_id not in self.g:
            return set()
        return cast(set[str], nx.descendants(self.g, node_id))

    def subgraph(self, root_id: str) -> ContentGraph:
        """Return a new ContentGraph containing root_id and all descendants.

        Args:
            root_id: Subgraph root; includes this node and its descendants.

        Returns:
            A new ``ContentGraph`` whose backing graph is an induced subgraph copy.
        """
        sub = ContentGraph()
        ids = self.descendants(root_id) | {root_id}
        sub.g = self.g.subgraph(ids).copy()
        for nid in ids:
            node = self.get_node(nid)
            if node and node.ari_key:
                sub._nodes_by_ari_key[node.ari_key] = nid
        return sub

    def topological_order(self) -> list[str]:
        """Return node IDs in topological order (parents before children).

        Returns:
            Topologically sorted ids, or arbitrary node order if the graph has cycles.
        """
        try:
            return list(nx.topological_sort(self.g))
        except nx.NetworkXUnfeasible:
            return list(self.g.nodes)

    def is_acyclic(self) -> bool:
        """Return whether the graph is a directed acyclic graph.

        Returns:
            ``True`` if no directed cycles exist.
        """
        return bool(nx.is_directed_acyclic_graph(self.g))


# ---------------------------------------------------------------------------
# Node serialization helpers (ADR-044 Phase 2)
# ---------------------------------------------------------------------------

_CONTENT_NODE_SIMPLE_FIELDS: tuple[str, ...] = (
    "file_path",
    "line_start",
    "line_end",
    "name",
    "module",
    "resolved_module_name",
    "module_options",
    "resolved_module_options",
    "options",
    "variables",
    "become",
    "when_expr",
    "tags",
    "loop",
    "loop_control",
    "register",
    "set_facts",
    "notify",
    "listen",
    "environment",
    "no_log",
    "ignore_errors",
    "changed_when",
    "failed_when",
    "delegate_to",
    "yaml_lines",
    "role_fqcn",
    "default_variables",
    "role_variables",
    "role_metadata",
    "collection_namespace",
    "collection_name",
    "ari_key",
)


def _node_to_dict(node: ContentNode) -> dict[str, object]:
    """Serialize a ContentNode to a JSON-compatible dict.

    Args:
        node: ContentNode to serialize.

    Returns:
        Dict with identity, scope, and all content fields.
    """
    d: dict[str, object] = {
        "identity": {
            "path": node.identity.path,
            "node_type": node.identity.node_type.value,
        },
        "scope": node.scope.value,
    }
    for fname in _CONTENT_NODE_SIMPLE_FIELDS:
        d[fname] = getattr(node, fname)
    return d


def _node_from_dict(d: dict[str, object]) -> ContentNode:
    """Reconstruct a ContentNode from a serialized dict.

    Args:
        d: Dict produced by ``_node_to_dict``.

    Returns:
        Reconstructed ContentNode.
    """
    raw_identity = cast(dict[str, str], d["identity"])
    identity = NodeIdentity(
        path=raw_identity["path"],
        node_type=NodeType(raw_identity["node_type"]),
    )

    kwargs: dict[str, object] = {"identity": identity}
    kwargs["scope"] = NodeScope(cast(str, d.get("scope", "owned")))

    for fname in _CONTENT_NODE_SIMPLE_FIELDS:
        if fname in d:
            kwargs[fname] = d[fname]

    return ContentNode(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# GraphBuilder
# ---------------------------------------------------------------------------

_JINJA_VAR = re.compile(r"\{\{.*?\}\}")


def _has_template(value: str) -> bool:
    """Return True if the string contains Jinja2 template syntax.

    Args:
        value: String to inspect (e.g. task file path or argument).

    Returns:
        ``True`` if ``value`` contains ``{{`` (likely templating).
    """
    return "{{" in value


class GraphBuilder:
    """Constructs a ``ContentGraph`` from ARI definitions.

    Consumes the same ``root_definitions`` and ``ext_definitions`` dicts
    that ``TreeLoader`` uses, so it can run in parallel for validation.
    """

    def __init__(
        self,
        root_definitions: dict[str, object],
        ext_definitions: dict[str, object],
        *,
        scan_root: str = "",
    ) -> None:
        """Create a builder for graph construction from ARI definition maps.

        Args:
            root_definitions: Primary project definitions (same shape as ``TreeLoader``).
            ext_definitions: External/referenced definitions merged after roots.
            scan_root: Optional filesystem root for path normalization (reserved).
        """
        self._root_defs = root_definitions
        self._ext_defs = ext_definitions
        self._scan_root = scan_root
        self._graph = ContentGraph()
        self._visited: set[str] = set()
        self._object_by_key: dict[str, object] = {}

    def build(self) -> ContentGraph:
        """Build and return the ContentGraph.

        Builds a key-to-object lookup from all loaded definitions (mirroring
        ``TreeLoader``'s resolution), then processes playbooks, roles, and
        taskfiles.  String keys in child lists (``Playbook.plays``,
        ``Play.tasks``, ``TaskFile.tasks``, etc.) are resolved through this
        lookup.

        Returns:
            Fully wired ``ContentGraph`` instance.
        """
        from .models import ObjectList, Role, TaskFile
        from .tree import load_all_definitions

        root_loaded = load_all_definitions(self._root_defs)
        ext_loaded = load_all_definitions(self._ext_defs)

        # Build flat key → object lookup for string-key resolution.
        types = ["collections", "roles", "taskfiles", "modules", "playbooks", "plays", "tasks"]
        for type_key in types:
            for loaded in (root_loaded, ext_loaded):
                obj_list = loaded.get(type_key, ObjectList())
                if isinstance(obj_list, ObjectList):
                    for item in obj_list.items:
                        if hasattr(item, "key") and item.key:
                            self._object_by_key[item.key] = item

        # Register handler taskfiles (handlers are stored on Role objects
        # but excluded from the flat definitions dict by ARI).
        roles_list = root_loaded.get("roles", ObjectList())
        if isinstance(roles_list, ObjectList):
            for obj in roles_list.items:
                if not isinstance(obj, Role):
                    continue
                for h in obj.handlers:
                    if isinstance(h, TaskFile) and h.key:
                        self._object_by_key[h.key] = h
                        for task in h.tasks:
                            if hasattr(task, "key") and task.key:
                                self._object_by_key[task.key] = task

        self._build_from_loaded(root_loaded, NodeScope.OWNED)
        self._build_from_loaded(ext_loaded, NodeScope.REFERENCED)

        self._wire_notify_listen()
        self._wire_data_flow()

        return self._graph

    def _resolve_key(self, key: str, expected_type: type | None = None) -> object | None:
        """Resolve an ARI string key to the actual definition object.

        Args:
            key: ARI key string (e.g. ``play playbook:site.yml#play:[0]``).
            expected_type: If set, only return the object when it matches.

        Returns:
            The definition object, or ``None`` if not found or wrong type.
        """
        obj = self._object_by_key.get(key)
        if obj is None:
            return None
        if expected_type is not None and not isinstance(obj, expected_type):
            return None
        return obj

    def _build_from_loaded(
        self,
        loaded: dict[str, ObjectList],
        scope: NodeScope,
    ) -> None:
        """Process loaded definitions (playbooks, roles, taskfiles).

        Args:
            loaded: Output of ``load_all_definitions`` (playbooks, roles, taskfiles lists).
            scope: Whether nodes are owned project content or referenced externals.
        """
        from .models import ObjectList, Playbook, Role, TaskFile

        playbooks = loaded.get("playbooks", ObjectList())
        if isinstance(playbooks, ObjectList):
            for item in playbooks.items:
                if isinstance(item, Playbook):
                    self._build_playbook(item, scope)

        roles = loaded.get("roles", ObjectList())
        if isinstance(roles, ObjectList):
            for item in roles.items:
                if isinstance(item, Role):
                    self._build_role(item, scope)

        taskfiles = loaded.get("taskfiles", ObjectList())
        if isinstance(taskfiles, ObjectList):
            for item in taskfiles.items:
                if isinstance(item, TaskFile):
                    self._build_taskfile(item, scope=scope)

    # -- Playbook -----------------------------------------------------------

    def _build_playbook(self, pb: Playbook, scope: NodeScope) -> str:
        """Build graph nodes for a playbook and its plays.

        Args:
            pb: Parsed playbook ARI object.
            scope: Ownership scope for created nodes.

        Returns:
            Playbook node id (its path identity).
        """
        from .models import Play

        file_path = getattr(pb, "defined_in", "") or ""
        identity = NodeIdentity(path=file_path, node_type=NodeType.PLAYBOOK)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            name=getattr(pb, "name", "") or os.path.basename(file_path),
            variables=_safe_dict(getattr(pb, "variables", {})),
            options=_safe_dict(getattr(pb, "options", {})),
            ari_key=pb.key,
            scope=scope,
        )
        self._graph.add_node(node)

        for i, play_or_key in enumerate(pb.plays):
            play: Play | None = None
            if isinstance(play_or_key, Play):
                play = play_or_key
            elif isinstance(play_or_key, str):
                resolved = self._resolve_key(play_or_key, Play)
                play = cast("Play", resolved) if resolved else None
            if play is None:
                continue

            if play.import_playbook:
                imported_nid = self._handle_import_playbook(play, nid, file_path, i, scope)
                if imported_nid:
                    continue

            play_nid = self._build_play(play, nid, file_path, i, scope)
            self._graph.add_edge(nid, play_nid, EdgeType.CONTAINS, position=i)

        return nid

    def _handle_import_playbook(
        self, play: Play, parent_nid: str, parent_file: str, position: int, scope: NodeScope
    ) -> str | None:
        """Handle import_playbook directive — creates an import edge.

        Args:
            play: Play declaring ``import_playbook``.
            parent_nid: Containing playbook node id.
            parent_file: Filesystem path of the parent playbook.
            position: Index among parent's children for edge ordering.
            scope: Ownership scope for a stub imported playbook node if created.

        Returns:
            Target playbook node id, or ``None`` if no import path.
        """
        import_path = play.import_playbook
        if not import_path:
            return None
        parent_dir = os.path.dirname(parent_file)
        resolved_path = os.path.normpath(os.path.join(parent_dir, import_path))
        target_nid = resolved_path
        if target_nid not in self._graph.g:
            target_identity = NodeIdentity(path=resolved_path, node_type=NodeType.PLAYBOOK)
            target_node = ContentNode(
                identity=target_identity,
                file_path=resolved_path,
                name=os.path.basename(resolved_path),
                scope=scope,
            )
            self._graph.add_node(target_node)
        self._graph.add_edge(parent_nid, target_nid, EdgeType.IMPORT, position=position)
        return target_nid

    # -- Play ---------------------------------------------------------------

    def _build_play(self, play: Play, playbook_nid: str, file_path: str, play_index: int, scope: NodeScope) -> str:
        """Build graph nodes for a play and its children.

        Args:
            play: Parsed play ARI object.
            playbook_nid: Parent playbook node id.
            file_path: Playbook file path on disk.
            play_index: Zero-based index in ``pb.plays``.
            scope: Ownership scope for created nodes.

        Returns:
            Play node id (YAML-path identity under the playbook).
        """
        from .models import RoleInPlay, Task

        play_path = f"{file_path}/plays[{play_index}]"
        identity = NodeIdentity(path=play_path, node_type=NodeType.PLAY)
        nid = identity.path

        line_start, line_end = _extract_lines(play)
        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(play, "name", None),
            variables=_safe_dict(getattr(play, "variables", {})),
            options=_safe_dict(getattr(play, "options", {})),
            become=_extract_become(play),
            ari_key=play.key,
            scope=scope,
        )
        self._graph.add_node(node)

        position = 0

        # vars_files
        for vf in getattr(play, "vars_files", []) or []:
            if isinstance(vf, str):
                vf_path = os.path.normpath(os.path.join(os.path.dirname(file_path), vf))
                vf_nid = self._ensure_vars_file(vf_path, scope)
                self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
                position += 1

        # static roles
        for rip_or_key in getattr(play, "roles", []) or []:
            if isinstance(rip_or_key, RoleInPlay):
                role_nid = self._resolve_role_nid(rip_or_key)
                if role_nid:
                    self._graph.add_edge(nid, role_nid, EdgeType.DEPENDENCY, position=position)
                    position += 1

        # pre_tasks, tasks, post_tasks
        for task_list_attr in ("pre_tasks", "tasks", "post_tasks"):
            task_list = getattr(play, task_list_attr, []) or []
            for task_or_key in task_list:
                task_obj: Task | None = None
                if isinstance(task_or_key, Task):
                    task_obj = task_or_key
                elif isinstance(task_or_key, str):
                    resolved = self._resolve_key(task_or_key, Task)
                    task_obj = cast("Task", resolved) if resolved else None
                if task_obj is not None:
                    task_nid = self._build_task(task_obj, nid, file_path, play_index, position, scope)
                    self._graph.add_edge(nid, task_nid, EdgeType.CONTAINS, position=position)
                    position += 1

        # handlers
        handler_list = getattr(play, "handlers", []) or []
        for h_idx, handler_or_key in enumerate(handler_list):
            handler_obj: Task | None = None
            if isinstance(handler_or_key, Task):
                handler_obj = handler_or_key
            elif isinstance(handler_or_key, str):
                resolved = self._resolve_key(handler_or_key, Task)
                handler_obj = cast("Task", resolved) if resolved else None
            if handler_obj is not None:
                h_nid = self._build_handler(handler_obj, nid, file_path, play_index, h_idx, scope)
                self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                position += 1

        return nid

    # -- Task ---------------------------------------------------------------

    def _build_task(
        self,
        task: Task,
        parent_nid: str,
        file_path: str,
        play_index: int,
        position: int,
        scope: NodeScope,
        *,
        path_prefix: str = "",
    ) -> str:
        """Build a task node and wire executable edges.

        Args:
            task: Parsed task ARI object.
            parent_nid: Immediate parent node id (play, block, or taskfile).
            file_path: Source file path for location metadata.
            play_index: Play index when under a play (used for line context).
            position: Sibling index for default path when ``path_prefix`` is empty.
            scope: Ownership scope for the new node.
            path_prefix: Override identity path (for nested block children).

        Returns:
            New task or block node id.
        """
        from .models import ExecutableType

        if not path_prefix:
            path_prefix = f"{parent_nid}/tasks[{position}]"

        is_block = bool(getattr(task, "module", "") == "" and _has_block_children(task))
        node_type = NodeType.BLOCK if is_block else NodeType.TASK
        identity = NodeIdentity(path=path_prefix, node_type=node_type)
        nid = identity.path

        line_start, line_end = _extract_lines(task)
        options = _safe_dict(getattr(task, "options", {}))
        module_options = _safe_dict(getattr(task, "module_options", {}))

        when_raw = options.get("when")
        when_expr: str | list[str] | None
        if isinstance(when_raw, str):
            when_expr = when_raw
        elif isinstance(when_raw, list):
            when_expr = [str(x) for x in when_raw]
        else:
            when_expr = None

        loop_control_raw = options.get("loop_control")
        loop_control: YAMLDict | None = loop_control_raw if isinstance(loop_control_raw, dict) else None

        register_raw = options.get("register")
        register = register_raw if isinstance(register_raw, str) else None

        environment_raw = options.get("environment")
        environment: YAMLDict | None = environment_raw if isinstance(environment_raw, dict) else None

        no_log_raw = options.get("no_log")
        no_log = no_log_raw if isinstance(no_log_raw, bool) else None

        ignore_errors_raw = options.get("ignore_errors")
        ignore_errors = ignore_errors_raw if isinstance(ignore_errors_raw, bool) else None

        delegate_raw = options.get("delegate_to")
        delegate_to = delegate_raw if isinstance(delegate_raw, str) else None

        exec_type = getattr(task, "executable_type", None)
        resolved_module = ""
        if exec_type == ExecutableType.MODULE_TYPE:
            resolved_module = getattr(task, "resolved_name", "") or ""

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(task, "name", None),
            module=getattr(task, "module", "") or "",
            resolved_module_name=resolved_module,
            module_options=module_options,
            options=options,
            variables=_safe_dict(getattr(task, "variables", {})),
            become=_extract_become(task),
            when_expr=when_expr,
            tags=_as_str_list(options.get("tags")),
            loop=options.get("loop") or options.get("with_items"),
            loop_control=loop_control,
            register=register,
            set_facts=_safe_dict(getattr(task, "set_facts", {})),
            notify=_as_str_list(options.get("notify")),
            environment=environment,
            no_log=no_log,
            ignore_errors=ignore_errors,
            changed_when=options.get("changed_when"),
            failed_when=options.get("failed_when"),
            yaml_lines=getattr(task, "yaml_lines", "") or "",
            delegate_to=delegate_to,
            ari_key=task.key,
            scope=scope,
        )
        self._graph.add_node(node)

        # Block children (rescue, always, block tasks)
        if is_block:
            self._wire_block_children(task, nid, file_path, play_index, scope)

        # Executable edges (import_tasks, include_tasks, import_role, include_role, module)
        executable = getattr(task, "executable", "") or ""
        if executable and exec_type:
            is_dynamic = _has_template(executable)
            if exec_type == ExecutableType.TASKFILE_TYPE:
                is_import = getattr(task, "module", "") in ("ansible.builtin.import_tasks", "import_tasks")
                edge_type = EdgeType.IMPORT if is_import else EdgeType.INCLUDE
                resolved = self._resolve_taskfile_path(executable, file_path)
                if resolved:
                    if resolved not in self._graph.g:
                        self._ensure_taskfile_node(resolved, scope)
                    self._graph.add_edge(
                        nid,
                        resolved,
                        edge_type,
                        dynamic=is_dynamic,
                        conditional=node.when_expr is not None,
                        when_expr=str(node.when_expr) if node.when_expr else None,
                    )
            elif exec_type == ExecutableType.ROLE_TYPE:
                is_import = getattr(task, "module", "") in ("ansible.builtin.import_role", "import_role")
                edge_type = EdgeType.IMPORT if is_import else EdgeType.INCLUDE
                role_nid = self._resolve_role_nid_by_name(executable)
                if role_nid:
                    self._graph.add_edge(
                        nid,
                        role_nid,
                        edge_type,
                        dynamic=is_dynamic,
                        conditional=node.when_expr is not None,
                    )

        return nid

    def _build_handler(
        self,
        task: Task,
        parent_nid: str,
        file_path: str,
        play_index: int,
        handler_index: int,
        scope: NodeScope,
    ) -> str:
        """Build a handler node.

        Args:
            task: Parsed handler task ARI object.
            parent_nid: Containing play or role node id.
            file_path: Source file path.
            play_index: Play index when the parent is a play.
            handler_index: Index in the play's ``handlers`` list.
            scope: Ownership scope for the handler node.

        Returns:
            Handler node id.
        """
        path_prefix = f"{parent_nid}/handlers[{handler_index}]"
        identity = NodeIdentity(path=path_prefix, node_type=NodeType.HANDLER)
        nid = identity.path

        from .models import ExecutableType as _ET

        line_start, line_end = _extract_lines(task)
        options = _safe_dict(getattr(task, "options", {}))

        resolved_module = ""
        if getattr(task, "executable_type", None) == _ET.MODULE_TYPE:
            resolved_module = getattr(task, "resolved_name", "") or ""

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(task, "name", None),
            module=getattr(task, "module", "") or "",
            resolved_module_name=resolved_module,
            module_options=_safe_dict(getattr(task, "module_options", {})),
            options=options,
            notify=_as_str_list(options.get("notify")),
            listen=_as_str_list(options.get("listen")),
            yaml_lines=getattr(task, "yaml_lines", "") or "",
            ari_key=task.key,
            scope=scope,
        )
        self._graph.add_node(node)
        return nid

    # -- Block children (rescue / always) -----------------------------------

    def _wire_block_children(
        self,
        task: Task,
        block_nid: str,
        file_path: str,
        play_index: int,
        scope: NodeScope,
    ) -> None:
        """Wire block → rescue and block → always edges.

        Args:
            task: Block task whose ``options`` hold nested task lists.
            block_nid: Node id of the block.
            file_path: Source file path for nested task construction.
            play_index: Play index for nested task construction.
            scope: Ownership scope for child tasks.
        """
        from .models import Task as TaskModel

        options = _safe_dict(getattr(task, "options", {}))

        for section, edge_type in [("rescue", EdgeType.RESCUE), ("always", EdgeType.ALWAYS)]:
            section_tasks = options.get(section)
            if not isinstance(section_tasks, list):
                continue
            for i, child_or_key in enumerate(section_tasks):
                child_task: TaskModel | None = None
                if isinstance(child_or_key, TaskModel):
                    child_task = child_or_key
                elif isinstance(child_or_key, str):
                    resolved = self._resolve_key(child_or_key, TaskModel)
                    child_task = cast(TaskModel, resolved) if resolved else None
                if child_task is not None:
                    child_path = f"{block_nid}/{section}[{i}]"
                    child_nid = self._build_task(
                        child_task, block_nid, file_path, play_index, i, scope, path_prefix=child_path
                    )
                    self._graph.add_edge(block_nid, child_nid, EdgeType.CONTAINS, position=i)
                    self._graph.add_edge(block_nid, child_nid, edge_type, position=i)

        block_tasks = options.get("block")
        if isinstance(block_tasks, list):
            from .models import Task as TaskModel

            for i, child_or_key in enumerate(block_tasks):
                child_task_b: TaskModel | None = None
                if isinstance(child_or_key, TaskModel):
                    child_task_b = child_or_key
                elif isinstance(child_or_key, str):
                    resolved = self._resolve_key(child_or_key, TaskModel)
                    child_task_b = cast(TaskModel, resolved) if resolved else None
                if child_task_b is not None:
                    child_path = f"{block_nid}/block[{i}]"
                    child_nid = self._build_task(
                        child_task_b, block_nid, file_path, play_index, i, scope, path_prefix=child_path
                    )
                    self._graph.add_edge(block_nid, child_nid, EdgeType.CONTAINS, position=i)

    # -- Role ---------------------------------------------------------------

    def _build_role(self, role: Role, scope: NodeScope) -> str:
        """Build graph nodes for a role.

        Args:
            role: Parsed role ARI object.
            scope: Ownership scope for role and child nodes.

        Returns:
            Role node id (role path / ``defined_in`` identity).
        """
        from .models import Task, TaskFile

        role_fqcn = getattr(role, "fqcn", "") or getattr(role, "name", "") or ""
        defined_in = getattr(role, "defined_in", "") or ""
        role_path = defined_in or f"roles/{role_fqcn}"
        identity = NodeIdentity(path=role_path, node_type=NodeType.ROLE)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        raw_metadata = getattr(role, "metadata", None)
        role_metadata = _safe_dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

        node = ContentNode(
            identity=identity,
            file_path=defined_in,
            name=role_fqcn,
            role_fqcn=role_fqcn,
            default_variables=_safe_dict(getattr(role, "default_variables", {})),
            role_variables=_safe_dict(getattr(role, "variables", {})),
            role_metadata=role_metadata,
            ari_key=role.key,
            scope=scope,
        )
        self._graph.add_node(node)

        # Taskfiles in this role
        position = 0
        for tf_or_key in getattr(role, "taskfiles", []) or []:
            tf_obj: TaskFile | None = None
            if isinstance(tf_or_key, TaskFile):
                tf_obj = tf_or_key
            elif isinstance(tf_or_key, str):
                resolved = self._resolve_key(tf_or_key, TaskFile)
                tf_obj = cast("TaskFile", resolved) if resolved else None
            if tf_obj is not None:
                tf_nid = self._build_taskfile(tf_obj, parent_nid=nid, scope=scope)
                if tf_nid:
                    self._graph.add_edge(nid, tf_nid, EdgeType.CONTAINS, position=position)
                    position += 1

        # Handlers
        for h_idx, handler_or_key in enumerate(getattr(role, "handlers", []) or []):
            handler: TaskFile | Task | None = None
            if isinstance(handler_or_key, TaskFile | Task):
                handler = handler_or_key
            elif isinstance(handler_or_key, str):
                resolved = self._resolve_key(handler_or_key)
                handler = resolved if isinstance(resolved, TaskFile | Task) else None
            if isinstance(handler, TaskFile):
                h_nid = self._build_taskfile(handler, parent_nid=nid, scope=scope, is_handler_file=True)
                if h_nid:
                    self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                    position += 1
            elif isinstance(handler, Task):
                h_nid = self._build_handler(handler, nid, defined_in, 0, h_idx, scope)
                self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                position += 1

        # Role defaults and vars as vars_file nodes
        if node.default_variables:
            defaults_path = os.path.join(role_path, "defaults/main.yml")
            vf_nid = self._ensure_vars_file(defaults_path, scope, node.default_variables)
            self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
            position += 1

        if node.role_variables:
            vars_path = os.path.join(role_path, "vars/main.yml")
            vf_nid = self._ensure_vars_file(vars_path, scope, node.role_variables)
            self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
            position += 1

        # Role dependencies
        for dep in getattr(role, "dependency", []) or []:
            if isinstance(dep, dict):
                dep_name = dep.get("role", "") or dep.get("name", "")
                if isinstance(dep_name, str) and dep_name:
                    dep_nid = self._resolve_role_nid_by_name(dep_name)
                    if dep_nid:
                        self._graph.add_edge(nid, dep_nid, EdgeType.DEPENDENCY)

        return nid

    # -- TaskFile -----------------------------------------------------------

    def _build_taskfile(
        self,
        tf: TaskFile,
        *,
        parent_nid: str = "",
        scope: NodeScope = NodeScope.OWNED,
        is_handler_file: bool = False,
    ) -> str:
        """Build graph nodes for a taskfile and its tasks.

        Args:
            tf: Parsed task file ARI object.
            parent_nid: Optional parent role/play node for containment edges from caller.
            scope: Ownership scope for the taskfile and tasks.
            is_handler_file: If True, children are built as handlers not play tasks.

        Returns:
            Taskfile node id (``defined_in`` path).
        """
        from .models import Task

        defined_in = getattr(tf, "defined_in", "") or ""
        identity = NodeIdentity(path=defined_in, node_type=NodeType.TASKFILE)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        node = ContentNode(
            identity=identity,
            file_path=defined_in,
            name=os.path.basename(defined_in) if defined_in else "",
            variables=_safe_dict(getattr(tf, "variables", {})),
            ari_key=tf.key,
            scope=scope,
        )
        self._graph.add_node(node)

        for i, task_or_key in enumerate(getattr(tf, "tasks", []) or []):
            task_obj: Task | None = None
            if isinstance(task_or_key, Task):
                task_obj = task_or_key
            elif isinstance(task_or_key, str):
                resolved = self._resolve_key(task_or_key, Task)
                task_obj = cast("Task", resolved) if resolved else None
            if task_obj is not None:
                if is_handler_file:
                    child_nid = self._build_handler(task_obj, nid, defined_in, 0, i, scope)
                else:
                    child_path = f"{nid}/tasks[{i}]"
                    child_nid = self._build_task(task_obj, nid, defined_in, 0, i, scope, path_prefix=child_path)
                self._graph.add_edge(nid, child_nid, EdgeType.CONTAINS, position=i)

        return nid

    # -- Helpers ------------------------------------------------------------

    def _ensure_vars_file(self, path: str, scope: NodeScope, variables: YAMLDict | None = None) -> str:
        """Get or create a vars_file node.

        Args:
            path: Normalized path used as node identity.
            scope: Ownership scope for a newly created node.
            variables: Optional variable snapshot stored on the node.

        Returns:
            Vars-file node id (same as ``path``).
        """
        nid = path
        if nid not in self._graph.g:
            identity = NodeIdentity(path=path, node_type=NodeType.VARS_FILE)
            node = ContentNode(
                identity=identity,
                file_path=path,
                name=os.path.basename(path),
                variables=variables or {},
                scope=scope,
            )
            self._graph.add_node(node)
        return nid

    def _ensure_taskfile_node(self, path: str, scope: NodeScope) -> str:
        """Create a minimal taskfile node if not already present.

        Args:
            path: Taskfile path used as node identity.
            scope: Ownership scope for a newly created stub node.

        Returns:
            Taskfile node id (same as ``path``).
        """
        nid = path
        if nid not in self._graph.g:
            identity = NodeIdentity(path=path, node_type=NodeType.TASKFILE)
            node = ContentNode(
                identity=identity,
                file_path=path,
                name=os.path.basename(path),
                scope=scope,
            )
            self._graph.add_node(node)
        return nid

    def _resolve_taskfile_path(self, reference: str, from_file: str) -> str:
        """Resolve a relative taskfile reference to a normalized path.

        Args:
            reference: Path as written in the task (may be relative).
            from_file: YAML file containing the reference.

        Returns:
            ``os.path.normpath`` of ``reference`` resolved from ``from_file``'s directory.
        """
        parent_dir = os.path.dirname(from_file)
        resolved = os.path.normpath(os.path.join(parent_dir, reference))
        return resolved

    def _resolve_role_nid(self, rip: RoleInPlay) -> str | None:
        """Resolve a RoleInPlay to a role node ID.

        Args:
            rip: Role-in-play declaration from a play's ``roles`` list.

        Returns:
            Matching role node id if already present in the graph, else ``None``.
        """
        name = getattr(rip, "name", "") or ""
        return self._resolve_role_nid_by_name(name)

    def _resolve_role_nid_by_name(self, name: str) -> str | None:
        """Resolve a role name to an existing role node ID.

        Args:
            name: Role name or FQCN as referenced from YAML.

        Returns:
            Role node id if a role node matches by FQCN, name, or short name.
        """
        if not name:
            return None
        for node in self._graph.nodes(NodeType.ROLE):
            if node.role_fqcn == name or node.name == name:
                return node.node_id
            basename = node.role_fqcn.rsplit(".", 1)[-1] if "." in node.role_fqcn else node.role_fqcn
            if basename == name:
                return node.node_id
        return None

    def _wire_notify_listen(self) -> None:
        """Create notify edges from tasks/handlers to handler nodes.

        Scans all handler nodes for names and ``listen`` topics, then links
        tasks and handlers that reference those names via ``notify``.
        """
        handlers_by_name: dict[str, list[str]] = {}
        handlers_by_listen: dict[str, list[str]] = {}

        for node in self._graph.nodes():
            if node.node_type == NodeType.HANDLER:
                if node.name:
                    handlers_by_name.setdefault(node.name, []).append(node.node_id)
                for topic in node.listen:
                    handlers_by_listen.setdefault(topic, []).append(node.node_id)

        for node in self._graph.nodes():
            if node.node_type not in (NodeType.TASK, NodeType.HANDLER):
                continue
            for handler_name in node.notify:
                targets = handlers_by_name.get(handler_name, [])
                for target_id in targets:
                    self._graph.add_edge(node.node_id, target_id, EdgeType.NOTIFY)
                listen_targets = handlers_by_listen.get(handler_name, [])
                for target_id in listen_targets:
                    self._graph.add_edge(node.node_id, target_id, EdgeType.LISTEN)

    def _wire_data_flow(self) -> None:
        """Create data_flow edges for register → consumers.

        Uses topological order to map ``register`` and ``set_facts`` producers
        to later tasks that reference those names in ``when`` or module args.
        """
        registered: dict[str, str] = {}
        set_fact_producers: dict[str, str] = {}

        for nid in self._graph.topological_order():
            node = self._graph.get_node(nid)
            if node is None:
                continue
            if node.register:
                registered[node.register] = nid
            for fact_name in node.set_facts:
                set_fact_producers[fact_name] = nid

        for nid in self._graph.topological_order():
            node = self._graph.get_node(nid)
            if node is None:
                continue
            referenced_vars = _extract_variable_references(node)
            for var_name in referenced_vars:
                producer = registered.get(var_name) or set_fact_producers.get(var_name)
                if producer and producer != nid:
                    self._graph.add_edge(producer, nid, EdgeType.DATA_FLOW)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _safe_dict(v: object) -> YAMLDict:
    """Return ``v`` if it is a dict, otherwise an empty dict.

    Args:
        v: Arbitrary value from ARI/YAML parsing.

    Returns:
        ``v`` when it is a ``dict``, else ``{}``.
    """
    return cast(YAMLDict, v) if isinstance(v, dict) else {}


def _extract_lines(obj: object) -> tuple[int, int]:
    """Extract start and end line numbers from an ARI object.

    Args:
        obj: Model instance that may expose ``line_num_in_file``.

    Returns:
        ``(start, end)`` line numbers, or ``(0, 0)`` if unavailable.
    """
    line_num = getattr(obj, "line_num_in_file", None)
    if isinstance(line_num, list | tuple) and len(line_num) >= 2:
        return int(line_num[0]), int(line_num[1])
    return 0, 0


def _extract_become(obj: object) -> YAMLDict | None:
    """Extract become info as a dict.

    Args:
        obj: Model instance that may expose ``become`` (dict or object).

    Returns:
        Normalized become mapping, or ``None`` if unset or not convertible.
    """
    become = getattr(obj, "become", None)
    if become is None:
        return None
    if isinstance(become, dict):
        return cast(YAMLDict, become)
    if hasattr(become, "__dict__"):
        return cast(
            YAMLDict,
            {k: v for k, v in become.__dict__.items() if not k.startswith("_")},
        )
    return None


def _as_str_list(v: object) -> list[str]:
    """Coerce a value to a list of strings.

    Args:
        v: Scalar, list, or ``None`` (e.g. YAML ``tags`` / ``notify``).

    Returns:
        List of string values; empty list for ``None``.
    """
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _has_block_children(task: object) -> bool:
    """Check if a task object has block/rescue/always children.

    Args:
        task: Task model whose ``options`` may list nested tasks.

    Returns:
        ``True`` if any of ``block``, ``rescue``, or ``always`` is a non-empty list.
    """
    options = _safe_dict(getattr(task, "options", {}))
    return any(isinstance(options.get(k), list) for k in ("block", "rescue", "always"))


def _extract_variable_references(node: ContentNode) -> set[str]:
    """Extract Jinja2 variable names from a node's content.

    Args:
        node: Task-like node with ``when_expr`` and ``module_options`` strings.

    Returns:
        Set of simple identifier names referenced in Jinja (best-effort).
    """
    refs: set[str] = set()
    texts: list[str] = []

    if node.when_expr:
        if isinstance(node.when_expr, list):
            texts.extend(str(w) for w in node.when_expr)
        else:
            texts.append(str(node.when_expr))

    for v in node.module_options.values():
        if isinstance(v, str):
            texts.append(v)

    for match in _JINJA_VAR.findall(" ".join(texts)):
        cleaned = match.strip("{} ").split("|")[0].split(".")[0].strip()
        if cleaned and cleaned.isidentifier():
            refs.add(cleaned)

    return refs
