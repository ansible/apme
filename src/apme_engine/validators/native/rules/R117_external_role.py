"""Native rule R117: detect external role usage."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    RoleCall,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
)
from apme_engine.engine.models import RuleTag as Tag


@dataclass
class ExternalRoleRuleResult(RuleResult):
    """Result subclass for ExternalRoleRule."""


@dataclass
class ExternalRoleRule(Rule):
    """Rule for external role usage.

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

    rule_id: str = "R117"
    description: str = "An external role is used"
    enabled: bool = True
    name: str = "ExternalRole"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE
    result_type: type[RuleResult] = ExternalRoleRuleResult

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a role target.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if current target is a role.
        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Role)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Check for external role and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with verdict, or None.
        """
        role = ctx.current
        if role is None:
            return None

        spec_metadata = getattr(role.spec, "metadata", None)
        verdict = bool(
            not ctx.is_begin(role)
            and isinstance(role, RoleCall)
            and spec_metadata
            and isinstance(spec_metadata, dict)
            and spec_metadata.get("galaxy_info", None)
        )

        return RuleResult(
            verdict=verdict,
            file=cast("tuple[str | int, ...] | None", role.file_info()),
            rule=self.get_metadata(),
        )
