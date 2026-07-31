"""Tests for validator infrastructure error responses."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apme.v1 import validate_pb2


class FakeGrpcContext:
    """Minimal stub for grpc.ServicerContext."""

    def set_code(self, code: type) -> None:
        """Stub for set_code.

        Args:
            code: gRPC status code type.
        """

    def set_details(self, details: str) -> None:
        """Stub for set_details.

        Args:
            details: Error details string.
        """


async def test_native_validator_returns_infra_violation_on_error() -> None:
    """Unhandled native validator errors surface as INFRA-002 violations."""
    from apme_engine.daemon.native_validator_server import NativeValidatorServicer

    request = validate_pb2.ValidateRequest(
        request_id="infra-test-1",
        content_graph_data=b'{"version": 1}',
    )
    servicer = NativeValidatorServicer()

    with patch(
        "apme_engine.daemon.native_validator_server._run_graph",
        side_effect=RuntimeError("graph exploded"),
    ):
        resp = await servicer.Validate(request, FakeGrpcContext())  # type: ignore[arg-type]

    assert len(resp.violations) == 1  # type: ignore[attr-defined]
    assert resp.violations[0].rule_id == "INFRA-002"  # type: ignore[attr-defined]
    assert "graph exploded" in resp.violations[0].message  # type: ignore[attr-defined]


def test_sse_format_serializes_event() -> None:
    """Operation SSE helper emits event name and JSON payload."""
    from apme_gateway.api.operation_router import _sse_format

    payload = _sse_format("status", {"status": "running"})
    assert payload.startswith("event: status\n")
    assert '"status": "running"' in payload
    assert payload.endswith("\n\n")
