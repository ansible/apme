"""Convert between dict violations (validator output) and proto Violation."""

from collections.abc import Mapping

from apme.v1 import common_pb2
from apme.v1.common_pb2 import LineRange, Violation
from apme_engine.engine.models import RemediationClass, RemediationResolution, RuleScope, ViolationDict

# Keys that live in the common Violation proto fields (not in metadata).
_COMMON_KEYS = frozenset(
    {
        "rule_id",
        "level",
        "message",
        "file",
        "line",
        "path",
        "remediation_class",
        "remediation_resolution",
        "scope",
    }
)

# Rule-specific keys that transforms need; forwarded via proto metadata map.
_METADATA_KEYS = frozenset(
    {
        "resolved_fqcn",
        "original_module",
        "fqcn",
        "with_key",
        "redirect_chain",
        "removal_msg",
    }
)

# Map string remediation class to proto enum (str keys for mypy compat with str,Enum)
_REMEDIATION_CLASS_TO_PROTO: dict[str, int] = {
    RemediationClass.AUTO_FIXABLE.value: common_pb2.REMEDIATION_CLASS_AUTO_FIXABLE,  # type: ignore[attr-defined]
    RemediationClass.AI_CANDIDATE.value: common_pb2.REMEDIATION_CLASS_AI_CANDIDATE,  # type: ignore[attr-defined]
    RemediationClass.MANUAL_REVIEW.value: common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW,  # type: ignore[attr-defined]
}

# Map proto enum to string remediation class
_PROTO_TO_REMEDIATION_CLASS: dict[int, str] = {
    common_pb2.REMEDIATION_CLASS_UNSPECIFIED: RemediationClass.AI_CANDIDATE.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_AUTO_FIXABLE: RemediationClass.AUTO_FIXABLE.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_AI_CANDIDATE: RemediationClass.AI_CANDIDATE.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW: RemediationClass.MANUAL_REVIEW.value,  # type: ignore[attr-defined]
}

# Map string remediation resolution to proto enum
_RESOLUTION_TO_PROTO: dict[str, int] = {
    RemediationResolution.UNRESOLVED.value: common_pb2.REMEDIATION_RESOLUTION_UNRESOLVED,  # type: ignore[attr-defined]
    RemediationResolution.TRANSFORM_FAILED.value: common_pb2.REMEDIATION_RESOLUTION_TRANSFORM_FAILED,  # type: ignore[attr-defined]
    RemediationResolution.OSCILLATION.value: common_pb2.REMEDIATION_RESOLUTION_OSCILLATION,  # type: ignore[attr-defined]
    RemediationResolution.AI_PROPOSED.value: common_pb2.REMEDIATION_RESOLUTION_AI_PROPOSED,  # type: ignore[attr-defined]
    RemediationResolution.AI_FAILED.value: common_pb2.REMEDIATION_RESOLUTION_AI_FAILED,  # type: ignore[attr-defined]
    RemediationResolution.AI_LOW_CONFIDENCE.value: common_pb2.REMEDIATION_RESOLUTION_AI_LOW_CONFIDENCE,  # type: ignore[attr-defined]
    RemediationResolution.USER_REJECTED.value: common_pb2.REMEDIATION_RESOLUTION_USER_REJECTED,  # type: ignore[attr-defined]
    RemediationResolution.NEEDS_CROSS_FILE.value: common_pb2.REMEDIATION_RESOLUTION_NEEDS_CROSS_FILE,  # type: ignore[attr-defined]
    RemediationResolution.MANUAL.value: common_pb2.REMEDIATION_RESOLUTION_MANUAL,  # type: ignore[attr-defined]
}

# Map proto enum to string remediation resolution
_PROTO_TO_RESOLUTION: dict[int, str] = {
    common_pb2.REMEDIATION_RESOLUTION_UNSPECIFIED: RemediationResolution.UNRESOLVED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_UNRESOLVED: RemediationResolution.UNRESOLVED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_TRANSFORM_FAILED: RemediationResolution.TRANSFORM_FAILED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_OSCILLATION: RemediationResolution.OSCILLATION.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_PROPOSED: RemediationResolution.AI_PROPOSED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_FAILED: RemediationResolution.AI_FAILED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_AI_LOW_CONFIDENCE: RemediationResolution.AI_LOW_CONFIDENCE.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_USER_REJECTED: RemediationResolution.USER_REJECTED.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_NEEDS_CROSS_FILE: RemediationResolution.NEEDS_CROSS_FILE.value,  # type: ignore[attr-defined]
    common_pb2.REMEDIATION_RESOLUTION_MANUAL: RemediationResolution.MANUAL.value,  # type: ignore[attr-defined]
}

_SCOPE_TO_PROTO: dict[str, int] = {
    RuleScope.TASK.value: common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
    RuleScope.BLOCK.value: common_pb2.RULE_SCOPE_BLOCK,  # type: ignore[attr-defined]
    RuleScope.PLAY.value: common_pb2.RULE_SCOPE_PLAY,  # type: ignore[attr-defined]
    RuleScope.PLAYBOOK.value: common_pb2.RULE_SCOPE_PLAYBOOK,  # type: ignore[attr-defined]
    RuleScope.ROLE.value: common_pb2.RULE_SCOPE_ROLE,  # type: ignore[attr-defined]
    RuleScope.INVENTORY.value: common_pb2.RULE_SCOPE_INVENTORY,  # type: ignore[attr-defined]
    RuleScope.COLLECTION.value: common_pb2.RULE_SCOPE_COLLECTION,  # type: ignore[attr-defined]
}

_PROTO_TO_SCOPE: dict[int, str] = {
    common_pb2.RULE_SCOPE_UNSPECIFIED: RuleScope.TASK.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_TASK: RuleScope.TASK.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_BLOCK: RuleScope.BLOCK.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_PLAY: RuleScope.PLAY.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_PLAYBOOK: RuleScope.PLAYBOOK.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_ROLE: RuleScope.ROLE.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_INVENTORY: RuleScope.INVENTORY.value,  # type: ignore[attr-defined]
    common_pb2.RULE_SCOPE_COLLECTION: RuleScope.COLLECTION.value,  # type: ignore[attr-defined]
}


def violation_dict_to_proto(v: ViolationDict | Mapping[str, str | int | list[int] | bool | None]) -> Violation:
    """Build a proto Violation from a dict with rule_id, level, message, file, line, path.

    Args:
        v: Dict or mapping with rule_id, level, message, file, line (int or [start,end]), path,
           and optional remediation_class / remediation_resolution.

    Returns:
        Violation proto populated from the dict.
    """
    rc_raw = v.get("remediation_class") or RemediationClass.AI_CANDIDATE
    remediation_class_str = rc_raw.value if hasattr(rc_raw, "value") else str(rc_raw)
    remediation_class_proto = _REMEDIATION_CLASS_TO_PROTO.get(
        remediation_class_str,
        common_pb2.REMEDIATION_CLASS_AI_CANDIDATE,  # type: ignore[attr-defined]
    )
    res_raw = v.get("remediation_resolution") or RemediationResolution.UNRESOLVED
    resolution_str = res_raw.value if hasattr(res_raw, "value") else str(res_raw)
    resolution_proto = _RESOLUTION_TO_PROTO.get(
        resolution_str,
        common_pb2.REMEDIATION_RESOLUTION_UNRESOLVED,  # type: ignore[attr-defined]
    )

    scope_raw = v.get("scope") or RuleScope.TASK
    scope_str = scope_raw.value if hasattr(scope_raw, "value") else str(scope_raw)
    scope_proto = _SCOPE_TO_PROTO.get(
        scope_str,
        common_pb2.RULE_SCOPE_TASK,  # type: ignore[attr-defined]
    )

    out = Violation(
        rule_id=str(v.get("rule_id") or ""),
        level=str(v.get("level") or ""),
        message=str(v.get("message") or ""),
        file=str(v.get("file") or ""),
        path=str(v.get("path") or ""),
        remediation_class=remediation_class_proto,
        remediation_resolution=resolution_proto,
        scope=scope_proto,
    )
    line = v.get("line")
    if isinstance(line, list | tuple) and len(line) >= 2:
        out.line_range.CopyFrom(LineRange(start=int(line[0]), end=int(line[1])))
    elif isinstance(line, int):
        out.line = line
    elif isinstance(line, str) and line:
        raw = line.lstrip("L")
        parts = raw.split("-")
        try:
            if len(parts) >= 2:
                out.line_range.CopyFrom(LineRange(start=int(parts[0]), end=int(parts[1])))
            else:
                out.line = int(parts[0])
        except (ValueError, IndexError):
            pass

    for key in _METADATA_KEYS:
        val = v.get(key)
        if val is not None:
            if isinstance(val, list):
                out.metadata[key] = ",".join(str(x) for x in val)
            else:
                out.metadata[key] = str(val)

    return out


def violation_proto_to_dict(v: Violation) -> ViolationDict:
    """Build a dict violation from proto (for CLI output).

    Args:
        v: Violation proto to convert.

    Returns:
        ViolationDict with rule_id, level, message, file, line, path,
        remediation_class, remediation_resolution.
    """
    line: int | list[int] | None = v.line if v.HasField("line") else None
    if v.HasField("line_range"):
        line = [v.line_range.start, v.line_range.end]
    remediation_class = _PROTO_TO_REMEDIATION_CLASS.get(
        v.remediation_class,
        RemediationClass.AI_CANDIDATE.value,
    )
    resolution = _PROTO_TO_RESOLUTION.get(
        v.remediation_resolution,
        RemediationResolution.UNRESOLVED.value,
    )
    scope = _PROTO_TO_SCOPE.get(
        v.scope,
        RuleScope.TASK.value,
    )
    result: ViolationDict = {
        "rule_id": v.rule_id,
        "level": v.level,
        "message": v.message,
        "file": v.file,
        "line": line,
        "path": v.path,
        "remediation_class": remediation_class,
        "remediation_resolution": resolution,
        "scope": scope,
    }

    for key, val in v.metadata.items():
        if key in _METADATA_KEYS and val:
            if key == "redirect_chain":
                result[key] = val.split(",")  # type: ignore[assignment]
            else:
                result[key] = val

    return result
