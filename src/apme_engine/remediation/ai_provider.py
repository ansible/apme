"""AIProvider protocol and data models for graph-native AI remediation.

See ADR-024 for the rationale behind this abstraction and ADR-044
for the graph-native transform design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from apme_engine.remediation.ai_context import AINodeContext


class AIProviderError(RuntimeError):
    """AI infrastructure failed before it could return a remediation decision."""


@dataclass
class AISkipped:
    """A violation the AI could not fix, with an explanation.

    Attributes:
        rule_id: Rule ID that was not fixed.
        line: 1-based line number of the violation.
        reason: Why the AI could not fix it.
        suggestion: Manual remediation guidance for the user.
    """

    rule_id: str
    line: int
    reason: str
    suggestion: str


@dataclass
class AINodeFix:
    """AI-generated fix for a single graph node.

    Returned by ``AIProvider.propose_node_fix()``.  Contains the
    corrected YAML text that will be applied via
    ``ContentGraph.apply_transform()``.

    Attributes:
        fixed_snippet: Corrected YAML text for the node.
        rule_ids: Rule IDs addressed by this fix.
        explanation: Human-readable summary of changes.
        confidence: Confidence score (0.0-1.0).
        skipped: Violations the AI could not fix.
    """

    fixed_snippet: str
    rule_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    confidence: float = 0.85
    skipped: list[AISkipped] = field(default_factory=list)


class AIValidationVerdict(str, Enum):
    """Outcome of AI-assisted validation for a contextual rule finding.

    Attributes:
        TRUE_POSITIVE: The finding is a real issue that should be addressed.
        FALSE_POSITIVE: The finding is expected/legitimate in context.
        UNCERTAIN: The AI could not determine with confidence.
    """

    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    UNCERTAIN = "uncertain"


@dataclass
class AIValidationResult:
    """AI assessment of whether a contextual violation is a true positive.

    Returned by ``AIValidationProvider.validate_finding()``.

    Attributes:
        rule_id: Rule ID being validated.
        verdict: Whether the finding is a true or false positive.
        confidence: Confidence score (0.0-1.0).
        reasoning: Explanation of why the AI reached this verdict.
        suggestion: Recommended action (e.g. add noqa, fix the issue).
        noqa_comment: The exact ``# noqa: <rule_id>`` comment to add if suppressing.
    """

    rule_id: str
    verdict: AIValidationVerdict
    confidence: float
    reasoning: str
    suggestion: str
    noqa_comment: str = ""


class AIProvider(Protocol):
    """Protocol for AI-powered fix proposal providers.

    The engine depends only on this protocol, never on a concrete
    LLM client library.  See ADR-024.

    The sole entry point is ``propose_node_fix()``, which receives
    graph-derived context and returns a node-level fix.
    """

    async def propose_node_fix(
        self,
        context: AINodeContext,
        *,
        model: str | None = None,
    ) -> AINodeFix | None:
        """Propose a fix for a single graph node using graph-derived context.

        The graph-native entry point for AI remediation.  Receives an
        ``AINodeContext`` containing the node's YAML, violations, parent
        context, and sibling snippets — all derived from the ContentGraph.
        Returns an ``AINodeFix`` with the corrected YAML, or ``None``
        if the AI cannot fix any of the violations.

        Infrastructure failures should raise ``AIProviderError`` instead of
        reporting that the provider made a decision to abstain.

        Args:
            context: Graph-derived context bundle for this node.
            model: Optional model identifier.

        Returns:
            ``AINodeFix`` with corrected YAML, or ``None`` when no fix is available.

        """
        ...


@runtime_checkable
class AIValidationProvider(Protocol):
    """Protocol for AI-assisted finding validation.

    Extends the AI remediation concept to **validation** — the AI reviews
    contextual findings (e.g. R108 privilege escalation) to determine
    whether they are true or false positives.  If false positive, it
    suggests adding ``# noqa: <rule_id>`` to suppress the finding.
    """

    async def validate_finding(
        self,
        context: AINodeContext,
        *,
        model: str | None = None,
    ) -> AIValidationResult | None:
        """Validate whether a contextual finding is a true positive.

        Uses the task's YAML, parent context (become, vars), and
        surrounding siblings to determine if the violation is legitimate.

        Args:
            context: Graph-derived context with the single violation to validate.
            model: Optional model identifier.

        Returns:
            ``AIValidationResult`` with verdict and reasoning, or ``None`` on failure.
        """
        ...
