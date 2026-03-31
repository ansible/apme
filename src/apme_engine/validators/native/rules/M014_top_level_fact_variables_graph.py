r"""GraphRule M014: top-level fact variables — use ansible_facts[\"name\"] (removed in 2.24).

Graph-aware port of ``M014_top_level_fact_variables.py``.
"""

import re
from dataclasses import dataclass
from typing import cast

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

MAGIC_VARS = frozenset(
    {
        "ansible_check_mode",
        "ansible_diff_mode",
        "ansible_forks",
        "ansible_play_batch",
        "ansible_play_hosts",
        "ansible_play_hosts_all",
        "ansible_play_name",
        "ansible_play_role_names",
        "ansible_role_names",
        "ansible_run_tags",
        "ansible_skip_tags",
        "ansible_version",
        "ansible_loop",
        "ansible_loop_var",
        "ansible_index_var",
        "ansible_parent_role_names",
        "ansible_parent_role_paths",
        "ansible_facts",
        "ansible_local",
        "ansible_verbosity",
        "ansible_config_file",
        "ansible_connection",
        "ansible_become",
        "ansible_become_method",
    }
)

_ANSIBLE_VAR = re.compile(r"\b(ansible_\w+)\b")


@dataclass
class TopLevelFactVariablesGraphRule(GraphRule):
    """Detect injected ansible_* fact variables that will break in 2.24.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M014"
    description: str = 'Use ansible_facts["name"] instead of injected ansible_* fact variables (removed in 2.24)'
    enabled: bool = True
    name: str = "TopLevelFactVariables"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task or handler nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a task or handler.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type in _TASK_TYPES

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Scan Jinja2 expressions for deprecated top-level fact variables.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with ``found_facts``, ``suggestions``, and ``message`` when violated;
            else pass.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        yaml_lines = getattr(node, "yaml_lines", "") or ""
        options = getattr(node, "options", None) or {}
        module_options = getattr(node, "module_options", None) or {}
        all_text_parts = [yaml_lines]
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text_parts.append(v)
        text = " ".join(all_text_parts)
        found: set[str] = set()
        for m in _ANSIBLE_VAR.finditer(text):
            varname = m.group(1)
            if varname not in MAGIC_VARS and varname.startswith("ansible_"):
                found.add(varname)
        verdict = len(found) > 0
        detail: YAMLDict | None = None
        if found:
            suggestions = {v: f'ansible_facts["{v.removeprefix("ansible_")}"]' for v in sorted(found)}
            detail = cast(
                YAMLDict,
                {
                    "message": f"Top-level fact variable(s) {', '.join(sorted(found))} removed in 2.24",
                    "found_facts": sorted(found),
                    "suggestions": suggestions,
                },
            )
        return GraphRuleResult(
            verdict=verdict,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
