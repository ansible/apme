"""Re-export shim — canonical location is ``apme_engine.graph.rule_base`` (ADR-059)."""

from apme_engine.graph.rule_base import GraphRule as GraphRule  # noqa: F401
from apme_engine.graph.rule_base import GraphRuleResult as GraphRuleResult  # noqa: F401
from apme_engine.graph.rule_base import is_templated as is_templated  # noqa: F401
