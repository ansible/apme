"""GraphRule L082: template ``src`` paths should use a ``.j2`` extension."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

TEMPLATE_MODULES = frozenset(
    {
        "ansible.builtin.template",
        "ansible.legacy.template",
        "template",
    }
)


@dataclass
class TemplateJ2ExtGraphRule(GraphRule):
    """Flag static template ``src`` values that do not end with ``.j2``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L082"
    description: str = "Template source files should use .j2 extension"
    enabled: bool = True
    name: str = "TemplateJ2Ext"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes that invoke the template module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler whose resolved module is a
            template module.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        resolved = node.module
        return resolved in TEMPLATE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when a literal ``src`` lacks a ``.j2`` suffix.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when ``src`` is a non-empty
            string without Jinja and not ending in ``.j2``, or None if the node
            is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}
        raw = mo.get("src")
        if not isinstance(raw, str) or not raw or "{{" in raw:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        if raw.endswith(".j2"):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "src": raw,
            "message": "template source files should use .j2 extension",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
