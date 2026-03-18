"""Native rule R115: detect file deletion (directories recursively deleted)."""

from dataclasses import dataclass
from typing import cast

from apme_engine.engine.models import (
    AnnotationCondition,
    AnsibleRunContext,
    Rule,
    RuleResult,
    RunTargetType,
    Severity,
    TaskCall,
    YAMLDict,
)
from apme_engine.engine.models import DefaultRiskType as RiskType
from apme_engine.engine.models import RuleTag as Tag


@dataclass
class FileDeletionRule(Rule):
    """Rule for file deletion (directories recursively deleted).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
    """

    rule_id: str = "R115"
    description: str = "File deletion found. Directories will be recursively deleted."
    enabled: bool = True
    name: str = "FileDeletionRule"
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
        """Check for file deletion and return result.

        Args:
            ctx: AnsibleRunContext to process.

        Returns:
            RuleResult with path detail, or None.
        """
        task = ctx.current
        if task is None or not isinstance(task, TaskCall):
            return None

        # define a condition for this rule here
        ac = (
            AnnotationCondition()
            .risk_type(RiskType.FILE_CHANGE)
            .attr("is_deletion", True)
            .attr("is_mutable_path", True)
        )
        verdict = task.has_annotation_by_condition(ac)

        detail: dict[str, object] = {}
        if verdict:
            anno = task.get_annotation_by_condition(ac)
            if anno is not None and hasattr(anno, "path") and anno.path is not None:
                detail["path"] = anno.path.value

        return RuleResult(
            verdict=verdict,
            detail=cast("YAMLDict | None", detail),
            file=cast("tuple[str | int, ...] | None", task.file_info()),
            rule=self.get_metadata(),
        )
