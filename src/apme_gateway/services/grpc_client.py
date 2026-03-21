"""Async gRPC client wrapper for talking to the Primary service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import grpc
import grpc.aio

from apme.v1 import primary_pb2_grpc
from apme.v1.common_pb2 import HealthRequest
from apme.v1.primary_pb2 import ScanChunk, SessionCommand

if TYPE_CHECKING:
    from apme.v1.common_pb2 import HealthResponse
    from apme.v1.primary_pb2 import FormatResponse, ScanResponse, SessionEvent
    from apme_gateway.config import GatewayConfig

logger = logging.getLogger(__name__)


class PrimaryClient:
    """Thin async wrapper around the Primary gRPC stub.

    Manages a persistent channel that is opened on first use and closed
    explicitly via ``close()``.
    """

    def __init__(self, config: GatewayConfig) -> None:
        """Initialise with gateway configuration.

        Args:
            config: Gateway configuration with Primary address and limits.
        """
        self._config = config
        self._channel: grpc.aio.Channel | None = None
        self._stub: primary_pb2_grpc.PrimaryStub | None = None

    def _ensure_channel(self) -> primary_pb2_grpc.PrimaryStub:
        if self._channel is None or self._stub is None:
            max_msg = self._config.grpc_max_message_bytes
            self._channel = grpc.aio.insecure_channel(
                self._config.primary_address,
                options=[
                    ("grpc.max_send_message_length", max_msg),
                    ("grpc.max_receive_message_length", max_msg),
                ],
            )
            self._stub = primary_pb2_grpc.PrimaryStub(self._channel)  # type: ignore[no-untyped-call]
        return self._stub

    async def close(self) -> None:
        """Shut down the gRPC channel."""
        if self._channel is not None:
            await self._channel.close(grace=None)
            self._channel = None
            self._stub = None

    # ------------------------------------------------------------------
    # Unary RPCs
    # ------------------------------------------------------------------

    async def health(self) -> HealthResponse:
        """Call Primary.Health.

        Returns:
            HealthResponse from the engine.
        """
        stub = self._ensure_channel()
        return await stub.Health(HealthRequest(), timeout=self._config.grpc_timeout)  # type: ignore[no-any-return]

    async def scan_stream(self, chunks: AsyncIterator[ScanChunk]) -> ScanResponse:
        """Stream ScanChunk messages and return the aggregate ScanResponse.

        Args:
            chunks: Async iterator of ScanChunk messages.

        Returns:
            ScanResponse with violations, diagnostics, and summary.
        """
        stub = self._ensure_channel()
        return await stub.ScanStream(chunks, timeout=self._config.grpc_timeout)  # type: ignore[no-any-return]

    async def format_stream(self, chunks: AsyncIterator[ScanChunk]) -> FormatResponse:
        """Stream ScanChunk messages for formatting.

        Args:
            chunks: Async iterator of ScanChunk messages.

        Returns:
            FormatResponse with file diffs.
        """
        stub = self._ensure_channel()
        return await stub.FormatStream(chunks, timeout=self._config.grpc_timeout)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Bidi streaming (FixSession)
    # ------------------------------------------------------------------

    def fix_session(
        self,
        commands: AsyncIterator[SessionCommand],
    ) -> grpc.aio.StreamStreamCall[SessionCommand, SessionEvent]:
        """Open a FixSession bidi stream.

        Args:
            commands: Async iterator of SessionCommand messages.

        Returns:
            The bidi stream call object (async-iterable of SessionEvent).
        """
        stub = self._ensure_channel()
        return stub.FixSession(commands, timeout=600)  # type: ignore[no-any-return]
