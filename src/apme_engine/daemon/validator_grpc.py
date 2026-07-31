"""Shared helpers for APME Validator gRPC aio servers."""

from __future__ import annotations

import grpc
import grpc.aio

from apme.v1 import validate_pb2_grpc
from apme_engine.observability.grpc_middleware import GrpcMetricsInterceptor

_DEFAULT_OPTIONS: list[tuple[str, int]] = [
    ("grpc.max_receive_message_length", 50 * 1024 * 1024),
    ("grpc.max_send_message_length", 50 * 1024 * 1024),
]


async def start_validator_server(
    servicer: validate_pb2_grpc.ValidatorServicer,
    listen: str,
    *,
    service: str,
    max_concurrent_rpcs: int,
) -> grpc.aio.Server:
    """Create, bind, and start a Validator gRPC server with metrics middleware.

    Args:
        servicer: Concrete ``ValidatorServicer`` implementation.
        listen: Address passed to ``add_insecure_port`` (e.g. ``0.0.0.0:50059``).
        service: Short service label for OTel attributes (``native``, ``opa``, …).
        max_concurrent_rpcs: gRPC concurrent RPC limit for this process.

    Returns:
        Started gRPC server (caller must ``wait_for_termination``).
    """
    server = grpc.aio.server(
        interceptors=[GrpcMetricsInterceptor(service=service)],
        maximum_concurrent_rpcs=max_concurrent_rpcs,
        options=list(_DEFAULT_OPTIONS),
    )
    validate_pb2_grpc.add_ValidatorServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    # Honor the configured address (e.g. 0.0.0.0:50059 or 127.0.0.1:50059).
    # Do not rewrite host:port to [::]:port — that would expand loopback to a wildcard.
    server.add_insecure_port(listen)
    await server.start()
    return server
