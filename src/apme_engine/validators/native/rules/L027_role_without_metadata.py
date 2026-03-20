"""Native rule L027: detect roles used without metadata."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class RoleWithoutMetadataRule(Rule):
    """Rule for roles used without metadata.

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

    rule_id: str = "L027"
    description: str = "A role without metadata is used"
    enabled: bool = True
    name: str = "RoleWithoutMetadata"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Return True if the current target is a role.

        Args:
            ctx: Current Ansible run context.

        Returns:
            True if the current target is a role.

        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Role)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Check for roles without metadata and return verdict.

        Args:
            ctx: Current Ansible run context.

        Returns:
            RuleResult with verdict if role lacks metadata, None otherwise.

        """
        role = ctx.current
        if role is None:
            return None

        verdict = not getattr(role.spec, "metadata", None)

        return RuleResult(
            verdict=verdict,
            file=cast("tuple[str | int, ...] | None", role.file_info()),
            rule=self.get_metadata(),
        )
