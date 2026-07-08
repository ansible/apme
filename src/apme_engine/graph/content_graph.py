"""ContentGraph — DAG-backed model for Ansible content (ADR-044, ADR-059).

Shared graph data structure used by both the engine (Primary) and the
native validator.  This module has **no** dependency on
``apme_engine.engine`` — the dependency arrow points from engine → graph.

Public API
----------
- ``NodeIdentity`` — stable YAML-path-based ID for a content unit
- ``ContentNode``  — immutable snapshot of a node's content + metadata
- ``ContentGraph`` — top-level graph container with query helpers
"""

from __future__ import annotations

import difflib
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, cast

import networkx as nx  # type: ignore[import-untyped]

from apme_engine.graph.types import RemediationResolution, ViolationDict, YAMLDict, YAMLValue

if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap as _CommentedMap

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
        RULEBOOK: EDA rulebook file.
        RULESET: Ruleset within an EDA rulebook (analogous to PLAY).
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
    RULEBOOK = "rulebook"
    RULESET = "ruleset"


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
# NodeState — immutable snapshot at a pipeline phase (ADR-044 Phase 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeState:
    """Immutable snapshot of a node's YAML content at a pipeline phase.

    Recorded at scan and transform boundaries during the convergence loop.
    Each ``ContentNode`` accumulates an ordered ``progression`` of these
    snapshots, enabling snippet accuracy and remediation attribution.

    Violation tracking is **not** stored here — it lives in the
    ``ContentNode.violation_ledger`` (see ``ViolationRecord``).

    Attributes:
        id: Unique identifier for this progression entry
            (``"{node_id}@{seq}"`` where *seq* is the monotonic
            progression index).  Used as ``Proposal.id`` in the
            UI and referenced back in ``ApprovalRequest``.
        pass_number: Convergence pass (0 = initial scan).
        phase: Pipeline phase (``"original"``, ``"scanned"``,
            ``"transformed"``, ``"ai_transformed"``).
        yaml_lines: Raw YAML text for this node at this point in time.
        content_hash: SHA-256 hex digest of ``yaml_lines``.
        timestamp: ISO 8601 UTC timestamp when the snapshot was taken.
        approved: Whether this entry has been approved.  All entries
            start pending (``False``).  Deterministic transforms are
            auto-approved via ``ContentGraph.approve_pending()`` after
            convergence; AI transforms await human approval.
        source: UI metadata indicating how the transform was produced
            (e.g. ``"deterministic"``, ``"ai"``).  The graph never
            inspects this field for logic.
    """

    id: str
    pass_number: int
    phase: str
    yaml_lines: str
    content_hash: str
    timestamp: str
    approved: bool = False
    source: str = ""


# ---------------------------------------------------------------------------
# ViolationRecord — mutable violation lifecycle (ADR-044 violation ledger)
# ---------------------------------------------------------------------------

ViolationKey = tuple[str, str]
"""Identity key for a violation: ``(node_id, normalized_rule_id)``."""


@dataclass
class ViolationRecord:
    """Mutable record tracking a single violation's lifecycle on a node.

    The violation ledger on each ``ContentNode`` is the single source
    of truth for violation status.

    Status transitions::

        open ──→ fixed          (deterministic transform, auto-approved)
          │
          ├──→ ai_abstained     (AI attempted but could not produce a fix)
          │
          └──→ proposed         (AI fix applied, pending human review)
                  │
                  ├──→ fixed    (user approved)
                  │
                  └──→ declined (user rejected, violation restored)

    Attributes:
        key: ``(node_id, normalized_rule_id)`` identity.
        violation: Original violation dict from the validator.
        status: ``"open"``, ``"fixed"``, ``"proposed"``, ``"declined"``,
            or ``"ai_abstained"``.
        fixed_by: How the violation was resolved
            (``"deterministic"``, ``"ai"``, or ``None``).
        fixed_in_pass: Convergence pass that resolved the violation.
        discovered_in_pass: Convergence pass that first detected it.
        ai_reason: Why the AI could not fix this violation (``ai_abstained`` only).
        ai_suggestion: Manual remediation guidance from the AI (``ai_abstained`` only).
    """

    key: ViolationKey
    violation: ViolationDict
    status: str = "open"
    fixed_by: str | None = None
    fixed_in_pass: int | None = None
    discovered_in_pass: int = 0
    ai_reason: str | None = None
    ai_suggestion: str | None = None


def _normalize_rule_id(rule_id: str) -> str:
    """Strip legacy ``native:`` prefix from a rule ID.

    Args:
        rule_id: Raw rule ID, possibly prefixed.

    Returns:
        Bare rule ID suitable for ledger keys.
    """
    if rule_id.startswith("native:"):
        return rule_id[len("native:") :]
    return rule_id


def _violation_key(v: ViolationDict) -> ViolationKey:
    """Derive a ``ViolationKey`` from a violation dict.

    Args:
        v: Violation dict with ``path`` and ``rule_id`` entries.

    Returns:
        ``(path, normalized_rule_id)`` tuple.
    """
    return (str(v.get("path", "")), _normalize_rule_id(str(v.get("rule_id", ""))))


def _content_hash(text: str) -> str:
    """Compute SHA-256 hex digest of a text string.

    Args:
        text: Input string (typically ``yaml_lines``).

    Returns:
        Hex digest string.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _detect_indent(text: str) -> int:
    """Count leading spaces on the first non-blank line.

    Args:
        text: YAML text fragment.

    Returns:
        Number of leading spaces (0 for root-level content).
    """
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped:
            return len(line) - len(stripped)
    return 0


def _reindent(text: str, target: int) -> str:
    """Shift every line so the first content line starts at *target* spaces.

    Blank lines are passed through unchanged.

    Args:
        text: YAML text to re-indent.
        target: Desired leading-space count for the first content line.

    Returns:
        Re-indented text.
    """
    current = _detect_indent(text)
    delta = target - current
    if delta == 0:
        return text
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    for line in lines:
        if not line.strip():
            result.append(line)
        elif delta > 0:
            result.append(" " * delta + line)
        else:
            remove = min(-delta, len(line) - len(line.lstrip()))
            result.append(line[remove:])
    return "".join(result)


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
        indent_depth: Leading spaces on the first content line of ``yaml_lines``.
        role_fqcn: Role FQCN when this node is role-related.
        default_variables: Role defaults mapping.
        role_variables: Role vars mapping.
        role_metadata: Role meta/main.yml contents (galaxy_info, dependencies, etc.).
        collection_namespace: Declaring collection namespace.
        collection_name: Declaring collection name.
        collection_metadata: Parsed ``galaxy.yml`` contents for COLLECTION nodes.
        collection_meta_runtime: Parsed ``meta/runtime.yml`` for COLLECTION nodes.
        collection_files: File paths within the collection root.
        module_line_count: Line count of the plugin ``.py`` file (MODULE nodes).
        module_functions_without_return_type: Function names lacking ``-> type``
            return annotations (MODULE nodes).
        annotations: Annotator payloads (risk, module hints, etc.).
        scope: Owned vs referenced content classification.
        state: Current ``NodeState`` snapshot (most recent entry in progression).
        progression: Ordered list of ``NodeState`` snapshots across pipeline phases.
        violation_ledger: Mutable violation lifecycle records keyed by
            ``(node_id, rule_id)``.  Single source of truth for violation
            status and attribution.
        MAX_PROGRESSION: Upper bound on progression length per node (class-level).
    """

    identity: NodeIdentity

    # Source location
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0

    # Content extracted from YAML
    name: str | None = None
    module: str = ""
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
    indent_depth: int = 0

    # Role metadata
    role_fqcn: str = ""
    default_variables: YAMLDict = field(default_factory=dict)
    role_variables: YAMLDict = field(default_factory=dict)
    role_metadata: YAMLDict = field(default_factory=dict)

    # Collection metadata
    collection_namespace: str = ""
    collection_name: str = ""
    collection_metadata: YAMLDict = field(default_factory=dict)
    collection_meta_runtime: YAMLDict = field(default_factory=dict)
    collection_files: list[str] = field(default_factory=list)

    # Module / plugin metadata (MODULE nodes)
    module_line_count: int = 0
    module_functions_without_return_type: list[str] = field(default_factory=list)

    # Annotations from risk/module annotators
    annotations: list[object] = field(default_factory=list)

    # Scope
    scope: NodeScope = NodeScope.OWNED

    # Progression (ADR-044 Phase 3) — temporal YAML state tracking
    state: NodeState | None = None
    progression: list[NodeState] = field(default_factory=list)

    # Violation ledger — single source of truth for violation lifecycle
    violation_ledger: dict[ViolationKey, ViolationRecord] = field(default_factory=dict)

    @property
    def node_type(self) -> NodeType:
        """Return the node's type from its identity."""
        return self.identity.node_type

    @property
    def node_id(self) -> str:
        """Return the node's stable string identifier."""
        return str(self.identity)

    MAX_PROGRESSION: ClassVar[int] = 20

    def record_state(
        self,
        pass_number: int,
        phase: str,
        source: str = "",
    ) -> NodeState:
        """Record a progression snapshot at the current pipeline phase.

        Creates a ``NodeState`` from the node's current ``yaml_lines``,
        appends it to ``progression``, and sets ``state`` to the new entry.

        Violation tracking is handled separately via the
        ``violation_ledger`` — this method only captures YAML content.

        If progression already contains ``MAX_PROGRESSION`` entries the
        oldest entry is dropped to prevent unbounded growth from bugs in
        the convergence loop.

        Args:
            pass_number: Convergence pass (0 = initial scan).
            phase: Pipeline phase (``"original"``, ``"scanned"``,
                ``"transformed"``, ``"ai_transformed"``).
            source: How the transform was produced (e.g.
                ``"deterministic"``, ``"ai"``).  UI metadata only.

        Returns:
            The newly created ``NodeState``.
        """
        seq = len(self.progression)
        ns = NodeState(
            id=f"{self.node_id}@{seq}",
            pass_number=pass_number,
            phase=phase,
            yaml_lines=self.yaml_lines,
            content_hash=_content_hash(self.yaml_lines),
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=source,
        )
        if len(self.progression) >= self.MAX_PROGRESSION:
            self.progression.pop(0)
        self.progression.append(ns)
        self.state = ns
        return ns

    def update_from_yaml(self, yaml_text: str) -> None:
        """Rebuild typed fields from modified YAML text.

        Called after a transform serializes a modified ``CommentedMap``
        back to text.  Updates ``yaml_lines`` and re-extracts all
        content fields that graph rules evaluate.

        Only applicable to TASK, HANDLER, and BLOCK nodes.  Play/role/
        playbook nodes have additional fields that are not extractable
        from a single YAML mapping.

        Args:
            yaml_text: New YAML text for this node's span.
        """
        self.yaml_lines = yaml_text
        parsed = _parse_yaml_for_update(yaml_text)
        if parsed is None:
            return
        _apply_parsed_fields(self, parsed)


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
        self._dirty_nodes: set[str] = set()

    # -- Serialization (ADR-044 Phase 2 switchover) -------------------------

    def to_dict(self, *, slim: bool = False) -> dict[str, object]:
        """Serialize the graph to a JSON-compatible dict.

        Produces a deterministic representation suitable for transmission
        over gRPC as JSON bytes.  Nodes carry all serializable
        ``ContentNode`` fields (``annotations`` is excluded because its
        elements are not guaranteed to be JSON-safe).  Edges carry typed
        attributes and are sorted by ``(source, target)`` for stability.

        Args:
            slim: When ``True``, strip ``state`` and ``progression``
                from serialized nodes.  Use for validator fan-out where
                only current node fields are needed — avoids transmitting
                per-pass snapshots that validators never read.

        Returns:
            Dict with ``nodes`` and ``edges`` lists plus metadata.
        """
        nodes: list[dict[str, object]] = []
        for nid in sorted(self.g.nodes):
            node = self.get_node(nid)
            if node is not None:
                nodes.append({"id": nid, "data": _node_to_dict(node, slim=slim)})

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
            "execution_edges": self.execution_edges(),
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
            node: The content node to register (indexed by ``node_id``).
        """
        nid = node.node_id
        self.g.add_node(nid, node=node)

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

    # -- Dirty-node tracking (ADR-044 Phase 3) ------------------------------

    @property
    def dirty_nodes(self) -> frozenset[str]:
        """Return the set of node IDs modified since the last clear.

        Returns:
            Frozen set of node-ID strings.
        """
        return frozenset(self._dirty_nodes)

    def clear_dirty(self) -> None:
        """Reset the dirty-node set (called after a convergence pass)."""
        self._dirty_nodes.clear()

    def apply_yaml(self, node_id: str, yaml_content: str) -> bool:
        """Replace a node's YAML content and mark it dirty.

        This is the public API for callers that already hold
        fully-formed YAML (e.g. AI-generated fixes).  It normalizes
        indentation to match the node's ``indent_depth`` (including
        depth 0, unlike ``apply_transform`` which skips depth 0
        because ruamel already serializes at the correct base indent).

        Returns False (no-op) when the node does not exist or the
        resulting content is identical to the current ``yaml_lines``.

        Args:
            node_id: Graph node identifier.
            yaml_content: New YAML text for the node.

        Returns:
            True if the node content actually changed, False otherwise.
        """
        node = self.get_node(node_id)
        if node is None:
            return False

        text = yaml_content
        if _detect_indent(text) != node.indent_depth:
            text = _reindent(text, node.indent_depth)

        if text == node.yaml_lines:
            return False

        node.update_from_yaml(text)
        self._dirty_nodes.add(node_id)
        return True

    def collect_violations(self) -> list[ViolationDict]:
        """Collect all remaining (open) violations from the graph.

        Delegates to ``query_violations(status="open")``.

        Returns:
            Combined list of ``ViolationDict`` from all nodes.
        """
        return self.query_violations(status="open")

    def collect_step_diffs(self) -> list[dict[str, object]]:
        """Collect per-step content diffs from every node's progression.

        Walks each node's progression and produces a diff record for
        each consecutive pair where ``content_hash`` changed.

        Violation tracking lives in the ``violation_ledger``, not in
        progression snapshots.  The ``violations_removed`` and
        ``violations_added`` fields are omitted — use
        ``query_violations()`` for authoritative violation accounting.

        Returns:
            List of step-diff records, each with ``node_id``,
            ``pass_number``, ``phase``, ``diff``, and ``source``.
        """
        steps: list[dict[str, object]] = []
        for node in self.nodes():
            prog = node.progression
            for i in range(1, len(prog)):
                prev, curr = prog[i - 1], prog[i]
                if prev.content_hash == curr.content_hash:
                    continue
                diff = "".join(
                    difflib.unified_diff(
                        prev.yaml_lines.splitlines(keepends=True),
                        curr.yaml_lines.splitlines(keepends=True),
                        fromfile=f"pass{prev.pass_number}/{node.node_id}",
                        tofile=f"pass{curr.pass_number}/{node.node_id}",
                    )
                )
                steps.append(
                    {
                        "node_id": node.node_id,
                        "pass_number": curr.pass_number,
                        "phase": curr.phase,
                        "source": curr.source,
                        "diff": diff,
                    }
                )
        return steps

    # -- Violation ledger API --------------------------------------------------

    def register_violations(
        self,
        violations: list[ViolationDict],
        pass_number: int,
    ) -> None:
        """Register violations into the ledger of their respective nodes.

        New violations are inserted with ``status="open"``.  Already-open
        violations are no-ops (re-confirmed).  Previously-fixed violations
        that reappear are reopened (regression).

        Args:
            violations: Violation dicts (``path`` must be a graph node ID).
            pass_number: Convergence pass that produced these violations.
        """
        for v in violations:
            node_id = str(v.get("path", ""))
            node = self.get_node(node_id)
            if node is None:
                continue
            key = _violation_key(v)
            existing = node.violation_ledger.get(key)
            if existing is None:
                node.violation_ledger[key] = ViolationRecord(
                    key=key,
                    violation=v,
                    status="open",
                    discovered_in_pass=pass_number,
                )
            elif existing.status == "ai_abstained":
                existing.violation = v
            elif existing.status in ("fixed", "proposed", "declined"):
                existing.status = "open"
                existing.violation = v
                existing.fixed_by = None
                existing.fixed_in_pass = None
            else:
                existing.violation = v

    def resolve_violations(
        self,
        node_id: str,
        remaining_rule_ids: set[str],
        *,
        fixed_by: str,
        pass_number: int,
        status: str = "fixed",
    ) -> int:
        """Transition open violations when absent from a rescan.

        For the given node, any open ledger entry whose rule_id is
        **not** in ``remaining_rule_ids`` transitions to *status*.

        Use ``status="fixed"`` for deterministic transforms (auto-approved)
        and ``status="proposed"`` for AI transforms (pending human review).

        Args:
            node_id: Graph node whose ledger to update.
            remaining_rule_ids: Normalized rule IDs still present
                after the rescan.
            fixed_by: Attribution (``"deterministic"`` or ``"ai"``).
            pass_number: Convergence pass of the resolution.
            status: Target status (``"fixed"`` or ``"proposed"``).

        Returns:
            Number of violations transitioned.
        """
        node = self.get_node(node_id)
        if node is None:
            return 0
        count = 0
        for record in node.violation_ledger.values():
            if record.status != "open":
                continue
            _, rule_id = record.key
            if rule_id not in remaining_rule_ids:
                record.status = status
                record.fixed_by = fixed_by
                record.fixed_in_pass = pass_number
                count += 1
        return count

    def abstain_violations(
        self,
        node_id: str,
        rule_ids: frozenset[str],
        *,
        reasons: dict[str, tuple[str, str]] | None = None,
    ) -> int:
        """Transition ``open`` violations to ``ai_abstained`` on a node.

        Called when the AI attempted violations on this node but could
        not produce a fix (returned ``None`` or ``AISkipped``).

        ``fixed_in_pass`` is intentionally **not** set because
        ``ai_abstained`` is not a resolution — the violation remains
        open for manual review.

        The ``remediation_resolution`` is stamped by
        :meth:`query_violations` at query time based on
        ``ViolationRecord.status`` — no dict mutation here.

        Args:
            node_id: Graph node whose violations to mark.
            rule_ids: Set of normalized rule IDs the AI abstained from.
            reasons: Optional mapping of ``normalized_rule_id`` to
                ``(reason, suggestion)`` from the AI provider.

        Returns:
            Number of violations transitioned to ``ai_abstained``.
        """
        node = self.get_node(node_id)
        if node is None:
            return 0
        count = 0
        for record in node.violation_ledger.values():
            if record.status != "open":
                continue
            _, rule_id = record.key
            if rule_id in rule_ids:
                record.status = "ai_abstained"
                if reasons is not None:
                    reason_pair = reasons.get(rule_id)
                    if reason_pair is not None:
                        record.ai_reason, record.ai_suggestion = reason_pair
                count += 1
        return count

    def approve_proposed(self, node_id: str) -> int:
        """Promote ``proposed`` violations to ``fixed`` on a node.

        Called when the user approves an AI proposal.

        Args:
            node_id: Graph node whose proposals to approve.

        Returns:
            Number of violations promoted.
        """
        node = self.get_node(node_id)
        if node is None:
            return 0
        count = 0
        for record in node.violation_ledger.values():
            if record.status == "proposed":
                record.status = "fixed"
                count += 1
        return count

    def decline_proposed(self, node_id: str) -> int:
        """Transition ``proposed`` violations to ``declined`` on a node.

        Called when the user rejects an AI proposal.  The node's YAML
        should already be rolled back via ``reject_node()`` before
        calling this.

        Args:
            node_id: Graph node whose proposals to decline.

        Returns:
            Number of violations declined.
        """
        node = self.get_node(node_id)
        if node is None:
            return 0
        count = 0
        for record in node.violation_ledger.values():
            if record.status == "proposed":
                record.status = "declined"
                record.fixed_by = None
                record.fixed_in_pass = None
                count += 1
        return count

    def query_violations(
        self,
        *,
        status: str | None = None,
        fixed_by: str | None = None,
    ) -> list[ViolationDict]:
        """Query violations across all nodes with optional filters.

        The ledger ``ViolationRecord.status`` is the single source of
        truth for resolution.  This method stamps
        ``remediation_resolution`` on returned dicts for statuses that
        have a deterministic mapping (``ai_abstained``, ``proposed``,
        ``declined``).  Downstream code should not re-derive resolution
        for these statuses.

        Args:
            status: Filter by status: ``"open"``, ``"fixed"``,
                ``"proposed"``, ``"declined"``, or ``"ai_abstained"``
                (``None`` = all).
            fixed_by: Filter by attribution (``None`` = all).

        Returns:
            Flat list of ``ViolationDict`` from matching records.
        """
        _status_resolution: dict[str, RemediationResolution] = {
            "ai_abstained": RemediationResolution.AI_ABSTAINED,
            "proposed": RemediationResolution.AI_PROPOSED,
            "declined": RemediationResolution.USER_REJECTED,
        }

        result: list[ViolationDict] = []
        for node in self.nodes():
            for record in node.violation_ledger.values():
                if status is not None and record.status != status:
                    continue
                if fixed_by is not None and record.fixed_by != fixed_by:
                    continue
                vdict = dict(record.violation)
                mapped = _status_resolution.get(record.status)
                if mapped is not None:
                    vdict["remediation_resolution"] = mapped
                if record.ai_reason:
                    vdict["ai_reason"] = record.ai_reason
                if record.ai_suggestion:
                    vdict["ai_suggestion"] = record.ai_suggestion
                result.append(vdict)
        return result

    # -- Approval tracking (ADR-044 Phase 3) --------------------------------

    def approve_pending(
        self,
        node_id: str | None = None,
        *,
        source_filter: str | None = None,
    ) -> int:
        """Approve pending progression entries.

        When ``node_id`` is given, only that node's entries are approved.
        When ``None``, entries across the entire graph are approved.

        When ``source_filter`` is set (e.g., ``"deterministic"``), only
        entries whose ``source`` matches the filter **or** whose
        ``source`` is empty (scan entries, initial state) are approved.
        This auto-approves Tier 1 transforms and their associated scan
        snapshots while leaving ``source="ai"`` entries pending.

        Since ``NodeState`` is frozen, each pending entry is replaced
        with a copy that has ``approved=True``.

        Args:
            node_id: Optional node to scope approval to.
            source_filter: When set, skip entries whose non-empty
                ``source`` differs from this value.

        Returns:
            Number of entries approved.
        """
        count = 0
        targets = [self.get_node(node_id)] if node_id else list(self.nodes())
        for node in targets:
            if node is None:
                continue
            for i, entry in enumerate(node.progression):
                if entry.approved:
                    continue
                if source_filter is not None and entry.source and entry.source != source_filter:
                    continue
                node.progression[i] = replace(entry, approved=True)
                count += 1
            if node.progression:
                last_approved = next(
                    (s for s in reversed(node.progression) if s.approved),
                    node.progression[-1],
                )
                node.state = last_approved
        return count

    def approve_node(self, node_id: str) -> bool:
        """Approve all pending entries for a specific node.

        Convenience wrapper around ``approve_pending`` scoped to one node.

        Args:
            node_id: Graph node identifier.

        Returns:
            True if any entries were approved.
        """
        return self.approve_pending(node_id) > 0

    def reject_node(self, node_id: str) -> bool:
        """Reject unapproved entries and cascade forward.

        Finds the first unapproved progression entry (index *N*) and
        truncates the progression to entries ``0..N-1``.  The node's
        ``yaml_lines`` and typed fields are restored to the last
        approved state.

        When ``N == 0`` (no approved snapshots exist), the baseline
        entry is retained so the node always has at least one
        progression snapshot and ``node.state`` stays consistent.

        Args:
            node_id: Graph node identifier.

        Returns:
            True if any entries were removed.
        """
        node = self.get_node(node_id)
        if node is None:
            return False

        first_unapproved = next(
            (i for i, s in enumerate(node.progression) if not s.approved),
            None,
        )
        if first_unapproved is None:
            return False

        if first_unapproved == 0:
            baseline = node.progression[0]
            node.progression[:] = [baseline]
            node.state = baseline
            node.update_from_yaml(baseline.yaml_lines)
        else:
            node.progression[:] = node.progression[:first_unapproved]
            restored = node.progression[-1]
            node.state = restored
            node.update_from_yaml(restored.yaml_lines)
        return True

    async def apply_transform(
        self,
        node_id: str,
        transform_fn: Callable[[_CommentedMap, ViolationDict], bool | Awaitable[bool]],
        violation: ViolationDict,
    ) -> bool:
        """Apply a node-level transform via an ephemeral CommentedMap.

        Parses ``node.yaml_lines`` into a ruamel ``CommentedMap``,
        invokes the transform, serializes the result back into
        ``node.yaml_lines``, and calls ``node.update_from_yaml()``
        to rebuild typed fields.  The node is marked dirty on success.

        Supports both sync and async transform functions.  Sync
        functions (deterministic transforms) are called directly;
        async functions (e.g. AI transforms) are awaited.

        Args:
            node_id: Graph node identifier.
            transform_fn: ``(CommentedMap, ViolationDict) -> bool``
                or ``async (CommentedMap, ViolationDict) -> bool``.
            violation: Violation dict passed to the transform.

        Returns:
            True if the transform modified the node.
        """
        from ruamel.yaml.comments import CommentedMap, CommentedSeq  # noqa: PLC0415

        from apme_engine.engine.yaml_utils import FormattedYAML  # noqa: PLC0415

        node = self.get_node(node_id)
        if node is None or not node.yaml_lines:
            return False

        frag_config = dict(FormattedYAML.default_config)
        frag_config["explicit_start"] = False
        yaml = FormattedYAML(
            typ="rt",
            pure=True,
            version=(1, 1),
            config=frag_config,  # type: ignore[arg-type]
        )
        try:
            data = yaml.load(node.yaml_lines)
        except Exception:  # noqa: BLE001
            return False

        task: CommentedMap | None = None
        wrapper_seq: CommentedSeq | None = None
        if isinstance(data, CommentedSeq) and len(data) == 1 and isinstance(data[0], CommentedMap):
            task = data[0]
            wrapper_seq = data
        elif isinstance(data, CommentedMap):
            task = data
        if task is None:
            return False

        result = transform_fn(task, violation)
        applied = await result if inspect.isawaitable(result) else result
        if not applied:
            return False

        new_text = yaml.dumps(wrapper_seq) if wrapper_seq is not None else yaml.dumps(task)

        if node.indent_depth and _detect_indent(new_text) != node.indent_depth:
            new_text = _reindent(new_text, node.indent_depth)

        node.update_from_yaml(new_text)
        self._dirty_nodes.add(node_id)
        return True

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

    def has_edge_from(self, node_id: str, edge_type: EdgeType) -> bool:
        """Check if a node has any outgoing edge of the given type.

        More efficient than ``edges_from`` when only existence is needed,
        as it short-circuits on the first match without building a list
        or copying edge attribute dicts.

        Args:
            node_id: Source vertex id.
            edge_type: Edge type to check for.

        Returns:
            True if at least one matching outgoing edge exists.
        """
        if node_id not in self.g:
            return False
        ev = edge_type.value
        return any(data.get("edge_type") == ev for _, _, data in self.g.out_edges(node_id, data=True))

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
        """Return all descendant node IDs (transitive children via any edge).

        Args:
            node_id: Root of the descendant subgraph.

        Returns:
            All reachable node ids, or an empty set if ``node_id`` is absent.
        """
        if node_id not in self.g:
            return set()
        return cast(set[str], nx.descendants(self.g, node_id))

    def structural_descendants(self, node_id: str) -> set[str]:
        """Return descendant node IDs reachable via CONTAINS edges only.

        Unlike :meth:`descendants`, this traverses only structural
        (CONTAINS) edges, excluding DATA_FLOW, NOTIFY, INCLUDE, etc.

        Args:
            node_id: Root of the structural subtree.

        Returns:
            All structurally reachable node ids (excluding *node_id*
            itself), or an empty set if ``node_id`` is absent.
        """
        if node_id not in self.g:
            return set()
        result: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for target, _attrs in self.edges_from(current, EdgeType.CONTAINS):
                if target not in result:
                    result.add(target)
                    stack.append(target)
        return result

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

    # -- Execution-order view -----------------------------------------------

    def execution_edges(self) -> list[dict[str, str]]:
        """Compute the execution-order edge list for the graph.

        All positional edges (CONTAINS, INCLUDE, IMPORT) are treated
        uniformly as parent-to-child relationships for execution flow.
        This ensures ``import_playbook`` entries are threaded inline at
        their declared position alongside regular plays, and
        ``include_tasks``/``import_tasks`` targets appear as children of
        the including task node.

        Returns:
            List of ``{"source": src_id, "target": tgt_id}`` dicts
            representing execution flow transitions.  The list order
            is deterministic (parents sorted lexicographically) but is
            not itself a global topological ordering — each edge
            encodes a local flow dependency.
        """
        children_by_parent: dict[str, list[tuple[str, int]]] = {}

        # Rescue/always children are wired with both a CONTAINS edge and
        # a RESCUE/ALWAYS edge.  Collect those pairs so we can exclude
        # the CONTAINS edge from the mainline execution chain.
        rescue_always_pairs: set[tuple[str, str]] = set()
        for src, tgt, data in self.g.edges(data=True):
            if data.get("edge_type") in (EdgeType.RESCUE.value, EdgeType.ALWAYS.value):
                rescue_always_pairs.add((src, tgt))

        for src, tgt, data in self.g.edges(data=True):
            etype = data.get("edge_type", "")
            if etype in (
                EdgeType.CONTAINS.value,
                EdgeType.INCLUDE.value,
                EdgeType.IMPORT.value,
            ):
                if etype == EdgeType.CONTAINS.value and (src, tgt) in rescue_always_pairs:
                    continue
                pos = data.get("position", 0)
                children_by_parent.setdefault(src, []).append((tgt, pos))

        for children in children_by_parent.values():
            children.sort(key=lambda t: t[1])

        def last_exit(node_id: str, visited: set[str] | None = None) -> str:
            if visited is None:
                visited = set()
            if node_id in visited:
                return node_id
            visited.add(node_id)
            ch = children_by_parent.get(node_id)
            if not ch:
                return node_id
            return last_exit(ch[-1][0], visited)

        edges: list[dict[str, str]] = []

        for parent_id in sorted(children_by_parent):
            children = children_by_parent[parent_id]
            if not children:
                continue
            edges.append({"source": parent_id, "target": children[0][0]})
            for i in range(len(children) - 1):
                exit_node = last_exit(children[i][0])
                edges.append({"source": exit_node, "target": children[i + 1][0]})

        return edges


# ---------------------------------------------------------------------------
# Node serialization helpers (ADR-044 Phase 2)
# ---------------------------------------------------------------------------

_CONTENT_NODE_SIMPLE_FIELDS: tuple[str, ...] = (
    "file_path",
    "line_start",
    "line_end",
    "name",
    "module",
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
    "indent_depth",
    "role_fqcn",
    "default_variables",
    "role_variables",
    "role_metadata",
    "collection_namespace",
    "collection_name",
    "collection_metadata",
    "collection_meta_runtime",
    "collection_files",
    "module_line_count",
    "module_functions_without_return_type",
)


def _node_state_to_dict(ns: NodeState) -> dict[str, object]:
    """Serialize a NodeState to a JSON-compatible dict.

    Args:
        ns: NodeState snapshot to serialize.

    Returns:
        Plain dict with all NodeState fields.
    """
    return {
        "id": ns.id,
        "pass_number": ns.pass_number,
        "phase": ns.phase,
        "yaml_lines": ns.yaml_lines,
        "content_hash": ns.content_hash,
        "timestamp": ns.timestamp,
        "approved": ns.approved,
        "source": ns.source,
    }


def _node_state_from_dict(d: dict[str, object]) -> NodeState:
    """Reconstruct a NodeState from a serialized dict.

    Args:
        d: Dict produced by ``_node_state_to_dict``.

    Returns:
        Reconstructed frozen NodeState.
    """
    return NodeState(
        id=str(d.get("id", "")),
        pass_number=int(cast(int, d.get("pass_number", 0))),
        phase=str(d.get("phase", "")),
        yaml_lines=str(d.get("yaml_lines", "")),
        content_hash=str(d.get("content_hash", "")),
        timestamp=str(d.get("timestamp", "")),
        approved=bool(d.get("approved", False)),
        source=str(d.get("source", "")),
    )


def _violation_record_to_dict(rec: ViolationRecord) -> dict[str, object]:
    """Serialize a ViolationRecord to a JSON-compatible dict.

    Args:
        rec: Violation record to serialize.

    Returns:
        Plain dict with all ViolationRecord fields.
    """
    d: dict[str, object] = {
        "key": list(rec.key),
        "violation": dict(rec.violation),
        "status": rec.status,
        "discovered_in_pass": rec.discovered_in_pass,
    }
    if rec.fixed_by is not None:
        d["fixed_by"] = rec.fixed_by
    if rec.fixed_in_pass is not None:
        d["fixed_in_pass"] = rec.fixed_in_pass
    if rec.ai_reason is not None:
        d["ai_reason"] = rec.ai_reason
    if rec.ai_suggestion is not None:
        d["ai_suggestion"] = rec.ai_suggestion
    return d


def _violation_record_from_dict(d: dict[str, object]) -> ViolationRecord:
    """Reconstruct a ViolationRecord from a serialized dict.

    Args:
        d: Dict produced by ``_violation_record_to_dict``.

    Returns:
        Reconstructed ViolationRecord.
    """
    raw_key = d.get("key", ("", ""))
    if isinstance(raw_key, (list, tuple)) and len(raw_key) >= 2:
        key: ViolationKey = (str(raw_key[0]), str(raw_key[1]))
    else:
        key = ("", "")
    raw_violation = d.get("violation", {})
    violation: ViolationDict = dict(raw_violation) if isinstance(raw_violation, dict) else {}
    fixed_in_raw = d.get("fixed_in_pass")
    return ViolationRecord(
        key=key,
        violation=violation,
        status=str(d.get("status", "open")),
        fixed_by=str(d["fixed_by"]) if "fixed_by" in d else None,
        fixed_in_pass=int(cast(int, fixed_in_raw)) if fixed_in_raw is not None else None,
        discovered_in_pass=int(cast(int, d.get("discovered_in_pass", 0))),
        ai_reason=str(d["ai_reason"]) if "ai_reason" in d else None,
        ai_suggestion=str(d["ai_suggestion"]) if "ai_suggestion" in d else None,
    )


def _node_to_dict(node: ContentNode, *, slim: bool = False) -> dict[str, object]:
    """Serialize a ContentNode to a JSON-compatible dict.

    Args:
        node: ContentNode to serialize.
        slim: When ``True``, omit ``state`` and ``progression`` fields.

    Returns:
        Dict with identity, scope, and all content fields.  ``node_type``
        is promoted to a top-level convenience field alongside the full
        ``identity`` dict.
    """
    d: dict[str, object] = {
        "identity": {
            "path": node.identity.path,
            "node_type": node.identity.node_type.value,
        },
        "node_type": node.identity.node_type.value,
        "scope": node.scope.value,
    }
    for fname in _CONTENT_NODE_SIMPLE_FIELDS:
        d[fname] = getattr(node, fname)

    if not slim:
        if node.state is not None:
            d["state"] = _node_state_to_dict(node.state)
        if node.progression:
            d["progression"] = [_node_state_to_dict(ns) for ns in node.progression]
        if node.violation_ledger:
            d["violation_ledger"] = [_violation_record_to_dict(rec) for rec in node.violation_ledger.values()]

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

    node = ContentNode(**kwargs)  # type: ignore[arg-type]

    raw_state = d.get("state")
    deserialized_state: NodeState | None = None
    if isinstance(raw_state, dict):
        deserialized_state = _node_state_from_dict(cast(dict[str, object], raw_state))

    raw_progression = d.get("progression")
    deserialized_progression: list[NodeState] | None = None
    if isinstance(raw_progression, list):
        deserialized_progression = [
            _node_state_from_dict(cast(dict[str, object], entry))
            for entry in raw_progression
            if isinstance(entry, dict)
        ]

    # Reconcile: progression is source of truth; state == progression[-1].
    if deserialized_progression:
        nid = str(identity.path)
        for i, entry in enumerate(deserialized_progression):
            if not entry.id:
                deserialized_progression[i] = replace(entry, id=f"{nid}@{i}")
        node.progression = deserialized_progression
        node.state = deserialized_progression[-1]
    elif deserialized_state is not None:
        node.state = deserialized_state

    # Restore violation ledger.
    raw_ledger = d.get("violation_ledger")
    if isinstance(raw_ledger, list):
        for entry in raw_ledger:
            if isinstance(entry, dict):
                rec = _violation_record_from_dict(cast(dict[str, object], entry))
                node.violation_ledger[rec.key] = rec

    return node


# ---------------------------------------------------------------------------
# Shared utilities used by both ContentNode and GraphBuilder
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# YAML field extraction for update_from_yaml (ADR-044 Phase 3)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# YAML field extraction for update_from_yaml (ADR-044 Phase 3)
# ---------------------------------------------------------------------------

_TASK_META_KEYS = frozenset(
    {
        "name",
        "when",
        "changed_when",
        "failed_when",
        "register",
        "notify",
        "listen",
        "become",
        "become_user",
        "become_method",
        "become_flags",
        "delegate_to",
        "run_once",
        "connection",
        "ignore_errors",
        "ignore_unreachable",
        "no_log",
        "tags",
        "environment",
        "vars",
        "args",
        "loop",
        "loop_control",
        "with_items",
        "with_dict",
        "with_fileglob",
        "with_subelements",
        "with_sequence",
        "with_nested",
        "with_first_found",
        "block",
        "rescue",
        "always",
        "any_errors_fatal",
        "max_fail_percentage",
        "check_mode",
        "diff",
        "throttle",
        "timeout",
        "retries",
        "delay",
        "until",
        "debugger",
        "module_defaults",
        "collections",
        "action",
        "local_action",
    }
)


def _parse_yaml_for_update(yaml_text: str) -> dict[str, object] | None:
    """Parse a YAML text fragment into a plain dict for field extraction.

    Uses ``yaml.safe_load`` (not ruamel) since we only need values,
    not round-trip fidelity.  Returns ``None`` on parse failure.

    Args:
        yaml_text: Raw YAML string (typically a single task mapping).

    Returns:
        Parsed dict, or ``None`` if the text is unparseable.
    """
    import yaml  # noqa: PLC0415

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None

    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return cast(dict[str, object], data[0])
    if isinstance(data, dict):
        return cast(dict[str, object], data)
    return None


def _apply_parsed_fields(node: ContentNode, parsed: dict[str, object]) -> None:
    """Update a ContentNode's typed fields from a parsed YAML dict.

    Mirrors the field extraction logic in ``GraphBuilder._build_task``
    but operates on a plain dict instead of a parsed object.

    Args:
        node: ContentNode to update in place.
        parsed: Parsed YAML dict (single task/handler mapping).
    """
    module_key = None
    for key in parsed:
        if key not in _TASK_META_KEYS:
            module_key = str(key)
            break

    if module_key is not None:
        node.module = module_key
        raw_opts = parsed.get(module_key)
        if isinstance(raw_opts, dict):
            node.module_options = cast(YAMLDict, raw_opts)
        elif raw_opts is not None:
            node.module_options = cast(YAMLDict, {"_raw": raw_opts})
        else:
            node.module_options = {}

    node.name = parsed.get("name") if isinstance(parsed.get("name"), str) else None  # type: ignore[assignment]

    when_raw = parsed.get("when")
    if isinstance(when_raw, str):
        node.when_expr = when_raw
    elif isinstance(when_raw, list):
        node.when_expr = [str(x) for x in when_raw]
    else:
        node.when_expr = None

    node.variables = cast(YAMLDict, parsed["vars"]) if isinstance(parsed.get("vars"), dict) else {}

    become_user = parsed.get("become_user")
    become_val = parsed.get("become")
    become_method = parsed.get("become_method")
    become_flags = parsed.get("become_flags")
    if any(v is not None for v in (become_val, become_user, become_method, become_flags)):
        node.become = cast(
            YAMLDict,
            {
                k: v
                for k, v in [
                    ("become", become_val),
                    ("become_user", become_user),
                    ("become_method", become_method),
                    ("become_flags", become_flags),
                ]
                if v is not None
            },
        )
    else:
        node.become = None

    node.tags = _as_str_list(parsed.get("tags"))

    loop_val = parsed.get("loop") or next(
        (parsed[k] for k in parsed if k.startswith("with_")),
        None,
    )
    node.loop = cast(YAMLValue, loop_val)

    loop_ctrl = parsed.get("loop_control")
    node.loop_control = cast(YAMLDict, loop_ctrl) if isinstance(loop_ctrl, dict) else None

    register_raw = parsed.get("register")
    node.register = register_raw if isinstance(register_raw, str) else None

    env_raw = parsed.get("environment")
    node.environment = cast(YAMLDict, env_raw) if isinstance(env_raw, dict) else None

    no_log_raw = parsed.get("no_log")
    node.no_log = no_log_raw if isinstance(no_log_raw, bool) else None

    ignore_raw = parsed.get("ignore_errors")
    node.ignore_errors = ignore_raw if isinstance(ignore_raw, bool) else None

    node.changed_when = cast(YAMLValue, parsed.get("changed_when"))
    node.failed_when = cast(YAMLValue, parsed.get("failed_when"))

    delegate_raw = parsed.get("delegate_to")
    node.delegate_to = delegate_raw if isinstance(delegate_raw, str) else None

    node.notify = _as_str_list(parsed.get("notify"))
    node.listen = _as_str_list(parsed.get("listen"))

    # set_facts: for set_fact modules, extract variable names from module args
    _SET_FACT_MODULES = frozenset(  # noqa: N806
        {"ansible.builtin.set_fact", "ansible.legacy.set_fact", "set_fact"},
    )
    if module_key in _SET_FACT_MODULES and isinstance(node.module_options, dict):
        node.set_facts = cast(
            YAMLDict,
            {k: v for k, v in node.module_options.items() if k != "cacheable"},
        )
    else:
        node.set_facts = {}

    # Rebuild generic options so node.options stays consistent with the YAML.
    # Mirrors GraphBuilder._build_task: exclude name, module key, and
    # block-structure keys.
    _BLOCK_KEYS = frozenset({"block", "rescue", "always"})  # noqa: N806
    options: dict[str, object] = {}
    for key, value in parsed.items():
        if key == "name" or key == module_key or key in _BLOCK_KEYS:
            continue
        options[key] = value
    node.options = cast(YAMLDict, options)
