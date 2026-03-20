"""Native rule L054: detect role meta galaxy_info missing galaxy_tags."""

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


@dataclass
class MetaNoTagsRule(Rule):
    """Rule for role meta galaxy_info to include galaxy_tags.

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

    rule_id: str = "L054"
    description: str = "Role meta galaxy_info should include galaxy_tags"
    enabled: bool = True
    name: str = "MetaNoTags"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.ROLE

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
        """Check galaxy_tags presence and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with message detail, or None.
        """
        role = ctx.current
        if role is None:
            return None
        metadata = getattr(role.spec, "metadata", None) or {}
        gi = metadata.get("galaxy_info")
        galaxy_info = gi if isinstance(gi, dict) else {}
        if not galaxy_info:
            return RuleResult(
                verdict=False,
                file=cast("tuple[str | int, ...] | None", role.file_info()),
                rule=self.get_metadata(),
            )
        tags = galaxy_info.get("galaxy_tags") or galaxy_info.get("categories")
        verdict = not tags or (isinstance(tags, list) and len(tags) == 0)
        detail = {}
        if verdict:
            detail["message"] = "galaxy_info should include galaxy_tags or categories"
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", role.file_info()),
            rule=self.get_metadata(),
        )
