"""Shared infrastructure violation helpers for validator gRPC servicers."""

from __future__ import annotations

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict

# ADR-008 Risk range reserved for validator infrastructure failures:
# R901 = missing session precondition (e.g. no venv)
# R902 = validator runtime / infrastructure failure
RULE_MISSING_VENV = "R901"
RULE_VALIDATOR_FAILURE = "R902"

# Client-facing message — never include exception text (may leak paths/secrets).
PUBLIC_VALIDATOR_ERROR = "Validator infrastructure error; see server logs for details"


def infra_violation(message: str, *, rule_id: str = RULE_VALIDATOR_FAILURE) -> ViolationDict:
    """Build a validator infrastructure failure violation dict.

    Args:
        message: Human-readable error description (safe for clients).
        rule_id: Infrastructure rule id (``R901`` or ``R902``).

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
    logs: list[ProgressUpdate],
    *,
    message: str = PUBLIC_VALIDATOR_ERROR,
    rule_id: str = RULE_VALIDATOR_FAILURE,
) -> ValidateResponse:
    """Return a ValidateResponse signalling validator infrastructure failure.

    Args:
        req_id: Request id from the incoming Validate RPC.
        logs: Collected log entries from the request sink.
        message: Client-safe error description (defaults to public constant).
        rule_id: Infrastructure rule id.

    Returns:
        ValidateResponse with a single infrastructure violation.
    """
    return ValidateResponse(
        violations=[violation_dict_to_proto(infra_violation(message, rule_id=rule_id))],
        request_id=req_id,
        logs=logs,
    )
