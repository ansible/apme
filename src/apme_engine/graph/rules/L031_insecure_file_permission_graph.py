"""GraphRule L031: file permission may be insecure.

Complements OPA L020 (numeric mode) and L021 (missing mode) by detecting
**insecure permission values** — world-writable bits, overly permissive
patterns like 0777/0666, and non-string modes that could silently produce
wrong permissions.

The rule checks ``mode`` in ``module_options`` for file-related modules.
Templated values (Jinja) are skipped since the actual permission cannot
be determined statically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
    is_templated,
)
from apme_engine.graph.types import RuleTag as Tag
from apme_engine.graph.types import Severity, YAMLDict

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_FILE_PERMISSION_MODULES = frozenset(
    {
        "ansible.builtin.copy",
        "ansible.builtin.file",
        "ansible.builtin.template",
        "ansible.builtin.lineinfile",
        "ansible.builtin.replace",
        "ansible.builtin.synchronize",
        "ansible.builtin.unarchive",
        "ansible.builtin.assemble",
        "ansible.legacy.copy",
        "ansible.legacy.file",
        "ansible.legacy.template",
        "ansible.legacy.lineinfile",
        "ansible.legacy.replace",
        "ansible.legacy.synchronize",
        "ansible.legacy.unarchive",
        "copy",
        "file",
        "template",
        "lineinfile",
        "replace",
        "synchronize",
        "unarchive",
        "assemble",
    }
)

_OCTAL_RE = re.compile(r"^0?[0-7]{3,4}$")

_INSECURE_MODES = frozenset(
    {
        "0777",
        "777",
        "0666",
        "666",
        "0776",
        "776",
        "0767",
        "767",
        "0677",
        "677",
    }
)


def _world_writable(mode_str: str) -> bool:
    """Return True if the octal mode grants world-write (o+w).

    Args:
        mode_str: Octal mode string (e.g. "0755", "644").

    Returns:
        True when the "others" octal digit includes the write bit.
    """
    digits = mode_str.lstrip("0") or "0"
    if len(digits) < 1:
        return False
    try:
        others = int(digits[-1])
    except ValueError:
        return False
    return bool(others & 0o2)


def _check_mode(mode_value: object) -> tuple[bool, str]:
    """Evaluate a mode value for insecure patterns.

    Args:
        mode_value: The ``mode`` parameter from module_options.

    Returns:
        Tuple of (is_insecure, reason). When not insecure, reason is empty.
    """
    if mode_value is None:
        return False, ""

    if isinstance(mode_value, bool):
        return True, f"mode is boolean ({mode_value}); use octal string like '0644'"

    if isinstance(mode_value, int):
        octal_str = oct(mode_value).replace("0o", "0")
        if octal_str in _INSECURE_MODES:
            return True, f"mode {mode_value} (octal {octal_str}) is overly permissive"
        if _world_writable(octal_str):
            return True, f"mode {mode_value} (octal {octal_str}) is world-writable"
        return False, ""

    mode_str = str(mode_value).strip()
    if is_templated(mode_str):
        return False, ""

    if not _OCTAL_RE.match(mode_str):
        return False, ""

    if mode_str in _INSECURE_MODES:
        return True, f"mode '{mode_str}' is overly permissive"
    if _world_writable(mode_str):
        return True, f"mode '{mode_str}' is world-writable"
    return False, ""


@dataclass
class InsecureFilePermissionGraphRule(GraphRule):
    """Flag file tasks with insecure permission values.

    Detects world-writable modes, overly permissive patterns (0777, 0666),
    and boolean mode values on file-related modules.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L031"
    description: str = "File permission may be insecure"
    enabled: bool = True
    name: str = "InsecureFilePermission"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SECURITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match file-related tasks that set a mode parameter.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a file task with a ``mode`` option.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        if node.module not in _FILE_PERMISSION_MODULES:
            return False
        mo = node.module_options if isinstance(node.module_options, dict) else {}
        return "mode" in mo

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Check if the mode value is insecure.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult; verdict True when an insecure mode is detected.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        mo = node.module_options if isinstance(node.module_options, dict) else {}
        mode_value = mo.get("mode")
        is_insecure, reason = _check_mode(mode_value)

        if not is_insecure:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"message": reason}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
