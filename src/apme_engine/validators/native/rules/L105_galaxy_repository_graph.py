"""GraphRule L105: galaxy.yml should have a repository key.

Stub: ``match()`` returns False until COLLECTION nodes expose
parsed ``galaxy.yml`` metadata.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class GalaxyRepositoryGraphRule(GraphRule):
    """Flag collections whose ``galaxy.yml`` is missing a ``repository`` key.

    Currently a stub -- ``match()`` always returns False because
    COLLECTION nodes do not yet expose parsed ``galaxy.yml`` metadata.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L105"
    description: str = "galaxy.yml should have a repository key"
    enabled: bool = True
    name: str = "GalaxyRepository"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """No-op until COLLECTION nodes expose galaxy.yml metadata.

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
