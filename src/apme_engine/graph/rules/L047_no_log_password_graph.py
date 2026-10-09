"""GraphRule L047: password-like task parameters should use no_log.

Graph-aware port of ``L047_no_log_password.py``.  Treats ``no_log: true`` as
satisfying the rule when it appears on the task or on any ancestor scope,
not only as a literal on the task block.
"""

from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict
from apme_engine.graph.variable_helpers import enclosing_play_ids, no_log_true_in_scope

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

PASSWORD_LIKE_KEYS = frozenset({"password", "passwd", "pwd", "secret", "token", "api_key", "apikey", "private_key"})


def _option_keys_look_like_password(module_options: object) -> bool:
    """Return True if any option key looks like a password-related parameter.

    Args:
        module_options: Mapping of module or task options to inspect.

    Returns:
        True when at least one key (case-insensitive) is in the password-like set.
    """
    if not isinstance(module_options, dict):
        return False
    return any(k and str(k).lower() in PASSWORD_LIKE_KEYS for k in module_options)


@dataclass
class NoLogPasswordGraphRule(GraphRule):
    """Flag password-like parameters when ``no_log`` is not true anywhere in scope.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L047"
    description: str = "Tasks with password-like parameters should set no_log: true"
    enabled: bool = True
    name: str = "NoLogPassword"
    version: str = "v0.0.2"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SYSTEM,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks or handlers whose options include password-like keys.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task/handler with password-like keys in
            ``module_options`` or ``options``.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        return _option_keys_look_like_password(node.module_options) or _option_keys_look_like_password(node.options)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Require ``no_log`` when password-like keys are present.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result; ``verdict`` True when the violation is found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        play_ids = enclosing_play_ids(graph, node_id)
        if play_ids:
            # A shared include is protected only if every execution context
            # is protected, and every include path within each play inherits
            # no_log. Any unprotected execution can expose the password.
            protected = all(
                no_log_true_in_scope(
                    graph,
                    node_id,
                    play_context_id=play_id,
                    play_scope=graph.play_scoped_node_ids(play_id),
                    require_all_paths=True,
                )
                for play_id in play_ids
            ) and no_log_true_in_scope(graph, node_id, require_all_paths=True)
        else:
            protected = no_log_true_in_scope(graph, node_id, require_all_paths=True)
        if protected:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {
            "message": "password-like parameter detected; set no_log: true to avoid logging",
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
