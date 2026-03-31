"""GraphRule R104: detect network transfer from unauthorized source."""

import re
from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.validators.native.rules._module_risk_mapping import (
    get_risk_profile,
    resolve_field,
)
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})

_ALLOW_URL_LIST = ["https://*"]
_DENY_URL_LIST = ["http://*"]


@dataclass
class InvalidDownloadSourceGraphRule(GraphRule):
    """Flag inbound-transfer tasks whose source URL is not allowlisted.

    By default, ``https://`` sources are allowed and ``http://`` sources
    are denied.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R104"
    description: str = "A network transfer from unauthorized source is found."
    enabled: bool = True
    name: str = "InvalidDownloadSource"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.NETWORK,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use an inbound-transfer module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has an ``inbound`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.resolved_module_name, node.module)
        return profile is not None and profile.risk_type == "inbound"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag when the source URL fails the allow/deny check.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult, or None if node is missing.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        profile = get_risk_profile(node.resolved_module_name, node.module)
        if profile is None:
            return None

        mo = node.module_options
        if not isinstance(mo, dict):
            mo = {}

        src = resolve_field(mo, profile, "src")

        if src is None or _is_allowed_url(src, _ALLOW_URL_LIST, _DENY_URL_LIST):
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {"invalid_src": src}
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )


def _is_allowed_url(src: str, allow_list: list[str], deny_list: list[str]) -> bool:
    """Check if a URL passes the allow/deny regex lists.

    When ``allow_list`` is non-empty the URL must match at least one
    allow pattern.  Otherwise, the URL must not match any deny pattern.
    Patterns are Python regex strings passed to ``re.match()``.

    Args:
        src: URL to check.
        allow_list: Allowed URL regex patterns.
        deny_list: Denied URL regex patterns.

    Returns:
        True if the URL is allowed.
    """
    if allow_list:
        return any(re.match(pattern, src) for pattern in allow_list)
    if deny_list:
        return not any(re.match(pattern, src) for pattern in deny_list)
    return True
