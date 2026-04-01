"""GraphRule L100: variable names must not be Python or Ansible keywords."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

SET_FACT_MODULES = frozenset(
    {
        "ansible.builtin.set_fact",
        "ansible.legacy.set_fact",
        "set_fact",
    }
)

INCLUDE_VARS_MODULES = frozenset(
    {
        "ansible.builtin.include_vars",
        "ansible.legacy.include_vars",
        "include_vars",
    }
)

PYTHON_KEYWORDS = frozenset(
    {
        "False",
        "None",
        "True",
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
    }
)

ANSIBLE_KEYWORDS = frozenset(
    {
        "gather_facts",
        "hosts",
        "roles",
        "tasks",
        "handlers",
        "vars",
        "vars_files",
        "vars_prompt",
        "pre_tasks",
        "post_tasks",
        "become",
        "become_user",
        "become_method",
        "connection",
        "environment",
        "strategy",
        "serial",
        "collections",
        "module_defaults",
        "no_log",
        "tags",
        "when",
        "register",
        "changed_when",
        "failed_when",
        "loop",
        "with_items",
        "with_dict",
        "with_list",
        "until",
        "retries",
        "delay",
        "block",
        "rescue",
        "always",
        "notify",
        "listen",
        "ignore_errors",
        "ignore_unreachable",
        "any_errors_fatal",
        "check_mode",
        "diff",
        "throttle",
        "run_once",
        "debugger",
        "name",
        "action",
        "args",
    }
)

BLOCKED_NAMES = PYTHON_KEYWORDS | ANSIBLE_KEYWORDS


@dataclass
class VarNamingKeywordGraphRule(GraphRule):
    """Detect variable names that are Python or Ansible keywords.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L100"
    description: str = "Variable names must not be Python or Ansible keywords"
    enabled: bool = True
    name: str = "VarNamingKeyword"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.VARIABLE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes for keyword variable checks.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report keyword collisions in set_fact, include_vars, or register.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with ``verdict`` True when collisions exist, or
            None if the node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        keyword_vars: list[str] = []
        resolved = node.module
        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        if resolved in SET_FACT_MODULES:
            for key in mo:
                if key in ("cacheable",):
                    continue
                if key in BLOCKED_NAMES:
                    keyword_vars.append(key)

        if resolved in INCLUDE_VARS_MODULES:
            var_name = mo.get("name")
            if isinstance(var_name, str) and var_name in BLOCKED_NAMES:
                keyword_vars.append(var_name)

        reg = node.register
        if reg and reg in BLOCKED_NAMES:
            keyword_vars.append(str(reg))

        if not keyword_vars:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = cast(YAMLDict, {"keyword_vars": keyword_vars})
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
