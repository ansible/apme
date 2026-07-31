"""gRPC aio server interceptor that records RPC durations via OTel metrics."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import grpc
from grpc import aio

logger = logging.getLogger("apme.observability.grpc")


def _method_label(full_method: str) -> str:
    """Return the bare RPC method name from a full gRPC path.

    Args:
        full_method: Path like ``/apme.v1.Validator/Validate``.

    Returns:
        Trailing segment (e.g. ``Validate``), or ``unknown``.
    """
    if not full_method:
        return "unknown"
    return full_method.rsplit("/", 1)[-1] or "unknown"


def _status_label(context: Any) -> str:
    """Map the active servicer context code to a stable label.

    Args:
        context: Active gRPC aio servicer context.

    Returns:
        Status code name (e.g. ``OK``, ``INTERNAL``).
    """
    try:
        code = context.code()
    except Exception:  # noqa: BLE001 — best-effort status only
        return "UNKNOWN"
    if code is None:
        return "OK"
    return getattr(code, "name", None) or str(code)


class GrpcMetricsInterceptor(aio.ServerInterceptor):  # type: ignore[type-arg]
    """Record ``apme.grpc.server.duration`` for unary RPCs.

    Metrics failures are swallowed so observability never breaks the RPC.
    """

    def __init__(self, *, service: str) -> None:
        """Tag metrics with a logical service name.

        Args:
            service: Short validator label (``native``, ``opa``, …).
        """
        self.service = service

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[Any]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """Wrap the resolved handler to time unary-unary RPCs.

        Args:
            continuation: Next interceptor / handler lookup.
            handler_call_details: Incoming call metadata.

        Returns:
            Possibly wrapped ``RpcMethodHandler``, or ``None``.
        """
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        method = _method_label(handler_call_details.method)
        if handler.unary_unary is None:
            return handler

        original = handler.unary_unary

        async def unary_unary_wrapper(request: Any, context: Any) -> Any:
            started = time.perf_counter()
            status = "OK"
            try:
                return await original(request, context)
            except Exception:
                status = _status_label(context)
                if status == "OK":
                    status = "UNKNOWN"
                raise
            finally:
                if status == "OK":
                    status = _status_label(context)
                try:
                    from apme_engine.observability.metrics import record_grpc_request

                    record_grpc_request(
                        time.perf_counter() - started,
                        method=method,
                        status_code=status,
                        service=self.service,
                    )
                except Exception:  # noqa: BLE001 — metrics must never break RPCs
                    logger.debug("Failed to record gRPC metrics", exc_info=True)

        return grpc.unary_unary_rpc_method_handler(
            unary_unary_wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
