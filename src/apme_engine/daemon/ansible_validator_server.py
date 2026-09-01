"""Ansible validator daemon: async gRPC adapter using session-scoped venvs.

The Engine orchestrator owns venv lifecycle (creation, collection install,
reaping).  This validator receives a ready-to-use ``venv_path`` in every
``ValidateRequest`` and runs Ansible rules against it read-only.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import grpc.aio

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.fs_utils import write_chunked_fs
from apme_engine.daemon.validator_errors import (
    PUBLIC_VALIDATOR_ERROR,
    RULE_MISSING_VENV,
    infra_error_response,
    infra_violation,
)
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.log_bridge import attach_collector
from apme_engine.validators.ansible import AnsibleRunResult, AnsibleValidator
from apme_engine.validators.ansible._venv import DEFAULT_VERSION
from apme_engine.validators.base import ScanContext

logger = logging.getLogger("apme.ansible")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_ANSIBLE_MAX_RPCS", "8"))


@dataclass
class _AnsibleResult:
    """Result of running Ansible validator with timing metadata.

    Attributes:
        run_result: Violations and rule timings from AnsibleValidator.
        ansible_core_version: Ansible core version string used.
    """

    run_result: AnsibleRunResult
    ansible_core_version: str = ""


def _run_ansible_validate(
    files: list[File],
    raw_version: str,
    hierarchy_payload: YAMLDict,
    req_id: str,
    venv_path: str,
    content_graph_data: bytes = b"",
) -> _AnsibleResult:
    """Run Ansible validation against a session-scoped venv provided by Engine.

    Args:
        files: List of File protos to validate.
        raw_version: Ansible core version string.
        hierarchy_payload: Parsed hierarchy payload for context.
        req_id: Request ID for logging.
        venv_path: Session venv path from Engine (read-only).
        content_graph_data: Serialized ContentGraph for L057 node resolution.

    Returns:
        _AnsibleResult with violations and version.
    """
    temp_dir = None
    venv_root = Path(venv_path) if venv_path else None

    try:
        temp_dir = write_chunked_fs(list(files), prefix="apme_ansible_val_")

        if venv_root is None:
            logger.warning("Ansible: no venv_path provided, skipping (req=%s)", req_id)
            err_viol = infra_violation(
                "No session venv provided by Engine orchestrator",
                rule_id=RULE_MISSING_VENV,
            )
            return _AnsibleResult(
                run_result=AnsibleRunResult(violations=[err_viol]),  # type: ignore[list-item]
                ansible_core_version=raw_version,
            )

        logger.debug("Ansible: using session venv %s (req=%s)", venv_path, req_id)

        scan_context = ScanContext(
            hierarchy_payload=hierarchy_payload,
            root_dir=str(temp_dir),
        )
        validator = AnsibleValidator(venv_root=venv_root)
        run_result = validator.run_with_timing(scan_context, content_graph_data=content_graph_data)
        return _AnsibleResult(
            run_result=run_result,
            ansible_core_version=raw_version,
        )
    except Exception as e:
        logger.exception("Ansible: error in blocking executor (req=%s): %s", req_id, e)
        err_viol_exc = infra_violation(PUBLIC_VALIDATOR_ERROR)
        return _AnsibleResult(
            run_result=AnsibleRunResult(violations=[err_viol_exc]),  # type: ignore[list-item]
            ansible_core_version=raw_version,
        )
    finally:
        if temp_dir is not None and temp_dir.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(temp_dir)


class AnsibleValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: runs AnsibleValidator against a session venv from Engine."""

    async def Validate(self, request: ValidateRequest, context: grpc.aio.ServicerContext) -> ValidateResponse:  # type: ignore[type-arg]
        """Handle Validate RPC: run AnsibleValidator against session venv.

        Args:
            request: ValidateRequest with files, version, and venv_path.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                if not request.files and not request.hierarchy_payload:
                    return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                raw_version = (request.ansible_core_version or "").strip() or DEFAULT_VERSION

                hierarchy_payload: YAMLDict = {}
                if request.hierarchy_payload:
                    try:
                        hierarchy_payload = cast(YAMLDict, json.loads(request.hierarchy_payload))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("Ansible: failed to parse hierarchy_payload (req=%s)", req_id)

                logger.info(
                    "Ansible: validate start (%d files, core=%s, req=%s)",
                    len(request.files),
                    raw_version,
                    req_id,
                )

                ctx = contextvars.copy_context()
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    ctx.run,  # type: ignore[arg-type]
                    _run_ansible_validate,
                    list(request.files),
                    raw_version,
                    hierarchy_payload,
                    req_id,
                    request.venv_path or "",
                    bytes(request.content_graph_data),
                )

                total_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Ansible: validate done (%.0fms, %d violations, req=%s)",
                    total_ms,
                    len(result.run_result.violations),
                    req_id,
                )

                rule_timings = [
                    RuleTiming(
                        rule_id=rt.rule_id,
                        elapsed_ms=rt.elapsed_ms,
                        violations=rt.violations,
                    )
                    for rt in result.run_result.rule_timings
                ]
                diag_metadata: dict[str, str] = {
                    "ansible_core_version": result.ansible_core_version,
                }
                for mk, mv in result.run_result.metadata.items():
                    diag_metadata[mk] = str(mv)
                diag = ValidatorDiagnostics(
                    validator_name="ansible",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=len(request.files),
                    violations_found=len(result.run_result.violations),
                    rule_timings=rule_timings,
                    metadata=diag_metadata,
                )

                for v in result.run_result.violations:
                    v.setdefault("source", "ansible")

                return validate_pb2.ValidateResponse(
                    violations=[violation_dict_to_proto(cast(ViolationDict, v)) for v in result.run_result.violations],
                    request_id=req_id,
                    diagnostics=diag,
                    logs=sink.entries,
                )
            except Exception as e:
                logger.exception("Ansible: unhandled error (req=%s): %s", req_id, e)
                return infra_error_response(req_id, sink.entries)

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


async def serve(listen: str = "0.0.0.0:50053") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Ansible servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50053).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    from apme_engine.daemon.validator_grpc import start_validator_server

    return await start_validator_server(
        AnsibleValidatorServicer(),
        listen,
        service="ansible",
        max_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
    )
