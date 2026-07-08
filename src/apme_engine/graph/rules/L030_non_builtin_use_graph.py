"""GraphRule L030: prefer ansible.builtin when a builtin equivalent exists.

Graph-aware rule that only fires when the module's short name has a known
``ansible.builtin`` equivalent.  Modules from collections that have no
builtin counterpart (e.g. ``community.general.timezone``) are **not**
flagged — they are legitimate external dependencies, not missed builtins.
"""

from dataclasses import dataclass

from apme_engine.engine.finder import get_builtin_module_names
from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _has_builtin_equivalent(fqcn: str) -> bool:
    """Return True when the module's short name exists in ``ansible.builtin``.

    Args:
        fqcn: Fully-qualified collection module name (e.g.
            ``community.general.copy``).

    Returns:
        True if the short name (last segment) is a known builtin module.
    """
    short_name = fqcn.rsplit(".", 1)[-1]
    return short_name in get_builtin_module_names()


@dataclass
class NonBuiltinUseGraphRule(GraphRule):
    """Flag tasks using a non-builtin FQCN when a builtin equivalent exists.

    Only fires when the short module name (e.g. ``copy`` from
    ``community.general.copy``) has a known ``ansible.builtin`` counterpart.
    Collection modules with no builtin equivalent are intentional
    dependencies and are not flagged.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L030"
    description: str = "Non-builtin module used when a builtin equivalent exists"
    enabled: bool = True
    name: str = "NonBuiltinUse"
    version: str = "v0.0.3"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes using a non-builtin FQCN that has a builtin equivalent.

        Short module names (no dot) do not match.  Non-builtin FQCNs whose
        short name has no builtin counterpart do not match either.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the module FQCN is outside ``ansible.builtin`` **and**
            the short module name has a builtin equivalent.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        mod = node.module or ""
        if not mod or "." not in mod or mod.startswith("ansible.builtin."):
            return False
        return _has_builtin_equivalent(mod)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report the non-builtin FQCN and suggest the builtin alternative.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``verdict`` True when a builtin alternative exists.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mod = node.module or ""
        if not mod or "." not in mod or mod.startswith("ansible.builtin."):
            return GraphRuleResult(verdict=False, node_id=node_id)
        verdict = _has_builtin_equivalent(mod)
        short_name = mod.rsplit(".", 1)[-1]
        detail: YAMLDict = {
            "fqcn": mod,
            "builtin_alternative": f"ansible.builtin.{short_name}",
        }
        return GraphRuleResult(
            verdict=verdict,
            detail=detail if verdict else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
