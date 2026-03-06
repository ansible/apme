"""Remediation engine — deterministic transforms + AI escalation for scan violations."""

from apme_engine.remediation.registry import TransformRegistry, TransformResult, TransformFn
from apme_engine.remediation.partition import is_finding_resolvable
from apme_engine.remediation.engine import RemediationEngine, FixReport

__all__ = [
    "TransformRegistry",
    "TransformResult",
    "TransformFn",
    "is_finding_resolvable",
    "RemediationEngine",
    "FixReport",
]
