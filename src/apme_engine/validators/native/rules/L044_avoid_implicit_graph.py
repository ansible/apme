"""GraphRule L044: require explicit ``state`` for modules with implicit defaults."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_SHORT_NAMES = frozenset(
    {"file", "copy", "template", "package", "apt", "dnf", "yum", "service", "mount", "user", "group"}
)

MODULES_NEEDING_STATE = frozenset(
    _SHORT_NAMES | {f"ansible.builtin.{n}" for n in _SHORT_NAMES} | {f"ansible.legacy.{n}" for n in _SHORT_NAMES}
)


@dataclass
class AvoidImplicitGraphRule(GraphRule):
    """Flag tasks using certain modules when ``state`` is omitted.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L044"
    description: str = "Avoid implicit behavior; set state (or other key) explicitly where it matters"
    enabled: bool = True
    name: str = "AvoidImplicit"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes whose module requires an explicit ``state``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler using a module in
            ``MODULES_NEEDING_STATE``.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        resolved = node.module
        return resolved in MODULES_NEEDING_STATE

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when ``state`` is missing from module arguments.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when ``state`` is absent, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        resolved = node.module
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}
        if "state" in mo:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "module": resolved,
            "message": "state is not set; consider setting state explicitly",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
