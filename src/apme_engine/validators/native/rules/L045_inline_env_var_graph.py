"""GraphRule L045: inline task environment variables.

Graph-aware port of ``L045_inline_env_var.py``.  Matches tasks whose effective
environment comes from the task or an ancestor scope and attributes inherited
definitions in the violation detail.
"""

from dataclasses import dataclass
from typing import TypeGuard

from apme_engine.engine.content_graph import ContentGraph, NodeType
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity, YAMLDict
from apme_engine.engine.variable_provenance import VariableProvenanceResolver
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult

_TASK_TYPES = frozenset({NodeType.TASK, NodeType.HANDLER})


def _environment_truthy(value: object) -> TypeGuard[YAMLDict]:
    """Return True when ``value`` is a non-empty environment mapping.

    Args:
        value: Candidate environment value from a node or property origin.

    Returns:
        True if ``value`` is a dict with at least one entry.
    """
    return isinstance(value, dict) and bool(value)


@dataclass
class InlineEnvVarGraphRule(GraphRule):
    """Discourage inline ``environment`` on tasks (including inherited scopes).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L045"
    description: str = "Avoid inline environment variables in tasks; use env file or role vars"
    enabled: bool = True
    name: str = "InlineEnvVar"
    version: str = "v0.0.2"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match tasks or handlers that have a non-empty effective environment.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when ``environment`` is set on the node or inherited from an ancestor.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        if node.node_type not in _TASK_TYPES:
            return False
        if _environment_truthy(node.environment):
            return True
        resolver = VariableProvenanceResolver(graph)
        origins = resolver.resolve_property_origins(node_id)
        env_origin = origins.get("environment")
        return env_origin is not None and _environment_truthy(env_origin.value)

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Report inline environment usage with optional inheritance attribution.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Graph rule result with environment detail and ``verdict`` True.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        resolver = VariableProvenanceResolver(graph)
        origins = resolver.resolve_property_origins(node_id)
        env_origin = origins.get("environment")

        env_map: YAMLDict = {}
        if _environment_truthy(node.environment):
            env_map = dict(node.environment)
        elif env_origin is not None and _environment_truthy(env_origin.value):
            env_map = dict(env_origin.value)

        detail: YAMLDict = {
            "message": "task uses inline environment; consider env file or variables",
            "environment": env_map,
        }
        if env_origin is not None and env_origin.defining_node_id != node_id:
            detail["inherited_from"] = env_origin.defining_node_id
            detail["defined_in_file"] = env_origin.file_path

        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
