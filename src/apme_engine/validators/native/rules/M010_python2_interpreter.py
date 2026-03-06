"""M010: Python 2 interpreter path detected (dropped in ansible-core 2.18+)."""

import re
from dataclasses import dataclass

from apme_engine.engine.models import (
    AnsibleRunContext,
    RunTargetType,
    Rule,
    Severity,
    RuleTag as Tag,
    RuleResult,
)

_PY2_PATH = re.compile(r"python2(\.\d+)?$")


@dataclass
class Python2InterpreterRule(Rule):
    rule_id: str = "M010"
    description: str = "ansible_python_interpreter set to Python 2; dropped in 2.18+"
    enabled: bool = True
    name: str = "Python2Interpreter"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple = Tag.CODING

    def match(self, ctx: AnsibleRunContext) -> bool:
        return ctx.current.type == RunTargetType.Task

    def process(self, ctx: AnsibleRunContext):
        task = ctx.current
        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}
        task_vars = options.get("vars") or {}

        interpreter = (
            task_vars.get("ansible_python_interpreter")
            or options.get("ansible_python_interpreter")
            or module_options.get("ansible_python_interpreter")
            or ""
        )

        verdict = bool(_PY2_PATH.search(interpreter))
        detail = {}
        if verdict:
            detail["message"] = f"ansible_python_interpreter set to Python 2 path: {interpreter}"
            detail["interpreter"] = interpreter
        return RuleResult(verdict=verdict, detail=detail, file=task.file_info(), rule=self.get_metadata())
