"""Remediation engine — deterministic transforms + AI escalation for scan violations."""

from apme_engine.remediation.engine import FixReport, RemediationEngine
from apme_engine.remediation.partition import is_finding_resolvable
from apme_engine.remediation.registry import (
    StructuredTransformFn,
    TransformFn,
    TransformRegistry,
    TransformResult,
)
from apme_engine.remediation.structured import StructuredFile

__all__ = [
    "FixReport",
    "RemediationEngine",
    "StructuredFile",
    "StructuredTransformFn",
    "TransformFn",
    "TransformRegistry",
    "TransformResult",
    "is_finding_resolvable",
]
