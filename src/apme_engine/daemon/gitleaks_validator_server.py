"""Gitleaks validator daemon: async gRPC server for secret detection.

Writes files to a temp dir, runs gitleaks detect, and returns violations.
"""

import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import grpc
import grpc.aio

from apme.v1 import common_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.validators.gitleaks.scanner import GITLEAKS_BIN, run_gitleaks

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_GITLEAKS_MAX_RPCS", "16"))

_SCANNABLE_EXTENSIONS = (
    ".yml",
    ".yaml",
    ".cfg",
    ".ini",
    ".conf",
    ".env",
    ".py",
    ".sh",
    ".json",
)


def _run_scan(files: list[File]) -> tuple[list[dict[str, str | int | list[int] | None]], int]:
    """Blocking function: write files to temp dir, run gitleaks, return (violations, files_written).

    Args:
        files: List of File protos with path and content.

    Returns:
        Tuple of (violations list, count of files written).
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="apme_gitleaks_"))
    try:
        file_count = 0
        for f in files:
            if not f.path.endswith(_SCANNABLE_EXTENSIONS):
                continue
            out = temp_dir / f.path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(f.content)
            file_count += 1

        return run_gitleaks(temp_dir), file_count
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(temp_dir)


def _get_gitleaks_version() -> str:
    """Attempt to get gitleaks version string (best-effort).

    Returns:
        Version string from gitleaks --version, or "unknown" on failure.
    """
    import subprocess as _sp

    try:
        r = _sp.run([GITLEAKS_BIN, "version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


class GitleaksValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: runs gitleaks in executor thread."""

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: write files to temp dir, run gitleaks, return violations.

        Args:
            request: ValidateRequest with files to scan.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        try:
            if not request.files:
                return ValidateResponse(violations=[], request_id=req_id)

            sys.stderr.write(f"[req={req_id}] Gitleaks: scanning {len(request.files)} file(s)\n")
            sys.stderr.flush()

            violations, files_written = await asyncio.get_event_loop().run_in_executor(
                None,
                _run_scan,  # type: ignore[arg-type]
                list(request.files),
            )

            total_ms = (time.monotonic() - t0) * 1000
            sys.stderr.write(f"[req={req_id}] Gitleaks: {len(violations)} finding(s) in {total_ms:.1f}ms\n")
            sys.stderr.flush()

            diag = ValidatorDiagnostics(
                validator_name="gitleaks",
                request_id=req_id,
                total_ms=total_ms,
                files_received=len(request.files),
                violations_found=len(violations),
                rule_timings=[
                    RuleTiming(
                        rule_id="gitleaks_subprocess",
                        elapsed_ms=total_ms,
                        violations=len(violations),
                    ),
                ],
                metadata={
                    "subprocess_ms": f"{total_ms:.1f}",
                    "files_written": str(files_written),
                },
            )

            return ValidateResponse(
                violations=[violation_dict_to_proto(v) for v in violations],
                request_id=req_id,
                diagnostics=diag,
            )
        except Exception as e:
            import traceback

            sys.stderr.write(f"[req={req_id}] Gitleaks error: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            return ValidateResponse(violations=[], request_id=req_id)

    async def Health(
        self,
        request: common_pb2.HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Handle Health RPC: verify gitleaks binary is available.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with status including gitleaks version or error.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                GITLEAKS_BIN,
                "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                version = stdout.decode().strip()
                return HealthResponse(status=f"ok (gitleaks {version})")
            return HealthResponse(status=f"gitleaks exited {proc.returncode}")
        except FileNotFoundError:
            return HealthResponse(status="gitleaks binary not found")
        except Exception as e:
            return HealthResponse(status=f"gitleaks health error: {e}")


async def serve(listen: str = "0.0.0.0:50056") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Gitleaks servicer.

    Args:
        listen: Host:port to bind (e.g. 0.0.0.0:50056).

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
    validate_pb2_grpc.add_ValidatorServicer_to_server(GitleaksValidatorServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen:
        _, _, port = listen.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen)
    await server.start()
    return server
