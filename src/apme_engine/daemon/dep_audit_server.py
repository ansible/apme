"""Python Dependency Auditor daemon: async gRPC server for CVE scanning (ADR-051).

Runs ``pip-audit`` against session venv site-packages via
``run_in_executor()`` and maps findings to ``R200`` violations.
Follows the Gitleaks optional-validator pattern.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import grpc
import grpc.aio

from apme.v1 import common_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict
from apme_engine.log_bridge import attach_collector
from apme_engine.validators.dep_audit.auditor import (
    pip_audit_available,
    run_pip_audit,
)

logger = logging.getLogger("apme.dep_audit")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_DEP_AUDIT_MAX_RPCS", "8"))
_SESSIONS_ROOT = Path(os.environ.get("APME_SESSIONS_ROOT", "/sessions"))


def _run_audit(venv_path: str) -> list[ViolationDict]:
    """Blocking wrapper for pip-audit execution.

    Args:
        venv_path: Path to session venv root.

    Returns:
        List of violation dicts from pip-audit.
    """
    venv_dir = Path(venv_path).resolve()
    if not venv_dir.is_relative_to(_SESSIONS_ROOT.resolve()):
        logger.warning("Dep audit: venv_path outside sessions root: %s", venv_path)
        return []
    if not venv_dir.is_dir():
        logger.warning("Dep audit: venv_path not a directory: %s", venv_path)
        return []
    return run_pip_audit(venv_dir)


class DepAuditValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: runs pip-audit in executor thread."""

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: audit Python deps in session venv for CVEs.

        Args:
            request: ValidateRequest with venv_path.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with R200 violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                venv_path = request.venv_path
                if not venv_path:
                    logger.info("Dep audit: no venv_path in request (req=%s)", req_id)
                    return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                logger.info("Dep audit: validate start (venv=%s, req=%s)", venv_path, req_id)

                violations = await asyncio.get_running_loop().run_in_executor(
                    None,
                    _run_audit,
                    venv_path,
                )

                total_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Dep audit: validate done (%.0fms, %d findings, req=%s)",
                    total_ms,
                    len(violations),
                    req_id,
                )

                diag = ValidatorDiagnostics(
                    validator_name="dep_audit",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=0,
                    violations_found=len(violations),
                    rule_timings=[
                        RuleTiming(
                            rule_id="pip_audit_subprocess",
                            elapsed_ms=total_ms,
                            violations=len(violations),
                        ),
                    ],
                    metadata={
                        "subprocess_ms": f"{total_ms:.1f}",
                        "venv_path": venv_path,
                    },
                )

                return ValidateResponse(
                    violations=[violation_dict_to_proto(v) for v in violations],
                    request_id=req_id,
                    diagnostics=diag,
                    logs=sink.entries,
                )
            except Exception as e:
                logger.exception("Dep audit: unhandled error (req=%s): %s", req_id, e)
                return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

    async def Health(
        self,
        request: common_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Handle Health RPC: verify pip-audit is available.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with pip-audit availability status.
        """
        loop = asyncio.get_running_loop()
        available, info = await loop.run_in_executor(None, pip_audit_available)
        if available:
            return HealthResponse(status=f"ok ({info})")
        return HealthResponse(status=f"pip-audit not available: {info}")


async def serve(listen: str = "0.0.0.0:50059") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with DepAudit servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50059).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    from apme_engine.daemon.validator_grpc import start_validator_server

    return await start_validator_server(
        DepAuditValidatorServicer(),
        listen,
        service="dep-audit",
        max_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
    )
