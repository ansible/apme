"""M027: Mixing inline k=v arguments with args: mapping is deprecated (2.23)."""

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

_KV_INLINE = re.compile(r"\w+=\S")


@dataclass
class LegacyKvMergedWithArgsRule(Rule):
    """Detect tasks that mix inline k=v args with an args: mapping.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M027"
    description: str = "Mixing inline k=v arguments with args: mapping is deprecated (2.23)"
    enabled: bool = True
    name: str = "LegacyKvMergedWithArgs"
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
        """Check for inline k=v + args: mapping in the same task.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            RuleResult with verdict and violation details, or None if the task is unavailable.

        """
        task = ctx.current
        if task is None:
            return None

        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}

        has_args_key = "args" in options and isinstance(options.get("args"), dict) and bool(options["args"])

        has_inline_kv = False
        raw = module_options.get("_raw_params", "")
        if isinstance(raw, str) and _KV_INLINE.search(raw):
            has_inline_kv = True
        else:
            for val in module_options.values():
                if isinstance(val, str) and _KV_INLINE.search(val):
                    has_inline_kv = True
                    break

        verdict = has_args_key and has_inline_kv
        detail: dict[str, object] = {}
        if verdict:
            detail["message"] = (
                "Inline k=v args merged with args: mapping is deprecated"
                " in 2.23; move all params into args: or module key"
            )
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
