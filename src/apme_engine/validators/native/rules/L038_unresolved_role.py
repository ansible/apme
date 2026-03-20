"""Native rule L038: detect unresolved role references."""

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
    ExecutableType as ActionType,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class UnresolvedRoleRuleResult(RuleResult):
    """Result subclass for UnresolvedRoleRule."""


@dataclass
class UnresolvedRoleRule(Rule):
    """Rule for unresolved role references.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        result_type: Type of RuleResult to produce.
    """

    rule_id: str = "L038"
    description: str = "Unresolved role is found"
    enabled: bool = True
    name: str = "UnresolvedRole"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE
    result_type: type = UnresolvedRoleRuleResult

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
        """Check for unresolved role and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with role detail, or None.
        """
        task = ctx.current
        if task is None:
            return None

        action_type = getattr(task, "action_type", "")
        spec_action = getattr(task.spec, "action", None)
        resolved_action = getattr(task, "resolved_action", "")
        verdict = bool(action_type == ActionType.ROLE_TYPE and spec_action and not resolved_action)
        detail = {
            "role": spec_action or "",
        }

        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
