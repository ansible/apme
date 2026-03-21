"""Native rule L042: detect plays or blocks with high task count (complexity)."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
    YAMLDict,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)

# Default threshold: play/block with more than this many tasks may be considered complex
DEFAULT_TASK_COUNT_THRESHOLD = 20


@dataclass
class ComplexityRule(Rule):
    """Rule for play/block with high task count (complexity).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "L042"
    description: str = "Play or block has high task count (complexity)"
    enabled: bool = True
    name: str = "Complexity"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.PLAY

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a task target.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if current target is a task.
        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Task)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Check play/block complexity and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with task_count/threshold detail, or None.
        """
        task = ctx.current
        if task is None:
            return None
        sequence = getattr(ctx.sequence, "items", []) or []
        task_count = sum(1 for t in sequence if getattr(t, "type", "") == RunTargetType.Task)
        threshold = getattr(self, "task_count_threshold", None) or DEFAULT_TASK_COUNT_THRESHOLD
        verdict = task_count > threshold
        detail = {}
        if verdict:
            detail["task_count"] = task_count
            detail["threshold"] = threshold
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
