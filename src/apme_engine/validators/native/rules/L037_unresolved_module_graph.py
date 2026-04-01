"""GraphRule L037: Detect unresolved module references on task and handler nodes."""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _module_looks_resolved(mod: str) -> bool:
    """Return whether ``mod`` looks like an Ansible FQCN (``namespace.collection.name``).

    Args:
        mod: Declared module string from the task node.

    Returns:
        True when ``mod`` has at least three dot-separated segments.
    """
    return len(mod.split(".")) >= 3


_INCLUDE_IMPORT_ACTIONS = frozenset(
    {
        "include",
        "include_tasks",
        "import_tasks",
        "include_role",
        "import_role",
        "include_vars",
        "ansible.builtin.include",
        "ansible.builtin.include_tasks",
        "ansible.builtin.import_tasks",
        "ansible.builtin.include_role",
        "ansible.builtin.import_role",
        "ansible.builtin.include_vars",
        "ansible.legacy.include",
        "ansible.legacy.include_tasks",
        "ansible.legacy.import_tasks",
        "ansible.legacy.include_role",
        "ansible.legacy.import_role",
        "ansible.legacy.include_vars",
    }
)


@dataclass
class UnresolvedModuleGraphRule(GraphRule):
    """Flag tasks whose module name did not resolve to a known module.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L037"
    description: str = "Unresolved module is found"
    enabled: bool = False
    name: str = "UnresolvedModule"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes with a module string but empty resolution.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler, ``module`` is non-empty,
            does not look like a resolved FQCN, and the module is not a dynamic
            include or import action name.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        mod = (node.module or "").strip()
        if not mod or _module_looks_resolved(mod):
            return False
        return mod not in _INCLUDE_IMPORT_ACTIONS

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when the module reference is unresolved.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True and ``module`` detail when
            unresolved, ``verdict`` False when resolved or excluded, or None if
            the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mod = (node.module or "").strip()
        if not mod or _module_looks_resolved(mod):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        if mod in _INCLUDE_IMPORT_ACTIONS:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {"module": mod}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
