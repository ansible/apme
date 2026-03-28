"""M014: Top-level fact variables — use ansible_facts["name"] (removed in 2.24).

Higher severity than L076 (lint/style): this is a *breaking change* in 2.24
where top-level fact injection is removed entirely.  L076 is a best-practice
recommendation; M014 signals that the code *will break* on 2.24+.

Note: detection matches any ``ansible_*`` variable not in the magic-vars
allowlist, which may include connection/inventory variables like
``ansible_user`` or ``ansible_host`` that are not injected facts.  A future
refinement could restrict matches to known injected fact names.
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

MAGIC_VARS = frozenset(
    {
        "ansible_check_mode",
        "ansible_diff_mode",
        "ansible_forks",
        "ansible_play_batch",
        "ansible_play_hosts",
        "ansible_play_hosts_all",
        "ansible_play_name",
        "ansible_play_role_names",
        "ansible_role_names",
        "ansible_run_tags",
        "ansible_skip_tags",
        "ansible_version",
        "ansible_loop",
        "ansible_loop_var",
        "ansible_index_var",
        "ansible_parent_role_names",
        "ansible_parent_role_paths",
        "ansible_facts",
        "ansible_local",
        "ansible_verbosity",
        "ansible_config_file",
        "ansible_connection",
        "ansible_become",
        "ansible_become_method",
    }
)

_ANSIBLE_VAR = re.compile(r"\b(ansible_\w+)\b")


@dataclass
class TopLevelFactVariablesRule(Rule):
    """Detect injected ansible_* fact variables that will break in 2.24.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "M014"
    description: str = 'Use ansible_facts["name"] instead of injected ansible_* fact variables (removed in 2.24)'
    enabled: bool = True
    name: str = "TopLevelFactVariables"
    version: str = "v0.0.1"
    severity: str = Severity.HIGH
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
        """Scan Jinja2 expressions for deprecated top-level fact variables.

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

        all_text_parts = [yaml_lines]
        for v in list(options.values()) + list(module_options.values()):
            if isinstance(v, str):
                all_text_parts.append(v)
        text = " ".join(all_text_parts)

        found = set()
        for m in _ANSIBLE_VAR.finditer(text):
            varname = m.group(1)
            if varname not in MAGIC_VARS and varname.startswith("ansible_"):
                found.add(varname)

        verdict = len(found) > 0
        detail: dict[str, object] = {}
        if found:
            suggestions = {v: f'ansible_facts["{v.removeprefix("ansible_")}"]' for v in sorted(found)}
            detail["message"] = f"Top-level fact variable(s) {', '.join(sorted(found))} removed in 2.24"
            detail["found_facts"] = sorted(found)
            detail["suggestions"] = suggestions
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
