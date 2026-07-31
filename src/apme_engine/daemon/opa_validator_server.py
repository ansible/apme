"""OPA validator daemon: async gRPC server that evaluates Rego policies via local OPA binary.

Uses the same OpaValidator (opa_client.run_opa) as the in-process path.
No OPA REST server required — the OPA binary is invoked via subprocess.
"""

import asyncio
import contextvars
import json
import logging
import os
import time
from typing import cast

import grpc
import grpc.aio

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateResponse
from apme_engine.daemon.validator_errors import infra_error_response
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.log_bridge import attach_collector
from apme_engine.validators.base import ScanContext
from apme_engine.validators.opa import OpaValidator

logger = logging.getLogger("apme.opa")

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
        with attach_collector() as sink:
            violations: list[ViolationDict] = []
            try:
                logger.info("OPA: validate start (req=%s)", req_id)

                hierarchy_payload: dict[str, object] = {}
                if request.hierarchy_payload:
                    try:
                        hierarchy_payload = json.loads(request.hierarchy_payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("OPA: failed to decode hierarchy_payload (req=%s)", req_id)
                        return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                ctx = contextvars.copy_context()
                violations = await asyncio.get_event_loop().run_in_executor(
                    None,
                    ctx.run,
                    _run_opa,
                    hierarchy_payload,
                )
                total_ms = (time.monotonic() - t0) * 1000
                logger.info("OPA: validate done (%.0fms, %d violations, req=%s)", total_ms, len(violations), req_id)
            except Exception:
                logger.error("OPA: unhandled error (req=%s): [REDACTED]", req_id)
                return infra_error_response(req_id, sink.entries)

            total_ms = (time.monotonic() - t0) * 1000

            from collections import Counter

            rule_counts = Counter(v.get("rule_id", "unknown") for v in violations)
            rule_timings = [
                RuleTiming(rule_id=str(rid), elapsed_ms=0.0, violations=count)
                for rid, count in sorted(rule_counts.items())
            ]

            diag = ValidatorDiagnostics(
                validator_name="opa",
                request_id=req_id,
                total_ms=total_ms,
                files_received=len(request.files),
                violations_found=len(violations),
                rule_timings=rule_timings,
            )

            for v in violations:
                v.setdefault("source", "opa")

            return ValidateResponse(
                violations=[violation_dict_to_proto(v) for v in violations],
                request_id=req_id,
                diagnostics=diag,
                logs=sink.entries,
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
    from apme_engine.daemon.validator_grpc import start_validator_server

    return await start_validator_server(
        OpaValidatorServicer(),
        listen,
        service="opa",
        max_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
    )
