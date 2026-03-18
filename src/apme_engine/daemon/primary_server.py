"""Primary daemon: async gRPC server that runs engine then fans out to all validators."""

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import grpc.aio
import jsonpickle

from apme.v1 import cache_pb2, cache_pb2_grpc, common_pb2, primary_pb2, primary_pb2_grpc, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthResponse, ValidatorDiagnostics
from apme.v1.primary_pb2 import (  # type: ignore[attr-defined]
    FileDiff,
    FormatRequest,
    FormatResponse,
    ScanChunk,
    ScanDiagnostics,
    ScanOptions,
    ScanRequest,
    ScanResponse,
)
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon.violation_convert import violation_proto_to_dict
from apme_engine.engine.jsonpickle_handlers import register_engine_handlers
from apme_engine.engine.models import AnsibleRunContext, ViolationDict
from apme_engine.runner import run_scan

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_PRIMARY_MAX_RPCS", "16"))


@dataclass
class _ValidatorResult:
    """Result from a single validator RPC call.

    Attributes:
        violations: List of violation dicts from the validator.
        diagnostics: Optional ValidatorDiagnostics from the response.
    """

    violations: list[ViolationDict] = field(default_factory=list)
    diagnostics: ValidatorDiagnostics | None = None


def _sort_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Sort violations by file then line for stable ordering.

    Args:
        violations: List of violation dicts.

    Returns:
        Sorted list of violations.
    """

    def key(v: ViolationDict) -> tuple[str, int | float]:
        f = str(v.get("file") or "")
        line = v.get("line")
        if isinstance(line, (list, tuple)) and line:
            line = line[0]
        if not isinstance(line, (int, float)):
            line = 0
        return (f, line if isinstance(line, (int, float)) else 0)

    return sorted(violations, key=key)


def _deduplicate_violations(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Remove duplicate violations sharing the same (rule_id, file, line).

    Args:
        violations: List of violation dicts (may contain duplicates).

    Returns:
        Deduplicated list preserving first occurrence order.
    """
    seen: set[tuple[str, str, str | int | list[int] | tuple[int, ...] | bool | None]] = set()
    out: list[ViolationDict] = []
    for v in violations:
        line: str | int | list[int] | tuple[int, ...] | bool | None = v.get("line")
        if isinstance(line, (list, tuple)):
            line = tuple(line)
        dedup_key = (str(v.get("rule_id", "")), str(v.get("file", "")), line)
        if dedup_key not in seen:
            seen.add(dedup_key)
            out.append(v)
    return out


def _normalize_scandata_contexts(scandata: object) -> None:
    """Ensure scandata.contexts is a list of AnsibleRunContext (mutates in place).

    Materializes iterators and drops non-AnsibleRunContext items so jsonpickle
    never encodes iterators, which decode as list_iterator on the native side.

    Args:
        scandata: The scan data object whose contexts attribute will be normalized.
    """
    if not scandata or not hasattr(scandata, "contexts"):
        return
    raw = getattr(scandata, "contexts", None)
    if raw is None:
        return
    materialized = list(raw) if not isinstance(raw, list) else raw
    valid = [c for c in materialized if isinstance(c, AnsibleRunContext)]
    if len(valid) != len(materialized):
        sys.stderr.write(
            f"Primary: normalized scandata.contexts {len(materialized)} -> {len(valid)} "
            f"(dropped non-AnsibleRunContext)\n"
        )
        sys.stderr.flush()
    scandata.contexts = valid


def _write_chunked_fs(project_root: str, files: list[File]) -> Path:
    """Write request.files into a temp directory; return path to that directory.

    Args:
        project_root: Name for project root (used in path structure).
        files: List of File protos with path and content.

    Returns:
        Path to the created temp directory.
    """
    tmp = Path(tempfile.mkdtemp(prefix="apme_primary_"))
    for f in files:
        path = tmp / f.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.content)
    return tmp


async def _call_validator(
    address: str,
    request: ValidateRequest,
    timeout: int = 60,
) -> _ValidatorResult:
    """Call a validator over async gRPC; return violations + diagnostics.

    Args:
        address: gRPC address of the validator (e.g. localhost:50055).
        request: ValidateRequest to send.
        timeout: Request timeout in seconds.

    Returns:
        _ValidatorResult with violations and optional diagnostics.
    """
    req_id = request.request_id or ""
    channel = grpc.aio.insecure_channel(address)
    stub = validate_pb2_grpc.ValidatorStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = await stub.Validate(request, timeout=timeout)
        return _ValidatorResult(
            violations=[violation_proto_to_dict(v) for v in resp.violations],
            diagnostics=resp.diagnostics if resp.HasField("diagnostics") else None,
        )
    except grpc.RpcError as e:
        sys.stderr.write(f"[req={req_id}] Validator at {address} failed: {e}\n")
        sys.stderr.flush()
        return _ValidatorResult()
    finally:
        await channel.close(grace=None)


_REQUIREMENTS_PATHS = {"requirements.yml", "collections/requirements.yml"}


def _discover_collection_specs(files: list[File]) -> list[str]:
    """Extract collection specs from requirements.yml files in the uploaded file set.

    Looks for ``requirements.yml`` and ``collections/requirements.yml``.
    Parses the ``collections`` key and returns ``name[:version]`` strings.

    Args:
        files: Uploaded File protos from the ScanRequest.

    Returns:
        Deduplicated list of collection specifiers found in requirements files.
    """
    import yaml

    specs: dict[str, str] = {}
    for f in files:
        norm = f.path.replace("\\", "/").lstrip("/")
        if norm not in _REQUIREMENTS_PATHS:
            continue
        try:
            data = yaml.safe_load(f.content.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        collections = data.get("collections")
        if not isinstance(collections, list):
            continue
        for entry in collections:
            if isinstance(entry, str):
                specs.setdefault(entry, entry)
            elif isinstance(entry, dict) and entry.get("name"):
                name = str(entry["name"])
                version = entry.get("version")
                spec = (
                    f"{name}:{version}"
                    if version and not str(version).startswith((">=", ">", "<", "!=", "*"))
                    else name
                )
                specs.setdefault(name, spec)
    return list(specs.values())


async def _ensure_collections_cached(collection_specs: list[str], scan_id: str) -> None:
    """Pull any missing collections into the cache via the CacheMaintainer.

    Calls PullGalaxy for each spec (idempotent — no-op if already cached).
    Failures are logged but never abort the scan.

    Args:
        collection_specs: Collection specifiers (e.g. community.general:9.0.0).
        scan_id: Request ID for log correlation.
    """
    cache_addr = os.environ.get("APME_CACHE_GRPC_ADDRESS")
    if not cache_addr or not collection_specs:
        return
    channel = grpc.aio.insecure_channel(cache_addr)
    try:
        stub = cache_pb2_grpc.CacheMaintainerStub(channel)  # type: ignore[no-untyped-call]
        for spec in collection_specs:
            try:
                resp = await stub.PullGalaxy(
                    cache_pb2.PullGalaxyRequest(spec=spec),
                    timeout=120,
                )
                if resp.success:
                    sys.stderr.write(f"[req={scan_id}] Cache: {spec} ready\n")
                else:
                    sys.stderr.write(f"[req={scan_id}] Cache: {spec} failed: {resp.error_message}\n")
            except grpc.RpcError as e:
                sys.stderr.write(f"[req={scan_id}] Cache: {spec} RPC error: {e}\n")
            sys.stderr.flush()
    finally:
        await channel.close(grace=None)


VALIDATOR_ENV_VARS = {
    "native": "NATIVE_GRPC_ADDRESS",
    "opa": "OPA_GRPC_ADDRESS",
    "ansible": "ANSIBLE_GRPC_ADDRESS",
    "gitleaks": "GITLEAKS_GRPC_ADDRESS",
}


class PrimaryServicer(primary_pb2_grpc.PrimaryServicer):
    """Primary gRPC servicer: runs engine, fans out to validators, aggregates results."""

    async def Scan(self, request: ScanRequest, context: grpc.aio.ServicerContext) -> primary_pb2.ScanResponse:  # type: ignore[type-arg]
        """Handle Scan RPC: run engine, fan out to validators, aggregate violations.

        Args:
            request: ScanRequest with files and options.
            context: gRPC servicer context.

        Returns:
            ScanResponse with violations and diagnostics.

        Raises:
            Exception: If scan fails (re-raised from inner exception).
        """
        scan_id = request.scan_id or str(uuid.uuid4())
        violations: list[ViolationDict] = []
        temp_dir = None
        scan_t0 = time.monotonic()

        try:
            sys.stderr.write(f"[req={scan_id}] Scan: received {len(request.files)} file(s)\n")
            sys.stderr.flush()

            if not request.files:
                return ScanResponse(scan_id=scan_id, violations=[])

            temp_dir = await asyncio.get_event_loop().run_in_executor(
                None,
                _write_chunked_fs,  # type: ignore[arg-type]
                request.project_root or "project",
                list(request.files),
            )
            target = str(temp_dir)
            project_root = target

            engine_t0 = time.monotonic()
            context_obj = await asyncio.get_event_loop().run_in_executor(
                None,
                run_scan,
                target,
                project_root,
                True,
            )
            (time.monotonic() - engine_t0) * 1000

            if not context_obj.hierarchy_payload:
                sys.stderr.write(f"[req={scan_id}] Scan: no hierarchy payload produced\n")
                sys.stderr.flush()
                return ScanResponse(scan_id=scan_id, violations=[])

            opts = request.options if request.HasField("options") else None
            collection_specs = list(opts.collection_specs) if opts else []

            discovered = _discover_collection_specs(
                list(request.files),  # type: ignore[arg-type]
            )
            if discovered:
                existing = {s.split(":")[0] for s in collection_specs}
                for spec in discovered:
                    if spec.split(":")[0] not in existing:
                        collection_specs.append(spec)
                sys.stderr.write(
                    f"[req={scan_id}] Collections: {len(discovered)} discovered from requirements.yml, "
                    f"{len(collection_specs)} total\n"
                )
                sys.stderr.flush()

            # Ensure scandata.contexts is a concrete list of AnsibleRunContext so
            # jsonpickle never encodes iterators (which decode as list_iterator on native).
            _normalize_scandata_contexts(context_obj.scandata)
            register_engine_handlers()

            validate_request = ValidateRequest(
                request_id=scan_id,
                project_root=request.project_root or "",
                files=list(request.files),
                hierarchy_payload=json.dumps(context_obj.hierarchy_payload).encode(),
                scandata=jsonpickle.encode(context_obj.scandata).encode(),
                ansible_core_version=opts.ansible_core_version if opts else "",
                collection_specs=collection_specs,
            )

            if collection_specs:
                await _ensure_collections_cached(collection_specs, scan_id)

            tasks = {}
            for name, env_var in VALIDATOR_ENV_VARS.items():
                addr = os.environ.get(env_var)
                if not addr:
                    continue
                tasks[name] = _call_validator(addr, validate_request)

            validator_diagnostics: list[ValidatorDiagnostics] = []

            fan_out_ms = 0.0
            if tasks:
                fan_t0 = time.monotonic()
                results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                fan_out_ms = (time.monotonic() - fan_t0) * 1000

                counts: dict[str, int] = {}
                for name, result in zip(tasks.keys(), results, strict=False):
                    if isinstance(result, BaseException):
                        sys.stderr.write(f"[req={scan_id}] {name} raised: {result}\n")
                        sys.stderr.flush()
                        counts[name] = 0
                    else:
                        res = result
                        counts[name] = len(res.violations)
                        violations.extend(res.violations)
                        if res.diagnostics:
                            validator_diagnostics.append(res.diagnostics)

                parts = " ".join(f"{n.title()}={counts.get(n, 0)}" for n in VALIDATOR_ENV_VARS)
                sys.stderr.write(f"[req={scan_id}] Scan: {parts} Total={len(violations)}\n")
                sys.stderr.flush()

            violations = _deduplicate_violations(_sort_violations(violations))
            from apme_engine.daemon.violation_convert import violation_dict_to_proto

            proto_violations = [violation_dict_to_proto(v) for v in violations]

            total_ms = (time.monotonic() - scan_t0) * 1000
            ediag = context_obj.engine_diagnostics
            scan_diag = ScanDiagnostics(
                engine_parse_ms=ediag.parse_ms,
                engine_annotate_ms=ediag.annotate_ms,
                engine_total_ms=ediag.total_ms,
                files_scanned=ediag.files_scanned,
                trees_built=ediag.trees_built,
                total_violations=len(violations),
                validators=validator_diagnostics,
                fan_out_ms=fan_out_ms,
                total_ms=total_ms,
            )

            return ScanResponse(
                violations=proto_violations,
                scan_id=scan_id,
                diagnostics=scan_diag,
            )
        except Exception as e:
            import traceback

            sys.stderr.write(f"[req={scan_id}] Scan failed: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise
        finally:
            if temp_dir is not None and temp_dir.is_dir():
                with contextlib.suppress(OSError):
                    shutil.rmtree(temp_dir)

    async def ScanStream(
        self,
        request_stream: AsyncIterator[ScanChunk],
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> primary_pb2.ScanResponse:
        """Handle ScanStream RPC: receive file batches, then run same logic as Scan.

        Avoids gRPC default max message size (4 MiB) by sending files in multiple messages.

        Args:
            request_stream: Async iterator of ScanChunk messages.
            context: gRPC servicer context.

        Returns:
            ScanResponse with violations and diagnostics.
        """
        all_files: list[File] = []
        scan_id = ""
        project_root = "project"
        opts: ScanOptions | None = None
        async for chunk in request_stream:
            if chunk.scan_id:
                scan_id = chunk.scan_id
            if chunk.project_root:
                project_root = chunk.project_root
            if chunk.HasField("options"):
                opts = chunk.options
            all_files.extend(chunk.files)
            if chunk.last:
                break
        req = ScanRequest(
            scan_id=scan_id or str(uuid.uuid4()),
            project_root=project_root,
            files=all_files,
            options=opts or ScanOptions(),
        )
        return await self.Scan(req, context)

    async def Format(self, request: FormatRequest, context: grpc.aio.ServicerContext) -> FormatResponse:  # type: ignore[type-arg]
        """Handle Format RPC: format YAML files and return diffs for changed ones.

        Args:
            request: FormatRequest with files to format.
            context: gRPC servicer context.

        Returns:
            FormatResponse with diffs for files that changed.
        """
        from apme_engine.formatter import format_content

        sys.stderr.write(f"Format: received {len(request.files)} file(s)\n")
        sys.stderr.flush()

        def _do_format(files: list[File]) -> list[FileDiff]:
            """Format YAML files and return diffs for changed ones.

            Args:
                files: List of File protos to format.

            Returns:
                List of FileDiff for files that changed.
            """
            diffs = []
            for f in files:
                if not f.path.endswith((".yml", ".yaml")):
                    continue
                try:
                    text = f.content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                result = format_content(text, filename=f.path)
                if result.changed:
                    diffs.append(
                        FileDiff(
                            path=f.path,
                            original=f.content,
                            formatted=result.formatted.encode("utf-8"),
                            diff=result.diff,
                        )
                    )
            return diffs

        diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            _do_format,  # type: ignore[arg-type]
            list(request.files),
        )

        sys.stderr.write(f"Format: {len(diffs)} file(s) changed\n")
        sys.stderr.flush()
        return FormatResponse(diffs=diffs)

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


async def serve(listen_address: str = "0.0.0.0:50051") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Primary servicer.

    Args:
        listen_address: Host:port to bind (e.g. 0.0.0.0:50051).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    server = grpc.aio.server(maximum_concurrent_rpcs=_MAX_CONCURRENT_RPCS)
    primary_pb2_grpc.add_PrimaryServicer_to_server(PrimaryServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen_address:
        _, _, port = listen_address.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen_address)
    await server.start()
    return server
