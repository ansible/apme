"""Variable provenance tracking for ContentGraph (ADR-044).

Determines *where* each variable in a node's scope originates by walking
the graph ancestry.  This replaces the flat accumulation done by
``Context.add()`` with a provenance-preserving model.

Public API
----------
- ``VariableProvenance``   — where a single variable was defined
- ``PropertyOrigin``       — where an inherited property (become, etc.) was defined
- ``VariableProvenanceResolver`` — resolves all variables for a node
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .content_graph import ContentGraph, ContentNode, EdgeType, NodeType
from .models import YAMLValue

# ---------------------------------------------------------------------------
# Provenance classification
# ---------------------------------------------------------------------------


class ProvenanceSource(str, Enum):
    """Where a variable binding originated.

    Attributes:
        LOCAL: Defined on the task or handler itself.
        BLOCK: Defined on a containing block.
        ROLE_DEFAULT: Role defaults/main.
        ROLE_VAR: Role vars.
        PLAY: Play-level vars.
        PLAYBOOK: Playbook-level vars.
        RUNTIME: From register or set_fact (data_flow).
        INVENTORY_FILE: From inventory (placeholder classification).
        VARS_FILE: From a linked vars_file node.
        EXTERNAL: Unknown or out-of-graph origin.
    """

    LOCAL = "local"
    BLOCK = "block"
    ROLE_DEFAULT = "role_default"
    ROLE_VAR = "role_var"
    PLAY = "play"
    PLAYBOOK = "playbook"
    RUNTIME = "runtime"
    INVENTORY_FILE = "inventory_file"
    VARS_FILE = "vars_file"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class VariableProvenance:
    """Record of a single variable's origin.

    Attributes:
        name: Variable name (e.g. ``nginx_port``).
        value: Resolved value at the defining scope.
        source: Provenance classification.
        defining_node_id: Node ID where the variable is defined.
        file_path: File containing the definition.
        line: Approximate line number (0 if unknown).
    """

    name: str
    value: YAMLValue | None = None
    source: ProvenanceSource = ProvenanceSource.EXTERNAL
    defining_node_id: str = ""
    file_path: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class PropertyOrigin:
    """Record of an inherited property's defining scope.

    Used for ``become``, ``environment``, ``no_log``, ``tags``, etc.
    so that violations can be attributed to the scope where the
    property was actually set rather than every inheriting child.

    Attributes:
        property_name: Name of the inherited property.
        value: The property value at the defining scope.
        defining_node_id: Node ID of the scope that set this property.
        file_path: File where the property was defined.
        line: Line number of the defining node.
    """

    property_name: str
    value: YAMLValue | None = None
    defining_node_id: str = ""
    file_path: str = ""
    line: int = 0


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

_INHERITED_PROPERTIES = frozenset(
    {
        "become",
        "environment",
        "no_log",
        "ignore_errors",
        "tags",
    }
)

_PROVENANCE_BY_NODE_TYPE: dict[NodeType, ProvenanceSource] = {
    NodeType.TASK: ProvenanceSource.LOCAL,
    NodeType.HANDLER: ProvenanceSource.LOCAL,
    NodeType.BLOCK: ProvenanceSource.BLOCK,
    NodeType.PLAY: ProvenanceSource.PLAY,
    NodeType.PLAYBOOK: ProvenanceSource.PLAYBOOK,
    NodeType.ROLE: ProvenanceSource.ROLE_VAR,
    NodeType.TASKFILE: ProvenanceSource.LOCAL,
    NodeType.VARS_FILE: ProvenanceSource.VARS_FILE,
}


class VariableProvenanceResolver:
    """Resolves variable bindings and property origins for ContentGraph nodes.

    Walk order follows Ansible's precedence rules (simplified):
    local task vars > block vars > role vars > play vars > role defaults
    > playbook vars > vars_files > inventory vars > external.
    """

    def __init__(self, graph: ContentGraph) -> None:
        """Create a resolver bound to a content graph.

        Args:
            graph: ``ContentGraph`` to walk for scope and data-flow edges.
        """
        self._graph = graph

    def resolve_variables(self, node_id: str) -> dict[str, VariableProvenance]:
        """Resolve all variables in scope for a node.

        Returns a dict mapping variable names to their provenance.
        Variables from higher-precedence scopes shadow lower ones.

        Args:
            node_id: Graph node id whose effective variable scope is resolved.

        Returns:
            Map from variable name to ``VariableProvenance`` (shadowing applied).
        """
        result: dict[str, VariableProvenance] = {}
        node = self._graph.get_node(node_id)
        if node is None:
            return result

        scope_chain = self._build_scope_chain(node_id)

        for scope_node in scope_chain:
            source = _PROVENANCE_BY_NODE_TYPE.get(scope_node.node_type, ProvenanceSource.EXTERNAL)

            if scope_node.node_type == NodeType.ROLE:
                for name, value in scope_node.default_variables.items():
                    if name not in result:
                        result[name] = VariableProvenance(
                            name=name,
                            value=value,
                            source=ProvenanceSource.ROLE_DEFAULT,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )
                for name, value in scope_node.role_variables.items():
                    result[name] = VariableProvenance(
                        name=name,
                        value=value,
                        source=ProvenanceSource.ROLE_VAR,
                        defining_node_id=scope_node.node_id,
                        file_path=scope_node.file_path,
                        line=scope_node.line_start,
                    )
            else:
                for name, value in scope_node.variables.items():
                    if scope_node == node or name not in result:
                        result[name] = VariableProvenance(
                            name=name,
                            value=value,
                            source=source,
                            defining_node_id=scope_node.node_id,
                            file_path=scope_node.file_path,
                            line=scope_node.line_start,
                        )

            self._collect_vars_file_vars(scope_node, result)

        self._collect_runtime_vars(node_id, result)

        return result

    def resolve_property_origins(self, node_id: str) -> dict[str, PropertyOrigin]:
        """Find the defining scope for each inherited property.

        For ``become``, ``environment``, ``no_log``, etc., walks up the
        ancestry to find the nearest scope where the property is defined.

        Args:
            node_id: Graph node id whose inherited properties are attributed.

        Returns:
            Map from property name to ``PropertyOrigin`` for defined properties only.
        """
        result: dict[str, PropertyOrigin] = {}
        node = self._graph.get_node(node_id)
        if node is None:
            return result

        scope_chain = self._build_scope_chain(node_id)

        for prop_name in _INHERITED_PROPERTIES:
            for scope_node in scope_chain:
                value = getattr(scope_node, prop_name, None)
                if value is not None:
                    result[prop_name] = PropertyOrigin(
                        property_name=prop_name,
                        value=value,
                        defining_node_id=scope_node.node_id,
                        file_path=scope_node.file_path,
                        line=scope_node.line_start,
                    )
                    break

        return result

    def _build_scope_chain(self, node_id: str) -> list[ContentNode]:
        """Build the variable scope chain (self first, root last).

        Args:
            node_id: Node to resolve scope for.

        Returns:
            The node (if present) followed by ``CONTAINS`` ancestors toward the root.
        """
        chain: list[ContentNode] = []
        node = self._graph.get_node(node_id)
        if node is not None:
            chain.append(node)
        chain.extend(self._graph.ancestors(node_id))
        return chain

    def _collect_vars_file_vars(
        self,
        scope_node: ContentNode,
        result: dict[str, VariableProvenance],
    ) -> None:
        """Collect variables from vars_files linked to a scope node.

        Args:
            scope_node: Play, role, or other node with ``VARS_INCLUDE`` outgoing edges.
            result: Mutable provenance map updated in place.
        """
        for target_id, _attrs in self._graph.edges_from(scope_node.node_id, EdgeType.VARS_INCLUDE):
            vf_node = self._graph.get_node(target_id)
            if vf_node is None:
                continue
            for name, value in vf_node.variables.items():
                if name not in result:
                    result[name] = VariableProvenance(
                        name=name,
                        value=value,
                        source=ProvenanceSource.VARS_FILE,
                        defining_node_id=vf_node.node_id,
                        file_path=vf_node.file_path,
                        line=vf_node.line_start,
                    )

    def _collect_runtime_vars(
        self,
        node_id: str,
        result: dict[str, VariableProvenance],
    ) -> None:
        """Collect variables from data_flow edges (register/set_fact).

        Args:
            node_id: Consumer task node id.
            result: Mutable provenance map updated in place.
        """
        for source_id, _attrs in self._graph.edges_to(node_id, EdgeType.DATA_FLOW):
            source_node = self._graph.get_node(source_id)
            if source_node is None:
                continue
            if source_node.register:
                result[source_node.register] = VariableProvenance(
                    name=source_node.register,
                    value=None,
                    source=ProvenanceSource.RUNTIME,
                    defining_node_id=source_node.node_id,
                    file_path=source_node.file_path,
                    line=source_node.line_start,
                )
            for fact_name in source_node.set_facts:
                result[fact_name] = VariableProvenance(
                    name=fact_name,
                    value=source_node.set_facts.get(fact_name),
                    source=ProvenanceSource.RUNTIME,
                    defining_node_id=source_node.node_id,
                    file_path=source_node.file_path,
                    line=source_node.line_start,
                )
