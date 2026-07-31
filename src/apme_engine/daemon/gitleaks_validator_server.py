"""Gitleaks validator daemon: async gRPC server for secret detection.

Uses ``run_gitleaks_nodes()`` for all scanning — content is piped to
gitleaks via stdin with delimiter-based node attribution.  No temp
files are written.
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

from apme.v1 import common_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthResponse, RuleTiming, ValidatorDiagnostics
from apme.v1.validate_pb2 import ValidateRequest, ValidateResponse
from apme_engine.daemon.validator_errors import infra_error_response
from apme_engine.daemon.violation_convert import violation_dict_to_proto
from apme_engine.engine.models import ViolationDict
from apme_engine.log_bridge import attach_collector
from apme_engine.validators.gitleaks.scanner import GITLEAKS_BIN, run_gitleaks_nodes

logger = logging.getLogger("apme.gitleaks")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_GITLEAKS_MAX_RPCS", "16"))


def _extract_nodes_from_graph_data(raw: bytes) -> tuple[list[tuple[str, str]], set[str]]:
    """Parse serialized ContentGraph JSON and extract ``(node_id, yaml_lines)`` tuples.

    Args:
        raw: JSON-encoded ContentGraph (from ``ContentGraph.to_dict(slim=True)``).

    Returns:
        Tuple of (scan nodes, covered file paths).  Scan nodes are
        ``(node_id, yaml_lines)`` for nodes with non-empty content.
        Covered file paths are the set of ``file_path`` values for those
        nodes, used to avoid re-scanning the same content via raw files.
    """
    try:
        data = cast(dict[str, object], json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], set()

    result: list[tuple[str, str]] = []
    covered_paths: set[str] = set()
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list):
        return result, covered_paths

    for entry in raw_nodes:
        if not isinstance(entry, dict):
            continue
        node_id = str(entry.get("id", ""))
        node_data = entry.get("data")
        if not isinstance(node_data, dict) or not node_id:
            continue
        yaml_lines = str(node_data.get("yaml_lines", ""))
        if yaml_lines:
            result.append((node_id, yaml_lines))
            file_path = node_data.get("file_path")
            if isinstance(file_path, str) and file_path:
                covered_paths.add(file_path)

    return result, covered_paths


def _run_scan(
    content_graph_data: bytes,
    files: list[File],
) -> tuple[list[ViolationDict], int]:
    """Build node tuples from graph data + uncovered files, scan via stdin.

    Graph nodes with ``yaml_lines`` get node-native attribution.  Files
    whose paths are **not** covered by any graph node are included as
    file-keyed entries so secrets outside the task-level graph (vars
    files, play headers, etc.) are still scanned.

    Args:
        content_graph_data: Serialized ContentGraph JSON (may be empty).
        files: File protos from the original scan request.

    Returns:
        Tuple of ``(violations, node_count)``.
    """
    nodes: list[tuple[str, str]] = []
    covered_paths: set[str] = set()

    if content_graph_data:
        graph_nodes, covered_paths = _extract_nodes_from_graph_data(content_graph_data)
        nodes.extend(graph_nodes)

    for f in files:
        if f.path in covered_paths:
            continue
        try:
            content = f.content.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if content:
            nodes.append((f.path, content))

    if not nodes:
        return [], 0

    return run_gitleaks_nodes(nodes), len(nodes)


class GitleaksValidatorServicer(validate_pb2_grpc.ValidatorServicer):
    """Async gRPC adapter: runs gitleaks in executor thread."""

    async def Validate(
        self,
        request: ValidateRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ValidateResponse:
        """Handle Validate RPC: scan content for secrets via stdin piping.

        Args:
            request: ValidateRequest with files and/or content_graph_data.
            context: gRPC servicer context.

        Returns:
            ValidateResponse with violations and diagnostics.
        """
        req_id = request.request_id or ""
        t0 = time.monotonic()
        with attach_collector() as sink:
            try:
                if not request.files and not request.content_graph_data:
                    return ValidateResponse(violations=[], request_id=req_id, logs=sink.entries)

                logger.info(
                    "Gitleaks: validate start (%d files, %d graph bytes, req=%s)",
                    len(request.files),
                    len(request.content_graph_data),
                    req_id,
                )

                ctx = contextvars.copy_context()
                violations, node_count = await asyncio.get_event_loop().run_in_executor(
                    None,
                    ctx.run,  # type: ignore[arg-type]
                    _run_scan,
                    bytes(request.content_graph_data),
                    list(request.files),
                )

                total_ms = (time.monotonic() - t0) * 1000
                logger.info("Gitleaks: validate done (%.0fms, %d findings, req=%s)", total_ms, len(violations), req_id)

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
                        "nodes_scanned": str(node_count),
                    },
                )

                return ValidateResponse(
                    violations=[violation_dict_to_proto(v) for v in violations],
                    request_id=req_id,
                    diagnostics=diag,
                    logs=sink.entries,
                )
            except Exception as e:
                logger.exception("Gitleaks: unhandled error (req=%s): %s", req_id, e)
                return infra_error_response(req_id, sink.entries)

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
    from apme_engine.daemon.validator_grpc import start_validator_server

    return await start_validator_server(
        GitleaksValidatorServicer(),
        listen,
        service="gitleaks",
        max_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
    )
