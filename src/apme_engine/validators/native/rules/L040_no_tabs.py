"""Native rule L040: detect YAML containing tabs instead of spaces."""

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
class NoTabsRule(Rule):
    """Rule for YAML containing tabs instead of spaces.

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

    rule_id: str = "L040"
    description: str = "YAML should not contain tabs; use spaces"
    enabled: bool = True
    name: str = "NoTabs"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.PLAYBOOK

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
        """Check for tabs in YAML and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with lines_with_tabs detail, or None.
        """
        task = ctx.current
        if task is None:
            return None
        yaml_lines = getattr(task.spec, "yaml_lines", "") or ""
        lines_with_tabs = []
        for i, line in enumerate(yaml_lines.splitlines(), start=1):
            if "\t" in line:
                lines_with_tabs.append(i)
        verdict = len(lines_with_tabs) > 0
        detail = {}
        if lines_with_tabs:
            detail["lines_with_tabs"] = lines_with_tabs
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
