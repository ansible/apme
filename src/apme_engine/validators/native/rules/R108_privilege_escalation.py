"""Native rule R108: detect privilege escalation."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
    TaskCall,
    YAMLDict,
)
from apme_engine.engine.models import RuleTag as Tag


@dataclass
class PrivilegeEscalationRule(Rule):
    """Rule for privilege escalation (become) usage.

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

    rule_id: str = "R108"
    description: str = "Privilege escalation is found"
    enabled: bool = True
    name: str = "PrivilegeEscalation"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.SYSTEM,)
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
        """Check for privilege escalation and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with become detail, or None.
        """
        task = ctx.current
        if task is None or not isinstance(task, TaskCall):
            return None

        verdict = bool(task.become and task.become.enabled)
        detail = {}
        if verdict:
            detail = task.become.__dict__

        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
