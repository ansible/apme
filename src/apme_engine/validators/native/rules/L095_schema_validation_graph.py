"""GraphRule L095: YAML file does not match expected schema structure.

Stub: ``match()`` returns False until play/collection nodes expose
``play_data`` and ``metadata`` dicts for schema validation.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class SchemaValidationGraphRule(GraphRule):
    """Flag YAML files that do not match expected schema structure.

    Currently a stub -- ``match()`` always returns False because
    the required ``play_data`` and ``metadata`` attributes are not
    yet available on ContentNode.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L095"
    description: str = "YAML file does not match expected schema structure"
    enabled: bool = True
    name: str = "SchemaValidation"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """No-op until play/collection nodes expose schema data.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            Always False.
        """
        return False

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Placeholder -- never called while ``match`` returns False.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            None.
        """
        return None
