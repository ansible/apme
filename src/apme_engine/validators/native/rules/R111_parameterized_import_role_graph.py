"""GraphRule R111: detect Jinja-templated role name in include/import_role."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult, is_templated

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_ROLE_MODULES = frozenset(
    {
        "ansible.builtin.include_role",
        "ansible.builtin.import_role",
        "ansible.legacy.include_role",
        "ansible.legacy.import_role",
        "include_role",
        "import_role",
    }
)


@dataclass
class ParameterizedImportRoleGraphRule(GraphRule):
    """Detect include/import_role with a Jinja-templated role name.

    A parameterized role name means the role resolved at runtime depends
    on variable values, making static analysis incomplete and introducing
    supply-chain risk if the variable is externally controlled.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R111"
    description: str = "Import/include a parameterized name of role"
    enabled: bool = True
    name: str = "ParameterizedImportRole"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that invoke include_role or import_role.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node invokes a role-type module.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        mod = node.module
        return mod in _ROLE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report when the role ``name`` argument contains Jinja2 templates.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result, or None if node missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        role_name = mo.get("name", "")
        if not isinstance(role_name, str):
            role_name = str(role_name) if role_name else ""

        if not is_templated(role_name):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"role": role_name}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
