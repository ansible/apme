"""M030: Broken conditional expressions will error in 2.23.

Attempts to parse when: values as Jinja2 expressions and flags
those that fail to parse.
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

try:
    from jinja2 import Environment as _Env  # type: ignore[import-not-found]

    _JINJA_ENV = _Env()
    HAS_JINJA = True
except ImportError:
    HAS_JINJA = False
    _JINJA_ENV = None


@dataclass
class BrokenConditionalExpressionsRule(Rule):
    """Detect when: conditions that fail Jinja2 expression parsing.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M030"
    description: str = "Broken conditional expressions will error in 2.23"
    enabled: bool = True
    name: str = "BrokenConditionalExpressions"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a task target and Jinja2 is available.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if the current target is a task and Jinja2 is installed.
        """
        if not HAS_JINJA:
            return False
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Task)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Parse when: value as a Jinja2 expression.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with detail, or None if there is no current task.
        """
        task = ctx.current
        if task is None:
            return None

        options = getattr(task.spec, "options", None) or {}
        when_val = options.get("when")
        if when_val is None or when_val == "":
            return RuleResult(
                verdict=False,
                detail=cast(YAMLDict | None, {}),
                file=cast("tuple[str | int, ...] | None", task.file_info()),
                rule=self.get_metadata(),
            )

        when_list = when_val if isinstance(when_val, list) else [when_val]
        broken = []
        for cond in when_list:
            if not isinstance(cond, str):
                continue
            cond = cond.strip()
            if not cond:
                continue
            try:
                _JINJA_ENV.parse("{{ " + cond + " }}")
            except Exception:
                broken.append(cond)

        verdict = len(broken) > 0
        detail: dict[str, object] = {}
        if broken:
            detail["message"] = f"Broken conditional(s) will error in 2.23: {broken}"
            detail["broken_conditions"] = broken
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
