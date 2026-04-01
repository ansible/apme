"""GraphRule L048: ``copy`` with ``remote_src`` should set ``owner`` explicitly."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

COPY_MODULES = frozenset(
    {
        "ansible.builtin.copy",
        "ansible.legacy.copy",
        "copy",
    }
)


@dataclass
class NoSameOwnerGraphRule(GraphRule):
    """Flag ``copy`` tasks with ``remote_src`` but no ``owner``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L048"
    description: str = "copy with remote_src should set owner explicitly; avoid same-owner default"
    enabled: bool = True
    name: str = "NoSameOwner"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.SYSTEM,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that use ``copy`` with truthy ``remote_src``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler, module is ``copy``, and
            ``remote_src`` is set to a truthy value.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        resolved = node.module
        if resolved not in COPY_MODULES:
            return False
        mo = node.module_options
        if not isinstance(mo, dict):
            return False
        return bool(mo.get("remote_src"))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when ``owner`` is omitted for remote ``copy``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when ``owner`` is missing, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}
        if "owner" in mo:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {"message": "copy with remote_src should set owner explicitly"}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
