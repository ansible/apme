"""M026: Invalid inventory variable names enforced in 2.23.

Variable names must be valid Python identifiers.
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
class InvalidInventoryVariableNamesRule(Rule):
    """Detect variable names that are not valid Python identifiers.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M026"
    description: str = "Inventory variable names must be valid Python identifiers (enforced in 2.23)"
    enabled: bool = True
    name: str = "InvalidInventoryVariableNames"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.VARIABLE,)

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
        """Check set_fact/vars keys for invalid Python identifiers.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            RuleResult with verdict and violation details, or None if the task is unavailable.

        """
        task = ctx.current
        if task is None:
            return None

        module_options = getattr(task.spec, "module_options", None) or {}
        options = getattr(task.spec, "options", None) or {}
        task_vars = options.get("vars") or {}

        invalid = []
        for src in (module_options, task_vars):
            if isinstance(src, dict):
                for key in src:
                    if isinstance(key, str) and not key.isidentifier():
                        invalid.append(key)

        verdict = len(invalid) > 0
        detail: dict[str, object] = {}
        if invalid:
            detail["message"] = f"Invalid variable name(s): {', '.join(invalid)}"
            detail["invalid_names"] = invalid
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
