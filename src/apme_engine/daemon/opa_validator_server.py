"""OPA validator daemon: async gRPC server that evaluates Rego policies via local OPA binary.

Uses the same OpaValidator (opa_client.run_opa) as the in-process path.
No OPA REST server required — the OPA binary is invoked via subprocess.
"""

import asyncio
import json
import os
import sys
import time
from typing import cast

import grpc
import grpc.aio

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.validators.base import ScanContext
from apme_engine.validators.opa import OpaValidator

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_OPA_MAX_RPCS", "32"))


def _run_opa(hierarchy_payload: dict[str, object]) -> list[ViolationDict]:
    """Blocking function: create ScanContext and run OpaValidator.

    Args:
        hierarchy_payload: Parsed hierarchy payload for context.

    Returns:
        List of violation dicts from OPA evaluation.
    """
    scan_context = ScanContext(
        hierarchy_payload=cast(YAMLDict, hierarchy_payload),
        scandata=None,
    )
    validator = OpaValidator()
    return validator.run(scan_context)


class OpaValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: deserializes hierarchy, runs OPA eval in executor."""

    async def Validate(
        self,
        request: validate_pb2.ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> validate_pb2.ValidateResponse:
        """Handle Validate RPC: deserialize hierarchy_payload, run OPA in executor.

        Args:
            request: ValidateRequest with hierarchy_payload.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        violations: list[ViolationDict] = []
        try:
            hierarchy_payload: dict[str, object] = {}
            if request.hierarchy_payload:
                try:
                    hierarchy_payload = json.loads(request.hierarchy_payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    sys.stderr.write(f"[req={req_id}] OPA: failed to decode hierarchy_payload\n")
                    return ValidateResponse(violations=[], request_id=req_id)

            violations = await asyncio.get_event_loop().run_in_executor(
                None,
                _run_opa,
                hierarchy_payload,
            )
            total_ms = (time.monotonic() - t0) * 1000
            sys.stderr.write(f"[req={req_id}] OPA: {len(violations)} violation(s) in {total_ms:.1f}ms\n")
            sys.stderr.flush()
        except Exception as e:
            import traceback

            sys.stderr.write(f"[req={req_id}] OPA error: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            return ValidateResponse(violations=[], request_id=req_id)

        total_ms = (time.monotonic() - t0) * 1000

        from collections import Counter

        rule_counts = Counter(v.get("rule_id", "unknown") for v in violations)
        rule_timings = [
            RuleTiming(rule_id=str(rid), elapsed_ms=0.0, violations=count) for rid, count in sorted(rule_counts.items())
        ]

        diag = ValidatorDiagnostics(
            validator_name="opa",
            request_id=req_id,
            total_ms=total_ms,
            files_received=len(request.files),
            violations_found=len(violations),
            rule_timings=rule_timings,
        )

        return ValidateResponse(
            violations=[violation_dict_to_proto(v) for v in violations],
            request_id=req_id,
            diagnostics=diag,
        )

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


async def serve(listen: str = "0.0.0.0:50054") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with OPA servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50054).

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
    validate_pb2_grpc.add_ValidatorServicer_to_server(OpaValidatorServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen:
        _, _, port = listen.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen)
    await server.start()
    return server
