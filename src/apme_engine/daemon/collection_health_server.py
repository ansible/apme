"""Collection Health validator daemon: async gRPC server (ADR-051).

Scans Ansible collections installed in session venvs with a curated
subset of APME's native rules.  Findings are scoped to
``RuleScope.COLLECTION``.  Follows the Gitleaks optional-validator pattern.
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
from apme_engine.validators.collection_health.scanner import scan_collections

logger = logging.getLogger("apme.collection_health")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_COLLECTION_HEALTH_MAX_RPCS", "4"))
_SESSIONS_ROOT = Path(os.environ.get("APME_SESSIONS_ROOT", "/sessions"))


def _run_scan(venv_path: str, rescan: bool) -> list[ViolationDict]:
    """Blocking wrapper for collection health scanning.

    Args:
        venv_path: Path to session venv root.
        rescan: If True, bust the findings cache.

    Returns:
        List of violation dicts from collection scanning.
    """
    venv_dir = Path(venv_path).resolve()
    if not venv_dir.is_relative_to(_SESSIONS_ROOT.resolve()):
        logger.warning("Collection health: venv_path outside sessions root: %s", venv_path)
        return []
    if not venv_dir.is_dir():
        logger.warning("Collection health: venv_path not a directory: %s", venv_path)
        return []
    return scan_collections(venv_dir, rescan=rescan)


class CollectionHealthValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: scans collections in executor thread."""

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: scan installed collections in session venv.

        Args:
            request: ValidateRequest with venv_path.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with collection-scoped violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                venv_path = request.venv_path
                if not venv_path:
                    logger.info("Collection health: no venv_path in request (req=%s)", req_id)
                    return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                logger.info(
                    "Collection health: validate start (venv=%s, req=%s)",
                    venv_path,
                    req_id,
                )

                violations = await asyncio.get_running_loop().run_in_executor(
                    None,
                    _run_scan,
                    venv_path,
                    False,
                )

                total_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Collection health: validate done (%.0fms, %d findings, req=%s)",
                    total_ms,
                    len(violations),
                    req_id,
                )

                diag = ValidatorDiagnostics(
                    validator_name="collection_health",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=0,
                    violations_found=len(violations),
                    rule_timings=[
                        RuleTiming(
                            rule_id="collection_scan",
                            elapsed_ms=total_ms,
                            violations=len(violations),
                        ),
                    ],
                    metadata={
                        "scan_ms": f"{total_ms:.1f}",
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
                logger.exception("Collection health: unhandled error (req=%s): %s", req_id, e)
                return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

    async def Health(
        self,
        request: common_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Handle Health RPC: collection health scanner is always available.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with status "ok".
        """
        return HealthResponse(status="ok")


async def serve(listen: str = "0.0.0.0:50058") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with CollectionHealth servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50058).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    from apme_engine.daemon.validator_grpc import start_validator_server

    return await start_validator_server(
        CollectionHealthValidatorServicer(),
        listen,
        service="collection_health",
        max_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
    )
