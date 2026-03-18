"""Native rule L048: detect copy with remote_src without explicit owner."""

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

COPY_MODULES = frozenset(
    {
        "ansible.builtin.copy",
        "ansible.legacy.copy",
        "copy",
    }
)


@dataclass
class NoSameOwnerRule(Rule):
    """Rule for copy with remote_src to set owner explicitly.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "L048"
    description: str = "copy with remote_src should set owner explicitly; avoid same-owner default"
    enabled: bool = True
    name: str = "NoSameOwner"
    version: str = "v0.0.1"
    severity: str = Severity.LOW
    tags: tuple[str, ...] = (Tag.SYSTEM,)

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
        """Check for copy with remote_src without owner and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with message detail, or None.
        """
        task = ctx.current
        if task is None:
            return None
        resolved = getattr(task.spec, "resolved_name", "") or ""
        file_info = cast("tuple[str | int, ...] | None", task.file_info())
        if resolved not in COPY_MODULES:
            return RuleResult(verdict=False, file=file_info, rule=self.get_metadata())
        module_options = getattr(task.spec, "module_options", None) or {}
        remote_src = module_options.get("remote_src")
        if not remote_src:
            return RuleResult(verdict=False, file=file_info, rule=self.get_metadata())
        has_owner = "owner" in module_options
        verdict = not has_owner
        detail: dict[str, str] = {}
        if verdict:
            detail["message"] = "copy with remote_src should set owner explicitly"
        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=file_info,
            rule=self.get_metadata(),
        )
