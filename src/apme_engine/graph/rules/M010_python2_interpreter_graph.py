"""GraphRule M010: detect ``ansible_python_interpreter`` pointing at Python 2.

Reports the finding at the defining scope (play/block) rather than
duplicating it on every child task.  Task-local ``vars`` and module
arguments are still checked directly on each task.
"""

import re
from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})
_VAR_SCOPE_TYPES = frozenset({NodeType.PLAY, NodeType.BLOCK})
_MATCH_TYPES = _TASK_TYPES | _VAR_SCOPE_TYPES

_PY2_PATH = re.compile(r"python2(\.\d+)?$")


def _options_vars(node: ContentNode) -> YAMLDict:
    """Return the task ``vars`` mapping from ``node.options`` when present.

    Args:
        node: Task or handler node.

    Returns:
        Vars dict, or empty dict if missing or not a mapping.
    """
    raw = node.options.get("vars")
    return raw if isinstance(raw, dict) else {}


def _local_py2_hit(node: ContentNode) -> str | None:
    """Return a Python-2 interpreter string from task-local fields, if any.

    Args:
        node: Task or handler node.

    Returns:
        Matching interpreter string, or None.
    """
    for val in (
        node.variables.get("ansible_python_interpreter"),
        _options_vars(node).get("ansible_python_interpreter"),
        node.module_options.get("ansible_python_interpreter"),
        node.options.get("ansible_python_interpreter"),
    ):
        if val is None or val == "":
            continue
        s = str(val)
        if _PY2_PATH.search(s):
            return s
    return None


@dataclass
class Python2InterpreterGraphRule(GraphRule):
    """Flag ``ansible_python_interpreter`` values that reference Python 2.

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

    rule_id: str = "M010"
    description: str = "ansible_python_interpreter set to Python 2; dropped in 2.18+"
    enabled: bool = True
    name: str = "Python2Interpreter"
    version: str = "v0.0.2"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)
    scope: str = RuleScope.PLAY
    precedence: int = 10

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match play, block, task, and handler nodes.

        Plays and blocks are matched so that a play/block-level
        ``ansible_python_interpreter`` is reported once at the defining
        scope rather than duplicated on every child task.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a play, block, task, or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _MATCH_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Detect Python-2 interpreter paths.

        For plays/blocks: check the node's own vars for a Python-2 path.
        For tasks/handlers: only check task-local fields (vars, module_options,
        options) — inherited play/block vars are caught at the defining scope.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        if node.node_type in _VAR_SCOPE_TYPES:
            return self._check_scope_node(node)

        return self._check_task_node(node)

    def _check_scope_node(self, node: ContentNode) -> GraphRuleResult:
        """Check a play or block node's own vars for Python-2 interpreter.

        Args:
            node: Play or block node.

        Returns:
            GraphRuleResult with verdict.
        """
        val = node.variables.get("ansible_python_interpreter")
        if val is None:
            raw_vars = node.options.get("vars")
            if isinstance(raw_vars, dict):
                val = raw_vars.get("ansible_python_interpreter")

        if val is not None and val != "":
            s = str(val)
            if _PY2_PATH.search(s):
                return GraphRuleResult(
                    verdict=True,
                    detail={
                        "message": f"ansible_python_interpreter set to Python 2 path: {s}",
                        "interpreter": s,
                    },
                    node_id=node.node_id,
                    file=(node.file_path, node.line_start),
                )

        return GraphRuleResult(
            verdict=False,
            detail={},
            node_id=node.node_id,
            file=(node.file_path, node.line_start),
        )

    def _check_task_node(self, node: ContentNode) -> GraphRuleResult:
        """Check a task/handler for a task-local Python-2 interpreter.

        Only inspects fields directly on the task (vars, module_options,
        options). Inherited play/block vars are not checked here — they
        are caught by ``_check_scope_node`` on the defining ancestor.

        Args:
            node: Task or handler node.

        Returns:
            GraphRuleResult with verdict.
        """
        local_hit = _local_py2_hit(node)
        if local_hit is not None:
            return GraphRuleResult(
                verdict=True,
                detail={
                    "message": f"ansible_python_interpreter set to Python 2 path: {local_hit}",
                    "interpreter": local_hit,
                },
                node_id=node.node_id,
                file=(node.file_path, node.line_start),
            )

        return GraphRuleResult(
            verdict=False,
            detail={},
            node_id=node.node_id,
            file=(node.file_path, node.line_start),
        )
