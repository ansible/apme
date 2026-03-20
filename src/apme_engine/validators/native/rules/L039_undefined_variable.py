"""Native rule L039: detect undefined variables."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
    VariableType,
    YAMLDict,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class UndefinedVariableRule(Rule):
    """Rule for undefined variable references.

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

    rule_id: str = "L039"
    description: str = "Undefined variable is found"
    enabled: bool = True
    name: str = "UndefinedVariable"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.VARIABLE,)
    scope: str = RuleScope.INVENTORY

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
        """Check for undefined variables and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with undefined_variables detail, or None.
        """
        task = ctx.current
        if task is None:
            return None

        verdict = False
        detail: dict[str, object] = {}
        variable_use = getattr(task, "variable_use", {}) or {}
        for v_name in variable_use:
            v = variable_use[v_name]
            if v and v[-1].type == VariableType.Unknown:
                verdict = True
                uv = detail.get("undefined_variables", [])
                current: list[str] = list(uv) if isinstance(uv, list) else []
                current.append(v_name)
                detail["undefined_variables"] = current

        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
