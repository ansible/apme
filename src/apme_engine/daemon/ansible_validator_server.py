"""Ansible validator daemon: async gRPC adapter using session-scoped venvs.

The Primary orchestrator owns venv lifecycle (creation, collection install,
reaping).  This validator receives a ready-to-use ``venv_path`` in every
``ValidateRequest`` and runs Ansible rules against it read-only.
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import grpc.aio

from apme.v1 import common_pb2, validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
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


def _write_chunked_fs(files: list[File]) -> Path:
    """Write request.files into a temp directory; return path to that directory.

    Args:
        files: List of File protos with path and content.

    Returns:
        Path to the created temp directory.
    """
    tmp = Path(tempfile.mkdtemp(prefix="apme_ansible_val_"))
    for f in files:
        path = tmp / f.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.content)
    return tmp


def _run_ansible_validate(
    files: list[File],
    raw_version: str,
    hierarchy_payload: YAMLDict,
    req_id: str,
    venv_path: str,
) -> _AnsibleResult:
    """Run Ansible validation against a session-scoped venv provided by Primary.

    Args:
        files: List of File protos to validate.
        raw_version: Ansible core version string.
        hierarchy_payload: Parsed hierarchy payload for context.
        req_id: Request ID for logging.
        venv_path: Session venv path from Primary (read-only).

    Returns:
        _AnsibleResult with violations and version.
    """
    temp_dir = None
    venv_root = Path(venv_path) if venv_path else None

    try:
        temp_dir = _write_chunked_fs(files)

        if venv_root is None:
            logger.warning("Ansible: no venv_path provided, skipping (req=%s)", req_id)
            err_viol: ViolationDict = {
                "rule_id": "INFRA-001",
                "severity": "error",
                "message": "No session venv provided by Primary orchestrator",
                "file": "",
                "line": 1,
                "path": "",
            }
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
        run_result = validator.run_with_timing(scan_context)
        return _AnsibleResult(
            run_result=run_result,
            ansible_core_version=raw_version,
        )
    except Exception as e:
        logger.exception("Ansible: error in blocking executor (req=%s): %s", req_id, e)
        err_viol_exc: ViolationDict = {
            "rule_id": "INFRA-002",
            "severity": "error",
            "message": str(e),
            "file": "",
            "line": 1,
            "path": "",
        }
        return _AnsibleResult(
            run_result=AnsibleRunResult(violations=[err_viol_exc]),  # type: ignore[list-item]
            ansible_core_version=raw_version,
        )
    finally:
        if temp_dir is not None and temp_dir.is_dir():
            with contextlib.suppress(OSError):
                shutil.rmtree(temp_dir)


class AnsibleValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: runs AnsibleValidator against a session venv from Primary."""

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
                if not request.files:
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

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    _run_ansible_validate,  # type: ignore[arg-type]
                    list(request.files),
                    raw_version,
                    hierarchy_payload,
                    req_id,
                    request.venv_path or "",
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
                diag = ValidatorDiagnostics(
                    validator_name="ansible",
                    request_id=req_id,
                    total_ms=total_ms,
                    files_received=len(request.files),
                    violations_found=len(result.run_result.violations),
                    rule_timings=rule_timings,
                    metadata={
                        "ansible_core_version": result.ansible_core_version,
                    },
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


async def serve(listen: str = "0.0.0.0:50053") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Ansible servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50053).

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
    validate_pb2_grpc.add_ValidatorServicer_to_server(AnsibleValidatorServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen:
        _, _, port = listen.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen)
    await server.start()
    return server
