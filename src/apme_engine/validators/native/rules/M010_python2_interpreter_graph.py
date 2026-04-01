"""GraphRule M010: detect ``ansible_python_interpreter`` pointing at Python 2.

Graph-aware port of ``M010_python2_interpreter.py``.  Uses
``VariableProvenanceResolver.resolve_variables`` for scope-wide variable
bindings and still inspects task-local ``vars`` and module arguments.
"""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeType
from apme_engine.engine.models import RuleScope, Severity, YAMLDict
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.variable_provenance import VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

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
        """Match task and handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Detect Python-2 interpreter paths from locals and resolved variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with interpreter and provenance detail, or None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        resolver = VariableProvenanceResolver(graph)
        resolved = resolver.resolve_variables(node_id)
        prov = resolved.get("ansible_python_interpreter")

        detail: YAMLDict = {}
        local_hit = _local_py2_hit(node)

        if local_hit is not None:
            detail["message"] = f"ansible_python_interpreter set to Python 2 path: {local_hit}"
            detail["interpreter"] = local_hit
            return GraphRuleResult(
                verdict=True,
                detail=detail,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        if prov is not None and prov.value is not None:
            s = str(prov.value)
            if _PY2_PATH.search(s):
                detail["message"] = f"ansible_python_interpreter set to Python 2 path: {s}"
                detail["interpreter"] = s
                if prov.defining_node_id and prov.defining_node_id != node_id:
                    detail["defined_at"] = prov.defining_node_id
                    detail["source"] = prov.source.value
                    if prov.file_path:
                        detail["defined_in_file"] = prov.file_path
                    if prov.line:
                        detail["defined_at_line"] = prov.line
                return GraphRuleResult(
                    verdict=True,
                    detail=detail,
                    node_id=node_id,
                    file=(node.file_path, node.line_start),
                )

        return GraphRuleResult(
            verdict=False,
            detail={},
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
