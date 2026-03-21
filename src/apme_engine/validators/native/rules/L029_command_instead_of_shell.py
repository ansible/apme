"""Native rule L029: detect shell usage where command suffices."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnsibleRunContext,
    Rule,
    RuleResult,
    RunTargetType,
    Severity,
)
from apme_engine.engine.models import (
    ExecutableType as ActionType,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)


@dataclass
class UseShellRule(Rule):
    """Rule for tasks using shell instead of command module.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L029"
    description: str = "Use 'command' module instead of 'shell' "
    enabled: bool = True
    name: str = "UseShellRule"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.COMMAND,)

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
        """Check for shell module usage and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with verdict, or None.
        """
        task = ctx.current
        if task is None:
            return None

        # define a condition for this rule here
        action_type = getattr(task, "action_type", "")
        spec_action = getattr(task.spec, "action", None)
        resolved_action = getattr(task, "resolved_action", "")
        verdict = bool(
            action_type == ActionType.MODULE_TYPE
            and spec_action
            and resolved_action
            and resolved_action == "ansible.builtin.shell"
        )

        return RuleResult(
            verdict=verdict,
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
