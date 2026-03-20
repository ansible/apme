"""Native rule L060: detect lines exceeding maximum length."""

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

DEFAULT_MAX_LINE_LENGTH = 160


@dataclass
class LineLengthRule(Rule):
    """Rule for detecting lines exceeding the configured maximum length.

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

    rule_id: str = "L060"
    description: str = "Line too long"
    enabled: bool = True
    name: str = "LineLength"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
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
        """Check for lines exceeding max length and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with long_lines detail, or None.
        """
        task = ctx.current
        if task is None:
            return None
        yaml_lines = getattr(task.spec, "yaml_lines", "") or ""
        long_lines: list[dict[str, int]] = []
        for i, line in enumerate(yaml_lines.splitlines(), start=1):
            if len(line) > DEFAULT_MAX_LINE_LENGTH:
                long_lines.append({"line": i, "length": len(line)})
        verdict = len(long_lines) > 0
        detail: dict[str, object] = {}
        if long_lines:
            detail["long_lines"] = long_lines
            detail["max_length"] = DEFAULT_MAX_LINE_LENGTH
            detail["message"] = f"line too long (>{DEFAULT_MAX_LINE_LENGTH} characters)"
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
