"""M019: !!omap / !!pairs YAML tags are deprecated (2.23).

Standard YAML mappings preserve insertion order in Python 3.7+.
"""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RunTargetType,
    Severity,
    YAMLDict,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class OmapPairsYamlTagsRule(Rule):
    """Detect !!omap and !!pairs YAML tags in content.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M019"
    description: str = "!!omap and !!pairs YAML tags are deprecated; use plain mappings (2.23)"
    enabled: bool = True
    name: str = "OmapPairsYamlTags"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a task target.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            True if this rule should evaluate the given context.

        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Task)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Scan raw YAML for !!omap or !!pairs tags.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            RuleResult with verdict and violation details, or None if the task is unavailable.

        """
        task = ctx.current
        if task is None:
            return None

        yaml_lines = getattr(task.spec, "yaml_lines", "") or ""
        found_tags = []
        if "!!omap" in yaml_lines:
            found_tags.append("!!omap")
        if "!!pairs" in yaml_lines:
            found_tags.append("!!pairs")

        verdict = len(found_tags) > 0
        detail: dict[str, object] = {}
        if found_tags:
            detail["message"] = f"Deprecated YAML tag(s): {', '.join(found_tags)}; dicts are ordered in Python 3.7+"
            detail["tags"] = found_tags
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
