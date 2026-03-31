"""Native validator daemon: async gRPC server that runs GraphRules on deserialized ContentGraph.

Also keeps the legacy ``detect()`` path as a dual-run for validation
during the ADR-044 switchover.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import cast

import grpc
import grpc.aio
import jsonpickle

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.content_graph import ContentGraph
from apme_engine.engine.graph_scanner import (
    GraphScanReport,
    graph_report_to_violations,
    load_graph_rules,
)
from apme_engine.engine.graph_scanner import (
    scan as graph_scan,
)
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.log_bridge import attach_collector
from apme_engine.validators.base import ScanContext
from apme_engine.validators.native import NativeRunResult, NativeValidator

logger = logging.getLogger("apme.native")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_NATIVE_MAX_RPCS", "32"))


def _run_native(hierarchy_payload: dict[str, object], scandata: object) -> NativeRunResult:
    """Blocking function: create ScanContext and run NativeValidator with timing (legacy).

    Args:
        hierarchy_payload: Parsed hierarchy payload for context.
        scandata: Deserialized scandata object.

    Returns:
        NativeRunResult with violations and rule timings.
    """
    scan_context = ScanContext(
        hierarchy_payload=cast(YAMLDict, hierarchy_payload),
        scandata=scandata,
    )
    validator = NativeValidator()
    return validator.run_with_timing(scan_context)


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
        _GraphRunResult with violations, timings, and the raw report.
    """
    graph_dict = json.loads(raw_graph_data)
    content_graph = ContentGraph.from_dict(graph_dict)
    rules_dir = _default_rules_dir()
    rules = load_graph_rules(rules_dir=rules_dir)
    report = graph_scan(content_graph, rules)
    violations = graph_report_to_violations(report)
    return _GraphRunResult(violations=violations, report=report)


class NativeValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: deserializes scandata, runs native rules in executor."""

    async def Validate(
        self,
        request: validate_pb2.ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: run GraphRules on ContentGraph, legacy detect() as fallback.

        Args:
            request: ValidateRequest with content_graph_data and/or scandata.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                logger.info("Native: validate start (req=%s)", req_id)

                # --- Graph path (primary) ---
                graph_result: _GraphRunResult | None = None
                if request.content_graph_data:
                    try:
                        graph_result = await asyncio.get_event_loop().run_in_executor(
                            None,
                            _run_graph,
                            request.content_graph_data,
                        )
                        logger.debug(
                            "Native: graph path done (%d violations, req=%s)",
                            len(graph_result.violations),
                            req_id,
                        )
                    except Exception:
                        logger.warning(
                            "Native: graph path failed, falling back to legacy (req=%s)", req_id, exc_info=True
                        )

                # --- Legacy path (fallback / dual-run comparison) ---
                legacy_result: NativeRunResult | None = None
                hierarchy_payload: dict[str, object] = {}
                if request.hierarchy_payload:
                    try:
                        hierarchy_payload = json.loads(request.hierarchy_payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("Native: failed to decode hierarchy_payload (req=%s)", req_id)

                if request.scandata:
                    try:
                        from apme_engine.engine import jsonpickle_handlers as _jp  # noqa: F401
                        from apme_engine.engine import models as _models  # noqa: F401
                        from apme_engine.engine import scanner as _scanner  # noqa: F401

                        _jp.register_engine_handlers()
                        for name in ("SingleScan",):
                            getattr(_scanner, name, None)
                        for name in (
                            "AnsibleRunContext",
                            "RunTargetList",
                            "RunTarget",
                            "TaskCall",
                            "Object",
                        ):
                            getattr(_models, name, None)
                        scandata = jsonpickle.decode(request.scandata.decode("utf-8"))
                        legacy_result = await asyncio.get_event_loop().run_in_executor(
                            None,
                            _run_native,
                            hierarchy_payload,
                            scandata,
                        )
                    except Exception:
                        logger.warning("Native: legacy path failed (req=%s)", req_id, exc_info=True)

                # --- Dual-run comparison logging ---
                if graph_result is not None and legacy_result is not None:
                    graph_ids: set[str] = {str(v.get("rule_id", "")) for v in graph_result.violations}
                    legacy_ids: set[str] = {str(v.get("rule_id", "")) for v in legacy_result.violations}
                    only_graph = graph_ids - legacy_ids
                    only_legacy = legacy_ids - graph_ids
                    if only_graph or only_legacy:
                        logger.info(
                            "Native dual-run diff (req=%s): graph-only=%s legacy-only=%s",
                            req_id,
                            sorted(only_graph),
                            sorted(only_legacy),
                        )

                # --- Produce response (graph primary, legacy fallback) ---
                violations_out: list[ViolationDict]
                if graph_result is not None:
                    violations_out = graph_result.violations
                    source_label = "graph"
                elif legacy_result is not None:
                    violations_out = [cast(ViolationDict, v) for v in legacy_result.violations]
                    source_label = "legacy"
                else:
                    violations_out = []
                    source_label = "none"

                total_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Native: validate done [%s] (%.0fms, %d violations, req=%s)",
                    source_label,
                    total_ms,
                    len(violations_out),
                    req_id,
                )

                rule_timings: list[RuleTiming] = []
                if legacy_result is not None:
                    rule_timings = [
                        RuleTiming(
                            rule_id=rt.rule_id,
                            elapsed_ms=rt.elapsed_ms,
                            violations=rt.violations,
                        )
                        for rt in legacy_result.rule_timings
                    ]
                diag = ValidatorDiagnostics(
                    validator_name="native",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=len(request.files),
                    violations_found=len(violations_out),
                    rule_timings=rule_timings,
                )

                return validate_pb2.ValidateResponse(
                    violations=[violation_dict_to_proto(v) for v in violations_out],
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
