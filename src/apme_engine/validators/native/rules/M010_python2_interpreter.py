"""Native rule M010: detect ansible_python_interpreter set to Python 2."""

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

_PY2_PATH = re.compile(r"python2(\.\d+)?$")


def _play_vars_for_task(ctx: AnsibleRunContext, task_key: str) -> YAMLDict:
    """Collect play-level variables from the sequence before the current task.

    Args:
        ctx: Current Ansible run context.
        task_key: Key identifying the current task.

    Returns:
        Dict of play-level variables.
    """
    play_vars: YAMLDict = {}
    for rt in ctx.sequence:
        if rt.key == task_key:
            break
        if rt.type == RunTargetType.Play:
            spec = getattr(rt, "spec", None)
            if spec is not None:
                play_vars = dict(getattr(spec, "variables", None) or {})
    return play_vars


@dataclass
class Python2InterpreterRule(Rule):
    """Rule for ansible_python_interpreter set to Python 2 (dropped in 2.18+).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M010"
    description: str = "ansible_python_interpreter set to Python 2; dropped in 2.18+"
    enabled: bool = True
    name: str = "Python2Interpreter"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
    tags: tuple[str, ...] = (Tag.CODING,)

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
        """Check for Python 2 interpreter path and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with interpreter detail, or None.
        """
        task = ctx.current
        if task is None:
            return None
        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}
        task_vars = options.get("vars") or {}
        play_vars = _play_vars_for_task(ctx, task.key)

        interpreter = (
            task_vars.get("ansible_python_interpreter")
            or play_vars.get("ansible_python_interpreter")
            or options.get("ansible_python_interpreter")
            or module_options.get("ansible_python_interpreter")
            or ""
        )
        interpreter_str = str(interpreter) if interpreter else ""

        verdict = bool(_PY2_PATH.search(interpreter_str))
        detail: YAMLDict = {}
        if verdict:
            detail["message"] = f"ansible_python_interpreter set to Python 2 path: {interpreter_str}"
            detail["interpreter"] = interpreter_str
        return RuleResult(
            verdict=verdict,
            detail=detail,
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
