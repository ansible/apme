"""Native rule L046: detect raw/command/shell without explicit args (free-form)."""

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
    ExecutableType as ActionType,
)
from apme_engine.engine.models import (
    RuleTag as Tag,
)

FREE_FORM_ACTIONS = frozenset(
    {
        "ansible.builtin.raw",
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.legacy.raw",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "raw",
        "command",
        "shell",
    }
)


@dataclass
class NoFreeFormRule(Rule):
    """Rule for avoiding raw/command/shell without explicit args.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L046"
    description: str = "Avoid raw/command/shell without explicit args (use args: key)"
    enabled: bool = True
    name: str = "NoFreeForm"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.COMMAND,)

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Return True if the current target is a task.

        Args:
            ctx: Current Ansible run context.

        Returns:
            True if the current target is a task.

        """
        if ctx.current is None:
            return False
        return bool(ctx.current.type == RunTargetType.Task)

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Check for free-form raw/command/shell and return result.

        Args:
            ctx: Current Ansible run context.

        Returns:
            RuleResult with verdict and detail if free-form usage found, None otherwise.

        """
        task = ctx.current
        if task is None:
            return None
        if getattr(task, "action_type", "") != ActionType.MODULE_TYPE:
            return RuleResult(
                verdict=False,
                file=cast("tuple[str | int, ...] | None", task.file_info()),
                rule=self.get_metadata(),
            )
        resolved = getattr(task.spec, "resolved_name", "") or ""
        if resolved not in FREE_FORM_ACTIONS:
            return RuleResult(
                verdict=False,
                file=cast("tuple[str | int, ...] | None", task.file_info()),
                rule=self.get_metadata(),
            )
        args = getattr(task, "args", None)
        raw = getattr(args, "raw", None) if args is not None else None
        # Free-form: args.raw is a string, or module_options has "_raw" string (loader stores free-form there)
        if isinstance(raw, dict) and "_raw" in raw:
            raw = raw.get("_raw")
        is_free_form = isinstance(raw, str) and raw.strip() != ""
        verdict = is_free_form
        detail = {}
        if verdict:
            detail["module"] = resolved
            detail["message"] = "use args: with a list or cmd: key instead of free-form string"
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
