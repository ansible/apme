"""gRPC reporting sink -- pushes events to a Reporting service (ADR-020).

Health-gated: a background task probes the endpoint every 10 s.
When the service is marked unavailable, emit calls use a short fast-fail
timeout (1 s) so known-down endpoints don't block the scan path.
When the endpoint is healthy, the full timeout is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterator

import grpc
import grpc.aio

from apme.v1 import reporting_pb2, reporting_pb2_grpc
from apme_engine.daemon.chunked_reporting import (
    GRPC_MAX_MESSAGE_BYTES,
    UNARY_MAX_BYTES,
    yield_fix_completed_chunks,
)

logger = logging.getLogger("apme.events.grpc")

_TIMEOUT_S = 10.0
_STREAM_TIMEOUT_S = 120.0  # large streamed events + Gateway persist
_FAST_FAIL_TIMEOUT_S = 1.0
_HEALTH_INTERVAL_S = 10.0
_STARTUP_PROBE_RETRIES = 5
_STARTUP_PROBE_DELAY_S = 2.0
_GRPC_MAX_MSG = GRPC_MAX_MESSAGE_BYTES


def _next_chunk(
    iterator: Iterator[reporting_pb2.FixCompletedChunk],
) -> reporting_pb2.FixCompletedChunk | None:
    """Advance the synchronous chunk packer once, returning None at EOF.

    Args:
        iterator: Lazy chunk iterator.

    Returns:
        Next chunk, or None when the iterator is exhausted.
    """
    return next(iterator, None)


async def _async_chunks(
    event: reporting_pb2.FixCompletedEvent,
) -> AsyncIterator[reporting_pb2.FixCompletedChunk]:
    """Run chunk packing in the executor and expose an async gRPC iterator.

    Args:
        event: Completed event to stream.

    Yields:
        reporting_pb2.FixCompletedChunk: Chunks produced lazily by the packer.
    """
    iterator = yield_fix_completed_chunks(event)
    loop = asyncio.get_running_loop()
    while (chunk := await loop.run_in_executor(None, _next_chunk, iterator)) is not None:
        yield chunk


class GrpcReportingSink:
    """Pushes FixCompleted events to a gRPC Reporting service."""

    def __init__(self, endpoint: str) -> None:
        """Initialize with target endpoint.

        Args:
            endpoint: ``host:port`` of the Reporting gRPC service.
        """
        self._endpoint = endpoint
        self._channel: grpc.aio.Channel | None = None
        self._stub: reporting_pb2_grpc.ReportingStub | None = None
        self._available = False
        self._health_task: asyncio.Task[None] | None = None
        self._dropped_events = 0

    @property
    def dropped_events(self) -> int:
        """Number of fix events dropped before a stub was available.

        Logged per drop (see :meth:`on_fix_completed`) so silent loss is
        observable in the engine log; this counter exposes the same count
        programmatically for health checks and tests.

        Returns:
            Count of dropped events since construction.
        """
        return self._dropped_events

    def _record_drop(self, scan_id: str) -> None:
        """Count a dropped event, log it, and emit the metrics hook.

        Args:
            scan_id: Scan id of the dropped event (for the log line).
        """
        self._dropped_events += 1
        logger.warning(
            "Dropping FixCompletedEvent scan_id=%s: reporting stub not initialized (%s)",
            scan_id,
            self._endpoint,
        )
        try:
            from apme_engine.observability import record_reporting_drop  # noqa: PLC0415
        except ImportError:
            return
        try:
            record_reporting_drop(self._endpoint)
        except Exception:  # noqa: BLE001 — metrics never fail delivery
            logger.debug("Failed to record reporting-drop metric", exc_info=True)

    async def start(self) -> None:
        """Open channel, create stub, probe with retries, then launch health-check loop."""
        self._channel = grpc.aio.insecure_channel(
            self._endpoint,
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )
        self._stub = reporting_pb2_grpc.ReportingStub(self._channel)  # type: ignore[no-untyped-call]
        for attempt in range(1, _STARTUP_PROBE_RETRIES + 1):
            await self._probe()
            if self._available:
                break
            if attempt < _STARTUP_PROBE_RETRIES:
                logger.info(
                    "Reporting endpoint not ready, retrying in %.0fs (%d/%d)",
                    _STARTUP_PROBE_DELAY_S,
                    attempt,
                    _STARTUP_PROBE_RETRIES,
                )
                await asyncio.sleep(_STARTUP_PROBE_DELAY_S)
        if not self._available:
            logger.warning(
                "Reporting endpoint %s not available after %d startup probes; "
                "events will be delivered once the endpoint becomes healthy",
                self._endpoint,
                _STARTUP_PROBE_RETRIES,
            )
        self._health_task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        """Cancel health loop and close channel."""
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
        if self._channel:
            await self._channel.close(grace=None)

    async def on_fix_completed(self, event: reporting_pb2.FixCompletedEvent) -> None:
        """Push fix event to the Reporting service.

        Small events use unary ``ReportFixCompleted``. Oversized events
        (approaching the 50 MiB gRPC ceiling) use client-streaming
        ``ReportFixCompletedStream`` (ADR-020). Uses a fast-fail timeout
        when the endpoint is known-down. When no stub is initialized the
        event is dropped via :meth:`_record_drop` (counted in
        :attr:`dropped_events` and logged so silent drops are observable).

        Args:
            event: Completed fix event to deliver.
        """
        if self._stub is None:
            self._record_drop(event.scan_id)
            return
        loop = asyncio.get_running_loop()
        event_size = await loop.run_in_executor(None, event.ByteSize)
        stream = event_size >= UNARY_MAX_BYTES
        if not self._available:
            timeout = _FAST_FAIL_TIMEOUT_S
        elif stream:
            timeout = _STREAM_TIMEOUT_S
        else:
            timeout = _TIMEOUT_S
        try:
            if stream:
                logger.info(
                    "Streaming FixCompletedEvent scan_id=%s (%d bytes) to %s",
                    event.scan_id,
                    event_size,
                    self._endpoint,
                )
                await self._stub.ReportFixCompletedStream(
                    _async_chunks(event),
                    timeout=timeout,
                )
            else:
                await self._stub.ReportFixCompleted(event, timeout=timeout)
            if not self._available:
                logger.info("Reporting endpoint recovered (fix delivery): %s", self._endpoint)
                self._available = True
        except Exception:
            logger.warning(
                "Failed to emit FixCompletedEvent scan_id=%s to %s",
                event.scan_id,
                self._endpoint,
                exc_info=True,
            )
            self._available = False

    async def register_rules(
        self,
        request: reporting_pb2.RegisterRulesRequest,
    ) -> reporting_pb2.RegisterRulesResponse | None:
        """Push rule catalog to the Reporting service (ADR-041).

        Args:
            request: Registration payload with the full rule set.

        Returns:
            Response from the service, or None if the call failed.
        """
        if self._stub is None:
            return None
        timeout = _TIMEOUT_S if self._available else _FAST_FAIL_TIMEOUT_S
        try:
            resp = await self._stub.RegisterRules(request, timeout=timeout)
            if not self._available:
                logger.info("Reporting endpoint recovered (rule registration): %s", self._endpoint)
                self._available = True
            return resp  # type: ignore[no-any-return]
        except Exception:
            logger.warning(
                "Failed to register rules (%d rules) to %s",
                len(request.rules),
                self._endpoint,
                exc_info=True,
            )
            self._available = False
            return None

    async def _probe(self) -> None:
        """Single gRPC health probe — sets ``_available`` accordingly.

        Raises:
            asyncio.CancelledError: Re-raised for clean task cancellation.
        """
        from grpc_health.v1 import health_pb2, health_pb2_grpc

        try:
            stub = health_pb2_grpc.HealthStub(self._channel)
            await stub.Check(health_pb2.HealthCheckRequest(), timeout=5)
            if not self._available:
                logger.info("Reporting endpoint available: %s", self._endpoint)
            self._available = True
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._available:
                logger.warning("Reporting endpoint unavailable: %s", self._endpoint)
            self._available = False

    async def _health_loop(self) -> None:
        """Periodically probe the endpoint via gRPC health check."""
        while True:
            await asyncio.sleep(_HEALTH_INTERVAL_S)
            await self._probe()
