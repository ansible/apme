"""Native rule L052: detect galaxy version not following semantic version format."""

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

GALAXY_VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")


@dataclass
class GalaxyVersionIncorrectRule(Rule):
    """Rule for galaxy version in meta to follow semantic version format.

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

    rule_id: str = "L052"
    description: str = "Galaxy version in meta should follow semantic version format (x.y.z)"
    enabled: bool = True
    name: str = "GalaxyVersionIncorrect"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
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
        """Check galaxy version format and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with version/message detail, or None.
        """
        role = ctx.current
        if role is None:
            return None
        metadata = getattr(role.spec, "metadata", None) or {}
        gi = metadata.get("galaxy_info")
        galaxy_info = gi if isinstance(gi, dict) else {}
        version = galaxy_info.get("version") if galaxy_info else None
        if version is None:
            return RuleResult(
                verdict=False,
                file=cast("tuple[str | int, ...] | None", role.file_info()),
                rule=self.get_metadata(),
            )
        vs = str(version).strip()
        verdict = not bool(GALAXY_VERSION_PATTERN.match(vs))
        detail = {}
        if verdict:
            detail["version"] = vs
            detail["message"] = "galaxy version should be semantic (e.g. 1.0.0)"
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", role.file_info()),
            rule=self.get_metadata(),
        )
