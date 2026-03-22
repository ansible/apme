"""gRPC reporting sink -- pushes events to a Reporting service (ADR-020).

Health-gated: a background task probes the endpoint every 30 s.
When the service is down, emit calls skip instantly (no timeout penalty).
When it recovers, emission resumes automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import grpc
import grpc.aio

from apme.v1 import reporting_pb2, reporting_pb2_grpc

logger = logging.getLogger("apme.events.grpc")

_TIMEOUT_S = 2.0
_HEALTH_INTERVAL_S = 30.0


class GrpcReportingSink:
    """Pushes ScanCompleted / FixCompleted events to a gRPC Reporting service."""

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

    async def start(self) -> None:
        """Open channel, create stub, probe once, then launch health-check loop."""
        self._channel = grpc.aio.insecure_channel(self._endpoint)
        self._stub = reporting_pb2_grpc.ReportingStub(self._channel)  # type: ignore[no-untyped-call]
        await self._probe()
        self._health_task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        """Cancel health loop and close channel."""
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
        if self._channel:
            await self._channel.close(grace=None)

    async def on_scan_completed(self, event: reporting_pb2.ScanCompletedEvent) -> None:
        """Push scan event; silently skip if endpoint is unavailable.

        Args:
            event: Completed scan event to deliver.
        """
        if not self._available or self._stub is None:
            return
        try:
            await self._stub.ReportScanCompleted(event, timeout=_TIMEOUT_S)
        except Exception:
            self._available = False
            logger.warning("Failed to emit ScanCompletedEvent scan_id=%s", event.scan_id)

    async def on_fix_completed(self, event: reporting_pb2.FixCompletedEvent) -> None:
        """Push fix event; silently skip if endpoint is unavailable.

        Args:
            event: Completed fix event to deliver.
        """
        if not self._available or self._stub is None:
            return
        try:
            await self._stub.ReportFixCompleted(event, timeout=_TIMEOUT_S)
        except Exception:
            self._available = False
            logger.warning("Failed to emit FixCompletedEvent scan_id=%s", event.scan_id)

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
