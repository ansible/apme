"""Unit tests for bounded streamed reporting that need no database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from apme.v1 import reporting_pb2
from apme_gateway.grpc_reporting import servicer as reporting_servicer
from apme_gateway.grpc_reporting.servicer import ReportingServicer


def _mock_context() -> MagicMock:
    """Build a gRPC context mock with an awaitable abort method.

    Returns:
        Mock gRPC context.
    """
    context = MagicMock()
    context.abort = AsyncMock()
    return context


async def test_report_stream_parses_serialized_fragment_before_identity_check() -> None:
    """Serialized fragments are parsed before a missing scan ID is rejected."""
    event = reporting_pb2.FixCompletedEvent(session_id="session-without-scan-id")
    chunk = reporting_pb2.FixCompletedChunk(
        serialized_event_fragment=event.SerializeToString(),
        last=True,
    )

    async def _aiter() -> AsyncIterator[reporting_pb2.FixCompletedChunk]:
        yield chunk

    context = _mock_context()
    await ReportingServicer().ReportFixCompletedStream(_aiter(), context)

    context.abort.assert_awaited_once_with(
        grpc.StatusCode.INVALID_ARGUMENT,
        "FixCompletedChunk stream missing scan_id header",
    )


async def test_report_stream_rejects_malformed_serialized_event() -> None:
    """Malformed serialized event bytes are rejected before persistence."""
    chunk = reporting_pb2.FixCompletedChunk(serialized_event_fragment=b"\x80", last=True)

    async def _aiter() -> AsyncIterator[reporting_pb2.FixCompletedChunk]:
        yield chunk

    context = _mock_context()
    await ReportingServicer().ReportFixCompletedStream(_aiter(), context)

    context.abort.assert_awaited_once_with(
        grpc.StatusCode.INVALID_ARGUMENT,
        "FixCompletedChunk serialized event is malformed",
    )


async def test_report_stream_rejects_aggregate_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A streamed event cannot exceed the aggregate byte limit.

    Args:
        monkeypatch: Pytest fixture for adjusting the stream limit.
    """
    monkeypatch.setattr(reporting_servicer, "MAX_STREAM_BYTES", 5)
    chunk = reporting_pb2.FixCompletedChunk(serialized_event_fragment=b"123456", last=True)

    async def _aiter() -> AsyncIterator[reporting_pb2.FixCompletedChunk]:
        yield chunk

    context = _mock_context()
    await ReportingServicer().ReportFixCompletedStream(_aiter(), context)

    context.abort.assert_awaited_once_with(
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        "FixCompletedChunk stream exceeds 5 bytes",
    )


async def test_report_stream_rejects_excess_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream cannot exceed its chunk-count limit.

    Args:
        monkeypatch: Pytest fixture for adjusting the chunk-count limit.
    """
    monkeypatch.setattr(reporting_servicer, "MAX_STREAM_CHUNKS", 1)

    async def _aiter() -> AsyncIterator[reporting_pb2.FixCompletedChunk]:
        yield reporting_pb2.FixCompletedChunk(
            header=reporting_pb2.FixCompletedEvent(scan_id="too-many"),
        )
        yield reporting_pb2.FixCompletedChunk(last=True)

    context = _mock_context()
    await ReportingServicer().ReportFixCompletedStream(_aiter(), context)

    context.abort.assert_awaited_once_with(
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        "FixCompletedChunk stream exceeds 1 chunks",
    )
