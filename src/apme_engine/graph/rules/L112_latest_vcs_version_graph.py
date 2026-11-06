"""GraphRule L112: require an explicit version for git and Mercurial tasks."""

from __future__ import annotations

from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult, is_templated
from apme_engine.graph.rules._module_risk_mapping import short_module_name_is_ambiguous
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})
_LATEST_DEFAULTS = {
    "git": ("version", "HEAD"),
    "hg": ("revision", "default"),
    "community.general.hg": ("revision", "default"),
    "ansible.builtin.git": ("version", "HEAD"),
    "ansible.legacy.git": ("version", "HEAD"),
}


@dataclass
class LatestVCSVersionGraphRule(GraphRule):
    """Flag git/hg tasks that use their moving default revision.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule implementation version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L112"
    description: str = "Git and Mercurial tasks should select an explicit version"
    enabled: bool = True
    name: str = "LatestVCSVersion"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task nodes using a supported git or Mercurial module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a supported, unambiguous Git or Mercurial task.
        """
        node = graph.get_node(node_id)
        return bool(
            node
            and node.node_type in _TASK_TYPES
            and node.module in _LATEST_DEFAULTS
            and not short_module_name_is_ambiguous(node.module, node.collections_in_play)
        )

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report tasks that rely on the moving default revision.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A violation result for an unset or moving revision, a clean result
            when a revision is pinned, or None if the node is not applicable.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return None
        module_name = node.module
        if short_module_name_is_ambiguous(module_name, node.collections_in_play):
            return None
        configured = _LATEST_DEFAULTS.get(module_name)
        if configured is None:
            return None
        option, default = configured
        value = node.effective_module_options.get(option)
        is_default = value is None or (isinstance(value, str) and (not value.strip() or value.strip() == default))
        if not is_default or (isinstance(value, str) and is_templated(value)):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "module": module_name,
            "option": option,
            "default_value": default,
            "message": f"{module_name} task should set {option} to a pinned revision",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
