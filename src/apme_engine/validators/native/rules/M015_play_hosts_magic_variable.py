"""M015: play_hosts magic variable is deprecated (removed in 2.23).

Use ansible_play_batch instead.
"""

import re
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

_PLAY_HOSTS_REF = re.compile(r"\bplay_hosts\b")


@dataclass
class PlayHostsMagicVariableRule(Rule):
    """Detect deprecated play_hosts variable usage.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M015"
    description: str = "Use ansible_play_batch instead of deprecated play_hosts variable (removed in 2.23)"
    enabled: bool = True
    name: str = "PlayHostsMagicVariable"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
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
        """Scan Jinja2 expressions for deprecated play_hosts variable.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            RuleResult with verdict and violation details, or None if the task is unavailable.

        """
        task = ctx.current
        if task is None:
            return None

        yaml_lines = getattr(task.spec, "yaml_lines", "") or ""
        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}

        all_text_parts = [yaml_lines]
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text_parts.append(v)
        text = " ".join(all_text_parts)

        found = bool(_PLAY_HOSTS_REF.search(text))
        detail: dict[str, object] = {}
        if found:
            detail["message"] = "play_hosts is deprecated in 2.23; use ansible_play_batch"
            detail["replacement"] = "ansible_play_batch"
        return RuleResult(
            verdict=found,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
