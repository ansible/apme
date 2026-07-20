"""ADR-064 assess-pause helpers and Gateway status mapping."""

from __future__ import annotations

from apme_gateway.operation_types import OperationStatus, SSEEventType


def test_assessed_status_is_non_terminal() -> None:
    """ASSESSED must not be treated as a terminal operation status.

    Returns:
        None.
    """
    from apme_gateway.operation_types import TERMINAL_STATUSES

    assert OperationStatus.ASSESSED not in TERMINAL_STATUSES
    assert OperationStatus.ASSESSED.value == "assessed"


def test_findings_sse_event_type() -> None:
    """FINDINGS event type is registered for SSE clients.

    Returns:
        None.
    """
    assert SSEEventType.FINDINGS.value == "findings"


def test_fix_options_assess_pause_default_false() -> None:
    """Proto FixOptions.assess_pause defaults to false (non-breaking).

    Returns:
        None.
    """
    from apme.v1 import primary_pb2

    assert primary_pb2.FixOptions().assess_pause is False
