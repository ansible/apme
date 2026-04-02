"""Convert proto Violation to plain dict for CLI rendering and JSON output."""

from __future__ import annotations

from apme.v1 import common_pb2
from apme.v1.common_pb2 import Violation
from apme_engine.severity_defaults import severity_from_proto, severity_to_label

_PROTO_TO_REMEDIATION_CLASS: dict[int, str] = {
    common_pb2.REMEDIATION_CLASS_UNSPECIFIED: "ai-candidate",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_AUTO_FIXABLE: "auto-fixable",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_AI_CANDIDATE: "ai-candidate",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW: "manual-review",  # type: ignore[attr-defined]
}

_PROTO_TO_RESOLUTION: dict[int, str] = {
    common_pb2.REMEDIATION_RESOLUTION_UNSPECIFIED: "unresolved",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_UNRESOLVED: "unresolved",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_TRANSFORM_FAILED: "transform-failed",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_OSCILLATION: "oscillation",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_PROPOSED: "ai-proposed",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_FAILED: "ai-failed",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_LOW_CONFIDENCE: "ai-low-confidence",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_USER_REJECTED: "user-rejected",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_NEEDS_CROSS_FILE: "needs-cross-file",  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_MANUAL: "manual",  # type: ignore[attr-defined]
}

_PROTO_TO_SCOPE: dict[int, str] = {
    common_pb2.RULE_SCOPE_UNSPECIFIED: "task",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_TASK: "task",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_BLOCK: "block",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_PLAY: "play",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_PLAYBOOK: "playbook",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_ROLE: "role",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_INVENTORY: "inventory",  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_COLLECTION: "collection",  # type: ignore[attr-defined]
}


def violation_proto_to_dict(
    v: Violation,
) -> dict[str, str | int | list[int] | bool | None]:
    """Build a plain dict from a proto Violation for CLI output.

    Args:
        v: Protobuf Violation message.

    Returns:
        Plain dict suitable for CLI rendering.
    """
    line: int | list[int] | None = v.line if v.HasField("line") else None
    if v.HasField("line_range"):
        line = [v.line_range.start, v.line_range.end]
    return {
        "rule_id": v.rule_id,
        "severity": severity_to_label(severity_from_proto(v.severity)),
        "message": v.message,
        "file": v.file,
        "line": line,
        "path": v.path,
        "remediation_class": _PROTO_TO_REMEDIATION_CLASS.get(
            v.remediation_class,
            "ai-candidate",
        ),
        "remediation_resolution": _PROTO_TO_RESOLUTION.get(
            v.remediation_resolution,
            "unresolved",
        ),
        "scope": _PROTO_TO_SCOPE.get(
            v.scope,
            "task",
        ),
    }
