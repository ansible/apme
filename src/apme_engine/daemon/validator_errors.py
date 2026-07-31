"""Shared infrastructure violation helpers for validator gRPC servicers."""

from __future__ import annotations

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict


def infra_violation(message: str, *, rule_id: str = "INFRA-002") -> ViolationDict:
    """Build a validator infrastructure failure violation dict.

    Args:
        message: Human-readable error description.
        rule_id: Infrastructure rule id (``INFRA-001`` or ``INFRA-002``).

    Returns:
        Violation dict suitable for ``violation_dict_to_proto``.
    """
    return {
        "rule_id": rule_id,
        "severity": "error",
        "message": message,
        "file": "",
        "line": 1,
        "path": "",
    }


def infra_error_response(
    req_id: str,
    message: str,
    logs: list[ProgressUpdate],
    *,
    rule_id: str = "INFRA-002",
) -> ValidateResponse:
    """Return a ValidateResponse signalling validator infrastructure failure.

    Args:
        req_id: Request id from the incoming Validate RPC.
        message: Error description for operators.
        logs: Collected log entries from the request sink.
        rule_id: Infrastructure rule id.

    Returns:
        ValidateResponse with a single infrastructure violation.
    """
    return ValidateResponse(
        violations=[violation_dict_to_proto(infra_violation(message, rule_id=rule_id))],
        request_id=req_id,
        logs=logs,
    )
