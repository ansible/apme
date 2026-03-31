"""GraphRule L090: plugin entry files should be small.

Stub: ``match()`` returns False until plugin/module nodes expose
Python source content.
"""

from dataclasses import dataclass

from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.models import RuleTag as Tag
from apme_engine.engine.models import Severity
from apme_engine.validators.native.rules.graph_rule_base import GraphRule, GraphRuleResult


@dataclass
class PluginFileSizeGraphRule(GraphRule):
    """Flag plugin entry files that are too large.

    Currently a stub -- ``match()`` always returns False because
    plugin/module nodes do not yet expose Python source content.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L090"
    description: str = "Plugin entry files should be small; move helpers to module_utils"
    enabled: bool = True
    name: str = "PluginFileSize"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """No-op until plugin nodes expose Python source content.

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
