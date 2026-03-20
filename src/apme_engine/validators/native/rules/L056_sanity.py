"""Native rule L056: detect files in paths that should be excluded from lint/sanity."""

import re
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

# Paths that are commonly ignored by ansible-lint / sanity (e.g. .git, files in certain dirs)
SANITY_IGNORE_PATTERNS = [
    re.compile(r"\.git/"),
    re.compile(r"/\.ansible/"),
    re.compile(r"\.pyc$"),
    re.compile(r"__pycache__"),
]


@dataclass
class SanityRule(Rule):
    """Rule for files in paths that should be excluded from lint/sanity.

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

    rule_id: str = "L056"
    description: str = "File may be in a path that should be excluded from lint/sanity"
    enabled: bool = True
    name: str = "Sanity"
    version: str = "v0.0.1"
    severity: str = Severity.VERY_LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.PLAYBOOK

    def match(self, ctx: AnsibleRunContext) -> bool:
        """Check if context has a task or role target.

        Args:
            ctx: AnsibleRunContext to evaluate.

        Returns:
            True if current target is a task or role.
        """
        return bool(ctx.current is not None and ctx.current.type in (RunTargetType.Task, RunTargetType.Role))

    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        """Check if path matches sanity ignore patterns and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with path/message detail, or None.
        """
        target = ctx.current
        if target is None:
            return None
        defined_in = getattr(target.spec, "defined_in", "") or ""
        verdict = any(p.search(defined_in) for p in SANITY_IGNORE_PATTERNS)
        detail = {}
        if verdict:
            detail["path"] = defined_in
            detail["message"] = "path matches common ignore pattern; consider excluding from scan"
        return RuleResult(
            verdict=verdict,
            detail=cast(YAMLDict | None, detail),
            file=cast("tuple[str | int, ...] | None", target.file_info()),
            rule=self.get_metadata(),
        )
