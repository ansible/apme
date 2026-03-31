"""Native validator daemon: async gRPC server that runs GraphRules on deserialized ContentGraph.

The legacy ``detect()`` + ``AnsibleRunContext`` path has been removed.
All native rule evaluation runs via ``ContentGraph`` + ``GraphRule``.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

import grpc
import grpc.aio

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import HealthResponse, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    GraphScanReport,
    graph_report_to_violations,
    load_graph_rules,
)
from apme_engine.engine.graph_scanner import scan as graph_scan
from apme_engine.engine.models import ViolationDict
from apme_engine.log_bridge import attach_collector

logger = logging.getLogger("apme.native")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_NATIVE_MAX_RPCS", "32"))


@dataclass
class _GraphRunResult:
    """Result of running GraphRules on a deserialized ContentGraph.

    Attributes:
        violations: Violation dicts produced by graph rules.
        report: Full scan report for diagnostics.
    """

    violations: list[ViolationDict] = field(default_factory=list)
    report: GraphScanReport | None = None


def _default_rules_dir() -> str:
    """Return default path to the native rules directory.

    Returns:
        Absolute path to ``validators/native/rules``.
    """
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "validators", "native", "rules")


def _run_graph(raw_graph_data: bytes) -> _GraphRunResult:
    """Blocking function: deserialize ContentGraph, load GraphRules, and scan.

    Deserialization happens here (not in the async handler) so that JSON
    parsing and graph construction run in the executor thread rather than
    blocking the gRPC event loop.

    Args:
        raw_graph_data: Raw JSON bytes from ``ValidateRequest.content_graph_data``.

    Returns:
        _GraphRunResult with violations and the raw report.
    """
    graph_dict = json.loads(raw_graph_data)
    content_graph = ContentGraph.from_dict(graph_dict)
    rules_dir = _default_rules_dir()
    rules = load_graph_rules(rules_dir=rules_dir)
    report = graph_scan(content_graph, rules)
    violations = graph_report_to_violations(report)
    return _GraphRunResult(violations=violations, report=report)


class NativeValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: deserializes ContentGraph, runs GraphRules in executor."""

    async def Validate(
        self,
        request: validate_pb2.ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: deserialize ContentGraph and run GraphRules.

        Args:
            request: ValidateRequest with content_graph_data.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                logger.info("Native: validate start (req=%s)", req_id)

                if not request.content_graph_data:
                    logger.warning("Native: no content_graph_data in request (req=%s)", req_id)
                    return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    _run_graph,
                    request.content_graph_data,
                )

                total_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Native: validate done (%.0fms, %d violations, req=%s)",
                    total_ms,
                    len(result.violations),
                    req_id,
                )

                diag = ValidatorDiagnostics(
                    validator_name="native",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=len(request.files),
                    violations_found=len(result.violations),
                )

                return validate_pb2.ValidateResponse(
                    violations=[violation_dict_to_proto(v) for v in result.violations],
                    request_id=req_id,
                    diagnostics=diag,
                    logs=sink.entries,
                )
            except Exception as e:
                logger.exception("Native: unhandled error (req=%s): %s", req_id, e)
                return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

    async def Health(
        self,
        request: common_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Handle Health RPC.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with status "ok".
        """
        return HealthResponse(status="ok")


async def serve(listen: str = "0.0.0.0:50055") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Native servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50055).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    server = grpc.aio.server(
        maximum_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
        options=[
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
        ],
    )
    validate_pb2_grpc.add_ValidatorServicer_to_server(NativeValidatorServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen:
        _, _, port = listen.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen)
    await server.start()
    return server
