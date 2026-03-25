"""M022: tree / oneline callback plugins removed in 2.23.

Detects tasks that set ANSIBLE_STDOUT_CALLBACK or similar environment
variables to a removed callback. Does not scan ansible.cfg directly
since that is outside the playbook/role task scope.
"""

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

_REMOVED_CALLBACKS = {"tree", "oneline"}
_CALLBACK_REF = re.compile(r"\b(?:stdout_callback|callback_whitelist|callbacks_enabled)\s*[=:]\s*(\w+)")


@dataclass
class TreeOnelineCallbackPluginsRule(Rule):
    """Detect references to removed tree/oneline callback plugins.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M022"
    description: str = "tree and oneline callback plugins are removed in 2.23"
    enabled: bool = True
    name: str = "TreeOnelineCallbackPlugins"
    version: str = "v0.0.1"
    severity: str = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)

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
        """Check for references to removed callback plugins.

        Args:
            ctx: Rule evaluation context with task/play data.

        Returns:
            RuleResult with verdict and violation details, or None if the task is unavailable.

        """
        task = ctx.current
        if task is None:
            return None

        yaml_lines = getattr(task.spec, "yaml_lines", "") or ""
        options = getattr(task.spec, "options", None) or {}
        module_options = getattr(task.spec, "module_options", None) or {}

        all_text = yaml_lines
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text += " " + v

        found = set()
        for m in _CALLBACK_REF.finditer(all_text):
            cb = m.group(1).strip()
            if cb in _REMOVED_CALLBACKS:
                found.add(cb)

        env_vars = options.get("environment") or {}
        if isinstance(env_vars, dict):
            for key in ("ANSIBLE_STDOUT_CALLBACK",):
                val = env_vars.get(key, "")
                if isinstance(val, str) and val in _REMOVED_CALLBACKS:
                    found.add(val)

        verdict = len(found) > 0
        detail: dict[str, object] = {}
        if found:
            detail["message"] = f"Removed callback plugin(s): {', '.join(sorted(found))}"
            detail["removed_callbacks"] = sorted(found)
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
