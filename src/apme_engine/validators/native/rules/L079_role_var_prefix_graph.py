"""GraphRule L079: Role defaults and vars should use the role name as a prefix."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

SKIP_VARS = frozenset(
    {
        "ansible_become",
        "ansible_become_method",
        "ansible_become_user",
        "ansible_connection",
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_python_interpreter",
        "ansible_ssh_common_args",
        "ansible_ssh_private_key_file",
    }
)


@dataclass
class RoleVarPrefixGraphRule(GraphRule):
    """Require role-scoped variable names to share a role-specific prefix.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order (lower = earlier).
    """

    rule_id: str = "L079"
    description: str = "Role defaults/vars should be prefixed with the role name"
    enabled: bool = True
    name: str = "RoleVarPrefix"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)
    scope: str = RuleScope.ROLE
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match ROLE nodes only.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a ROLE.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.ROLE

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag defaults and vars whose names omit the expected role prefix.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult listing unprefixed variables when the role name is known.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        role_name = (node.name or "").strip() or (node.role_fqcn or "").strip()
        if not role_name:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        prefix = role_name.replace("-", "_") + "_"
        var_names = set(node.default_variables.keys()) | set(node.role_variables.keys())

        unprefixed: list[str] = []
        for var in sorted(var_names):
            if var in SKIP_VARS or var.startswith("__") or var.startswith(prefix):
                continue
            unprefixed.append(var)

        verdict = bool(unprefixed)
        if verdict:
            detail_dict = {
                "unprefixed_vars": unprefixed[:20],
                "expected_prefix": prefix,
                "message": f"role variables should be prefixed with '{prefix}'",
            }
            return GraphRuleResult(
                verdict=True,
                detail=cast(YAMLDict, detail_dict),
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        return GraphRuleResult(
            verdict=False,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
