"""GraphRule L085: Role include paths with Jinja should reference ``role_path``."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_ROLE_PATH_REF = re.compile("role_path")

INCLUDE_MODULES = frozenset(
    {
        "include_tasks",
        "include_vars",
        "include_role",
        "ansible.builtin.include_tasks",
        "ansible.builtin.include_vars",
        "ansible.builtin.include_role",
        "ansible.legacy.include_tasks",
        "ansible.legacy.include_vars",
        "ansible.legacy.include_role",
    }
)


@dataclass
class RolePathIncludeGraphRule(GraphRule):
    """Flag dynamic include paths in roles that omit ``role_path``.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L085"
    description: str = "Use explicit role_path prefix in include paths within roles"
    enabled: bool = True
    name: str = "RolePathInclude"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match include or import tasks and handlers under a role path.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler under ``/roles/`` whose
            module is a known include or import action.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        fp = node.file_path or ""
        if "/roles/" not in fp and not fp.startswith("roles/"):
            return False
        resolved = node.module
        return resolved in INCLUDE_MODULES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report a violation when a Jinja include path omits ``role_path``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when the path contains
            Jinja but not ``role_path``, ``verdict`` False otherwise, or None if
            the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}
        raw = mo.get("file")
        if raw is None or raw == "":
            raw = mo.get("_raw_params")
        if isinstance(raw, str):
            src = raw
        elif raw is not None:
            src = str(raw)
        else:
            src = ""
        if "{{" not in src or _ROLE_PATH_REF.search(src):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        detail: YAMLDict = {"include_path": src}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
