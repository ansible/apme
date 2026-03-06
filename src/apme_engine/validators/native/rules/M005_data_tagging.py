"""M005: Data tagging — registered var used in Jinja template (2.19+ trust model).

Detects patterns where a registered variable is referenced inside a {{ }}
expression in a subsequent task, which may fail under 2.19's untrusted
data tagging model.
"""

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

_JINJA_VAR_REF = re.compile(r"\{\{\s*(\w+)")


@dataclass
class DataTaggingRule(Rule):
    rule_id: str = "M005"
    description: str = "Registered variable used in Jinja template may be untrusted in 2.19+"
    enabled: bool = True
    name: str = "DataTagging"
    version: str = "v0.0.1"
    severity: Severity = Severity.HIGH
    tags: tuple = Tag.CODING

    def match(self, ctx: AnsibleRunContext) -> bool:
        return ctx.current.type == RunTargetType.Task

    def process(self, ctx: AnsibleRunContext):
        task = ctx.current
        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}

        registered_vars = set()
        for prev in getattr(ctx, "previous_tasks", []):
            prev_opts = getattr(prev.spec, "options", None) or {}
            reg = prev_opts.get("register")
            if reg:
                registered_vars.add(reg)

        if not registered_vars:
            return RuleResult(verdict=False, detail={}, file=task.file_info(), rule=self.get_metadata())

        all_values = []
        for v in options.values():
            if isinstance(v, str):
                all_values.append(v)
        for v in module_options.values():
            if isinstance(v, str):
                all_values.append(v)

        flagged = []
        for val in all_values:
            for m in _JINJA_VAR_REF.finditer(val):
                var_name = m.group(1)
                if var_name in registered_vars:
                    flagged.append(var_name)

        verdict = len(flagged) > 0
        detail = {}
        if flagged:
            detail["message"] = f"Registered variable(s) {', '.join(set(flagged))} used in Jinja template; may be untrusted in 2.19+"
            detail["registered_vars"] = list(set(flagged))
        return RuleResult(verdict=verdict, detail=detail, file=task.file_info(), rule=self.get_metadata())
