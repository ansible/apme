"""Native rule R501: suggest dependencies for unresolved modules/roles."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RuleScope,
    RunTargetType,
    Severity,
    Task,
    YAMLDict,
)
from apme_engine.engine.models import RuleTag as Tag


@dataclass
class DependencySuggestionRule(Rule):
    """Rule to suggest dependencies for unresolved modules/roles.

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

    rule_id: str = "R501"
    description: str = "Suggest dependencies for unresolved modules/roles"
    enabled: bool = True
    name: str = "DependencySuggestion"
    version: str = "v0.0.1"
    severity: str = Severity.NONE
    tags: tuple[str, ...] = (Tag.DEPENDENCY,)
    scope: str = RuleScope.COLLECTION

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
        """Suggest dependencies for unresolved modules/roles and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with type/fqcn/defined_in detail, or None.
        """
        task = ctx.current
        if task is None:
            return None

        verdict = False
        detail: YAMLDict = {}
        spec = task.spec
        if isinstance(spec, Task) and spec.possible_candidates:
            fqcn, defined_in = spec.possible_candidates[0]
            verdict = True
            detail["type"] = spec.executable_type.lower()
            detail["fqcn"] = fqcn
            detail["defined_in"] = defined_in

        return RuleResult(
            verdict=verdict,
            detail=detail if detail else None,
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
