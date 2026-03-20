"""Native rule L055: detect role meta video_links with invalid URLs."""

import re
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

URL_PATTERN = re.compile(r"^https?://\S+$")


@dataclass
class MetaVideoLinksRule(Rule):
    """Rule for role meta video_links to be valid URLs.

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

    rule_id: str = "L055"
    description: str = "Role meta video_links should be valid URLs"
    enabled: bool = True
    name: str = "MetaVideoLinks"
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
        """Check video_links validity and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with invalid_links detail, or None.
        """
        role = ctx.current
        if role is None:
            return None
        metadata = getattr(role.spec, "metadata", None) or {}
        gi = metadata.get("galaxy_info")
        galaxy_info = gi if isinstance(gi, dict) else {}
        video_links = galaxy_info.get("video_links") if galaxy_info else None
        if not video_links:
            return RuleResult(
                verdict=False,
                file=cast("tuple[str | int, ...] | None", role.file_info()),
                rule=self.get_metadata(),
            )
        if not isinstance(video_links, list):
            return RuleResult(
                verdict=True,
                detail={"message": "video_links must be a list"},
                file=cast("tuple[str | int, ...] | None", role.file_info()),
                rule=self.get_metadata(),
            )
        invalid = [u for u in video_links if not (isinstance(u, str) and URL_PATTERN.match(u.strip()))]
        verdict = len(invalid) > 0
        detail: dict[str, str | list[str]] = {}
        if invalid:
            detail["invalid_links"] = [str(u) for u in invalid[:10]]
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", role.file_info()),
            rule=self.get_metadata(),
        )
