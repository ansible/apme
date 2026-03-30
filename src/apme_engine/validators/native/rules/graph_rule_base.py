"""GraphRule — base class for rules consuming ContentGraph (ADR-044).

In Phase 2, all native rules are ported from ``Rule`` (which consumes
``AnsibleRunContext``) to ``GraphRule`` (which consumes ``ContentGraph``
directly).  This base class is built in Phase 1 so the interface is
stable before porting begins.

Interface contract
------------------
- ``match(graph, node_id)`` → bool: does this rule apply to this node?
- ``process(graph, node_id)`` → GraphRuleResult | None: evaluate the rule

Rules receive the full graph so they can query ancestors, siblings,
descendants, property origins, and variable provenance — capabilities
that were impossible with the flat ``AnsibleRunContext`` sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from apme_engine.engine.models import RuleMetadata, YAMLDict

if TYPE_CHECKING:
    from apme_engine.engine.content_graph import ContentGraph


@dataclass
class GraphRuleResult:
    """Result of applying a GraphRule to a single node.

    Attributes:
        rule: Metadata for the rule that produced this result.
        verdict: True if the node passes (no violation).
        detail: Additional details for the violation message.
        node_id: ContentGraph node ID that was evaluated.
        file: File path and line info.
        error: Error message if evaluation failed.
    """

    rule: RuleMetadata | None = None
    verdict: bool = False
    detail: YAMLDict | None = None
    node_id: str = ""
    file: tuple[str | int, ...] | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Return True when the rule check passed."""
        return self.verdict

    @property
    def failed(self) -> bool:
        """Return True when the rule check failed."""
        return not self.verdict


@dataclass
class GraphRule(RuleMetadata):
    """Base class for ContentGraph-aware rules.

    Subclasses must implement ``match`` and ``process``.  The rule
    receives the full ContentGraph so it can query relationships,
    ancestry, and provenance — not just the current node.

    Attributes:
        enabled: Whether the rule is enabled.
        precedence: Evaluation order (lower = earlier).
    """

    enabled: bool = False
    precedence: int = 10

    def __post_init__(self) -> None:
        """Validate that rule_id and description are non-empty.

        Raises:
            ValueError: If rule_id or description is empty after init.
        """
        if not self.rule_id:
            raise ValueError("A rule must have a unique rule_id")
        if not self.description:
            raise ValueError("A rule must have a description")

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Check if this rule applies to a specific node.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the rule should be evaluated for this node.

        Raises:
            NotImplementedError: Subclasses must override.
        """
        raise NotImplementedError

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Evaluate the rule against a specific node.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with verdict and detail, or None if not applicable.

        Raises:
            NotImplementedError: Subclasses must override.
        """
        raise NotImplementedError
