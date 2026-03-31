"""GraphRule R107: detect package install with insecure options."""

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


def _is_truthy(value: object) -> bool:
    """Return True when *value* represents a YAML boolean true.

    Handles booleans, strings (``"yes"``, ``"true"``, ``"1"``),
    and integers.

    Args:
        value: Raw value from ``module_options``.

    Returns:
        ``True`` when the value is truthy in Ansible's sense.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "yes", "1"}
    if isinstance(value, int):
        return value != 0
    return False


def _is_falsy(value: object) -> bool:
    """Return True when *value* represents a YAML boolean false.

    Args:
        value: Raw value from ``module_options``.

    Returns:
        ``True`` when the value is falsy in Ansible's sense.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value.lower() in {"false", "no", "0"}
    if isinstance(value, int):
        return value == 0
    return False


@dataclass
class InsecurePkgInstallGraphRule(GraphRule):
    """Flag package-install tasks that disable security checks.

    Triggers when ``validate_certs`` is false, ``disable_gpg_check``
    is true, or ``allow_downgrade`` is true on yum/dnf modules.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R107"
    description: str = "A package install with insecure options found"
    enabled: bool = True
    name: str = "InsecurePkgInstall"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.PACKAGE,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match task/handler nodes that use a package-install module.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node's module has a ``package_install`` risk profile.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type not in _TASK_TYPES:
            return False
        profile = get_risk_profile(node.resolved_module_name, node.module)
        return profile is not None and profile.risk_type == "package_install"

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag insecure package-install options.

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

        insecure = False
        vc = mo.get("validate_certs")
        if vc is not None and _is_falsy(vc):
            insecure = True
        gpg = mo.get("disable_gpg_check")
        if gpg is not None and _is_truthy(gpg):
            insecure = True
        ad = mo.get("allow_downgrade")
        if ad is not None and _is_truthy(ad):
            insecure = True

        pkg = resolve_field(mo, profile, "pkg")
        detail: YAMLDict = {"pkg": pkg} if pkg else {}

        return GraphRuleResult(
            verdict=insecure,
            detail=detail if insecure else None,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
