"""Native rule L034: detect variables not successfully re-defined due to low precedence."""

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
class UnusedOverrideRule(Rule):
    """Rule for variables not successfully re-defined due to low precedence.

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

    rule_id: str = "L034"
    description: str = "A variable is not successfully re-defined because of low precedence"
    enabled: bool = True
    name: str = "UnusedOverride"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
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
        """Check for unused variable override and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with variables detail, or None.
        """
        task = ctx.current
        if task is None:
            return None

        verdict = False
        variables_list: list[dict[str, object]] = []
        defined_vars = getattr(task.spec, "defined_vars", None) or []
        variable_set = getattr(task, "variable_set", {}) or {}
        if defined_vars:
            for v in defined_vars:
                all_definitions = variable_set.get(v, [])
                if len(all_definitions) > 1:
                    prev_prec = all_definitions[-2].type
                    new_prec = all_definitions[-1].type
                    if new_prec < prev_prec:
                        variables_list.append(
                            {
                                "name": v,
                                "prev_precedence": prev_prec,
                                "new_precedence": new_prec,
                            }
                        )
                        verdict = True

        detail: dict[str, object] = {"variables": variables_list}
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
