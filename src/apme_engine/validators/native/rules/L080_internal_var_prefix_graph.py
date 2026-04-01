"""GraphRule L080: Internal role variables set via set_fact should use ``__`` prefix."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

SET_FACT_MODULES = frozenset(
    {
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
        "set_fact",
    }
)


@dataclass
class InternalVarPrefixGraphRule(GraphRule):
    """Flag set_fact keys in role content that lack an underscore prefix.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L080"
    description: str = "Internal role variables should be prefixed with __ (double underscore)"
    enabled: bool = True
    name: str = "InternalVarPrefix"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match set_fact tasks and handlers defined under a role path.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler under ``/roles/`` whose
            module is set_fact.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        fp = node.file_path or ""
        if "/roles/" not in fp and not fp.startswith("roles/"):
            return False
        resolved = node.module
        return resolved in SET_FACT_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when fact keys omit a leading underscore.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True and a ``variables`` list
            when any key is neither ``cacheable`` nor underscore-prefixed, or
            ``verdict`` False when all keys are excluded, or None if the node is
            missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mo = node.module_options if isinstance(node.module_options, dict) else {}
        sf = node.set_facts if isinstance(node.set_facts, dict) else {}
        names = set(mo.keys()) | set(sf.keys())
        non_prefixed = sorted(k for k in names if k != "cacheable" and not str(k).startswith("_"))
        if not non_prefixed:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {"variables": cast("list[YAMLValue]", non_prefixed)}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
