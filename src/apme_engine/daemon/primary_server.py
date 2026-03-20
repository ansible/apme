"""Primary daemon: async gRPC server that runs engine then fans out to all validators.

The Primary is the sole API surface for all clients (CLI, web UI, CI).
Clients send file bytes via gRPC streams and receive processed bytes back.
The Primary delegates internally to validators, cache, and remediation.
"""

import asyncio
import contextlib
import difflib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import grpc.aio
import jsonpickle

from apme.v1 import cache_pb2, cache_pb2_grpc, primary_pb2_grpc, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthRequest, HealthResponse, ScanSummary, ServiceHealth, ValidatorDiagnostics
from apme.v1.primary_pb2 import (  # type: ignore[attr-defined]
    ApprovalAck,
    FileDiff,
    FilePatch,
    FixOptions,
    FixReport,
    FormatRequest,
    FormatResponse,
    ProgressUpdate,
    Proposal,
    ProposalsReady,
    ScanChunk,
    ScanCompletedEvent,
    ScanDiagnostics,
    ScanOptions,
    ScanRequest,
    ScanResponse,
    SessionClosed,
    SessionCommand,
    SessionCreated,
    SessionEvent,
    SessionResult,
    SubscribeRequest,
    Tier1Summary,
)
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon.session import ResourceExhaustedError, SessionState, SessionStore
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.engine.jsonpickle_handlers import register_engine_handlers
from apme_engine.engine.models import AnsibleRunContext, ViolationDict
from apme_engine.runner import run_scan

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_PRIMARY_MAX_RPCS", "16"))
_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — hierarchy+scandata can exceed the 4 MiB default


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
        if isinstance(line, list | tuple) and line:
            line = line[0]
        if not isinstance(line, int | float):
            line = 0
        return (f, line if isinstance(line, int | float) else 0)

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
        if isinstance(line, list | tuple):
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
    channel = grpc.aio.insecure_channel(
        address,
        options=[
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
        ],
    )
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


def merge_collection_specs(
    request_specs: list[str],
    discovered_specs: list[str],
    hierarchy_collections: Sequence[object],
) -> list[str]:
    """Merge collection specs with precedence: request > requirements.yml > FQCN-derived.

    Each source is deduplicated by bare ``namespace.collection`` name so
    versioned specs from earlier sources take priority over bare names
    discovered later.

    Args:
        request_specs: Specs from the gRPC request (highest precedence).
        discovered_specs: Specs from requirements.yml (may include versions).
        hierarchy_collections: Bare namespace.collection strings from FQCN auto-discovery.

    Returns:
        Merged list with duplicates removed by precedence.
    """
    result = list(request_specs)
    existing = {s.split(":")[0] for s in result}

    for spec in discovered_specs:
        bare = spec.split(":")[0]
        if bare not in existing:
            result.append(spec)
            existing.add(bare)

    for coll in hierarchy_collections:
        if isinstance(coll, str) and coll not in existing:
            result.append(coll)
            existing.add(coll)

    return result


async def _ensure_collections_cached(collection_specs: list[str], scan_id: str) -> None:
    """Pull any missing collections into the cache via the CacheMaintainer.

    Calls PullGalaxy for each spec (idempotent — no-op if already cached).
    Failures are logged but never abort the scan.

    When ``APME_GALAXY_PROXY_URL`` is set, collection management is delegated
    to the proxy (on-demand via ``uv pip install``), so this step is skipped.

    Args:
        collection_specs: Collection specifiers (e.g. community.general:9.0.0).
        scan_id: Request ID for log correlation.
    """
    if os.environ.get("APME_GALAXY_PROXY_URL", "").strip():
        sys.stderr.write(f"[req={scan_id}] Cache: skipping pre-pull (galaxy proxy handles on demand)\n")
        sys.stderr.flush()
        return

    cache_addr = os.environ.get("APME_CACHE_GRPC_ADDRESS")
    if not cache_addr or not collection_specs:
        return
    channel = grpc.aio.insecure_channel(
        cache_addr,
        options=[("grpc.max_receive_message_length", _GRPC_MAX_MSG)],
    )
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
    """Primary gRPC servicer — sole API surface for all clients.

    Runs engine, fans out to validators, orchestrates format + remediation.
    Clients send file bytes in, receive processed bytes out.
    """

    # ── internal: reusable scan pipeline ──────────────────────────────

    async def _scan_pipeline(
        self,
        temp_dir: Path,
        files: list[File],
        scan_id: str,
        *,
        ansible_core_version: str = "",
        collection_specs: list[str] | None = None,
        include_scandata: bool = True,
    ) -> tuple[list[ViolationDict], ScanDiagnostics | None]:
        """Core scan pipeline: engine + validator fan-out.

        Reused by Scan, ScanStream, and FixSession (as scan_fn for remediation).

        Args:
            temp_dir: Directory containing the materialized files.
            files: Original File protos (for ValidateRequest).
            scan_id: Request ID for correlation.
            ansible_core_version: Ansible core version constraint.
            collection_specs: Collection specifiers (may be extended by discovery).
            include_scandata: Whether to include scandata in engine call.

        Returns:
            Tuple of (violations as dicts, ScanDiagnostics or None).
        """
        scan_t0 = time.monotonic()
        collection_specs = list(collection_specs or [])

        context_obj = await asyncio.get_event_loop().run_in_executor(
            None,
            run_scan,
            str(temp_dir),
            str(temp_dir),
            include_scandata,
        )

        if not context_obj.hierarchy_payload:
            sys.stderr.write(f"[req={scan_id}] Scan: no hierarchy payload produced\n")
            sys.stderr.flush()
            return [], None

        discovered = _discover_collection_specs(files)
        hierarchy_collections = context_obj.hierarchy_payload.get("collection_set", [])
        if not isinstance(hierarchy_collections, list):
            hierarchy_collections = []

        collection_specs = merge_collection_specs(
            collection_specs,
            discovered,
            hierarchy_collections,
        )

        _normalize_scandata_contexts(context_obj.scandata)
        register_engine_handlers()

        validate_request = ValidateRequest(
            request_id=scan_id,
            project_root="",
            files=files,
            hierarchy_payload=json.dumps(context_obj.hierarchy_payload, default=str).encode(),
            scandata=jsonpickle.encode(context_obj.scandata).encode(),
            ansible_core_version=ansible_core_version,
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

        violations: list[ViolationDict] = []
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
                    counts[name] = len(result.violations)
                    violations.extend(result.violations)
                    if result.diagnostics:
                        validator_diagnostics.append(result.diagnostics)

            parts = " ".join(f"{n.title()}={counts.get(n, 0)}" for n in VALIDATOR_ENV_VARS)
            sys.stderr.write(f"[req={scan_id}] Scan: {parts} Total={len(violations)}\n")
            sys.stderr.flush()

        violations = _deduplicate_violations(_sort_violations(violations))

        total_ms = (time.monotonic() - scan_t0) * 1000
        ediag = context_obj.engine_diagnostics
        diag = ScanDiagnostics(
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
        return violations, diag

    @staticmethod
    def _format_files(files: list[File]) -> list[FileDiff]:
        """Format YAML files and return diffs for changed ones (sync, CPU-bound).

        Args:
            files: File protos to format.

        Returns:
            List of FileDiff for files whose content changed.
        """
        from apme_engine.formatter import format_content

        diffs: list[FileDiff] = []
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

    @staticmethod
    async def _accumulate_chunks(
        request_stream: AsyncIterator[ScanChunk],
    ) -> tuple[list[File], str, str, ScanOptions | None, FixOptions | None]:
        """Drain a ScanChunk stream into accumulated state.

        Args:
            request_stream: Async iterator of ScanChunk messages.

        Returns:
            Tuple of (files, scan_id, project_root, scan_options, fix_options).
        """
        all_files: list[File] = []
        scan_id = ""
        project_root = "project"
        opts: ScanOptions | None = None
        fix_opts: FixOptions | None = None
        async for chunk in request_stream:
            if chunk.scan_id:
                scan_id = chunk.scan_id
            if chunk.project_root:
                project_root = chunk.project_root
            if chunk.HasField("options"):
                opts = chunk.options
            if chunk.HasField("fix_options"):
                fix_opts = chunk.fix_options
            all_files.extend(chunk.files)  # type: ignore[arg-type]
            if chunk.last:
                break
        return all_files, scan_id or str(uuid.uuid4()), project_root, opts, fix_opts

    # ── Scan RPCs ─────────────────────────────────────────────────────

    async def Scan(self, request: ScanRequest, context: grpc.aio.ServicerContext) -> ScanResponse:  # type: ignore[type-arg]
        """Handle unary Scan RPC: validate files and return violations.

        Args:
            request: Scan request containing files and options.
            context: gRPC servicer context.

        Returns:
            ScanResponse with violations and diagnostics.

        Raises:
            Exception: Propagates unexpected errors after cleanup.
        """
        scan_id = request.scan_id or str(uuid.uuid4())
        temp_dir: Path | None = None

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
            assert temp_dir is not None

            opts = request.options if request.HasField("options") else None
            violations, diag = await self._scan_pipeline(
                temp_dir,
                list(request.files),  # type: ignore[arg-type]
                scan_id,
                ansible_core_version=opts.ansible_core_version if opts else "",
                collection_specs=list(opts.collection_specs) if opts else [],
            )

            from apme_engine.remediation.partition import add_classification_to_violations
            from apme_engine.remediation.transforms import build_default_registry

            registry = build_default_registry()
            add_classification_to_violations(violations, registry)

            from apme_engine.remediation.partition import count_by_remediation_class, count_by_resolution

            rem_counts = count_by_remediation_class(violations)
            res_counts = count_by_resolution(violations)
            summary = ScanSummary(
                total=len(violations),
                auto_fixable=rem_counts.get("auto-fixable", 0),
                ai_candidate=rem_counts.get("ai-candidate", 0),
                manual_review=rem_counts.get("manual-review", 0),
                by_resolution=res_counts,
            )

            violation_protos = [violation_dict_to_proto(v) for v in violations]

            response = ScanResponse(
                violations=violation_protos,
                scan_id=scan_id,
                diagnostics=diag,
                summary=summary,
            )

            return response
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
    ) -> ScanResponse:
        """Handle streaming Scan RPC: accumulate chunked files then validate.

        Args:
            request_stream: Async iterator of ScanChunk messages.
            context: gRPC servicer context.

        Returns:
            ScanResponse with violations and diagnostics.
        """
        all_files, scan_id, project_root, opts, _ = await self._accumulate_chunks(request_stream)
        req = ScanRequest(
            scan_id=scan_id,
            project_root=project_root,
            files=all_files,
            options=opts or ScanOptions(),
        )
        response = await self.Scan(req, context)

        from apme_engine.daemon.scan_events import publish

        publish(
            ScanCompletedEvent(
                scan_id=response.scan_id,  # type: ignore[attr-defined]
                project_path=project_root,
                violations=list(response.violations),  # type: ignore[attr-defined]
                diagnostics=response.diagnostics if response.HasField("diagnostics") else None,  # type: ignore[attr-defined]
                summary=response.summary if response.HasField("summary") else None,
                source="scan",
            )
        )

        return response

    # ── Format RPCs ───────────────────────────────────────────────────

    async def Format(self, request: FormatRequest, context: grpc.aio.ServicerContext) -> FormatResponse:  # type: ignore[type-arg]
        """Handle unary Format RPC: return diffs for files needing reformatting.

        Args:
            request: Format request containing files.
            context: gRPC servicer context.

        Returns:
            FormatResponse with file diffs.
        """
        sys.stderr.write(f"Format: received {len(request.files)} file(s)\n")
        sys.stderr.flush()

        diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            self._format_files,  # type: ignore[arg-type]
            list(request.files),
        )
        sys.stderr.write(f"Format: {len(diffs)} file(s) changed\n")
        sys.stderr.flush()
        return FormatResponse(diffs=diffs)

    async def FormatStream(
        self,
        request_stream: AsyncIterator[ScanChunk],
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> FormatResponse:
        """Handle streaming Format RPC: accumulate chunked files then reformat.

        Args:
            request_stream: Async iterator of ScanChunk messages.
            context: gRPC servicer context.

        Returns:
            FormatResponse with file diffs.
        """
        all_files, scan_id, *_ = await self._accumulate_chunks(request_stream)
        sys.stderr.write(f"[req={scan_id}] FormatStream: received {len(all_files)} file(s)\n")
        sys.stderr.flush()

        diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            self._format_files,
            all_files,
        )
        sys.stderr.write(f"[req={scan_id}] FormatStream: {len(diffs)} file(s) changed\n")
        sys.stderr.flush()
        return FormatResponse(diffs=diffs)

    # ── FixSession RPC (bidirectional stream, ADR-028) ─────────────────

    _session_store: SessionStore | None = None

    def _get_session_store(self) -> SessionStore:
        if self._session_store is None:
            self._session_store = SessionStore()
            self._session_store.start_reaper()
        return self._session_store

    async def FixSession(
        self,
        request_stream: AsyncIterator[SessionCommand],
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> AsyncIterator[SessionEvent]:
        """Bidirectional stream: upload -> process -> approve -> result.

        Args:
            request_stream: Async iterator of SessionCommand messages.
            context: gRPC servicer context.

        Yields:
            SessionEvent: Events streamed to the client.

        Raises:
            Exception: Propagates unexpected errors after logging.
        """
        store = self._get_session_store()
        session: SessionState | None = None
        scan_id = ""

        try:
            async for cmd in request_stream:
                oneof = cmd.WhichOneof("command")

                if oneof == "upload":
                    chunk: ScanChunk = cmd.upload
                    if session is None:
                        # First upload chunk — start accumulating
                        session, scan_id = await self._session_upload_start(
                            store,
                            chunk,
                        )
                        yield SessionEvent(
                            created=SessionCreated(
                                session_id=session.session_id,
                                ttl_seconds=session.ttl_seconds,
                            ),
                        )

                    self._session_upload_append(session, chunk)

                    if chunk.last:
                        async for event in self._session_process(session, scan_id):
                            yield event

                elif oneof == "approve":
                    if session is None:
                        continue
                    session.touch()
                    approved = set(cmd.approve.approved_ids)
                    applied = self._session_apply_approved(session, approved)
                    yield SessionEvent(
                        approval_ack=ApprovalAck(
                            applied_count=applied,
                            status=session.status,
                            ttl_seconds=session.ttl_seconds,
                        ),
                    )
                    if session.status == 3:  # COMPLETE
                        async for event in self._session_build_result(session):
                            yield event

                elif oneof == "extend":
                    if session:
                        session.touch()
                        yield SessionEvent(
                            created=SessionCreated(
                                session_id=session.session_id,
                                ttl_seconds=session.ttl_seconds,
                            ),
                        )

                elif oneof == "resume":
                    sid = cmd.resume.session_id
                    session = store.get(sid)
                    if session is None:
                        await context.abort(
                            grpc.StatusCode.NOT_FOUND,
                            f"Session {sid} not found or expired",
                        )
                        return
                    session.touch()
                    scan_id = session.session_id
                    yield SessionEvent(
                        created=SessionCreated(
                            session_id=session.session_id,
                            ttl_seconds=session.ttl_seconds,
                        ),
                    )
                    async for event in self._session_replay_state(session):
                        yield event

                # TODO: Emit ExpirationWarning when session.expiring_soon
                # becomes True.  Requires a background asyncio task per
                # session or periodic checks between commands.

                elif oneof == "close":
                    if session:
                        store.remove(session.session_id)
                    yield SessionEvent(closed=SessionClosed())
                    return

        except ResourceExhaustedError as e:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(e))
        except Exception as e:
            import traceback

            sys.stderr.write(f"[session={scan_id}] FixSession failed: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise

    # ── FixSession helpers ─────────────────────────────────────────────

    async def _session_upload_start(
        self,
        store: SessionStore,
        first_chunk: ScanChunk,
    ) -> tuple[SessionState, str]:
        session = store.create()
        scan_id = first_chunk.scan_id or session.session_id
        if first_chunk.HasField("fix_options"):
            session.fix_options = first_chunk.fix_options
        if first_chunk.HasField("options"):
            session.scan_options = first_chunk.options
        return session, scan_id

    @staticmethod
    def _session_upload_append(session: SessionState, chunk: ScanChunk) -> None:
        for f in chunk.files:
            session.original_files[f.path] = f.content  # type: ignore[attr-defined]
            session.working_files[f.path] = f.content  # type: ignore[attr-defined]

    async def _session_process(
        self,
        session: SessionState,
        scan_id: str,
    ) -> AsyncIterator[SessionEvent]:
        """Run format -> Tier 1 -> (optionally Tier 2) on the session.

        Args:
            session: Active session with uploaded files.
            scan_id: Scan identifier for log correlation.

        Yields:
            SessionEvent: Progress, tier1 summary, proposals, and/or result events.
        """
        from apme_engine.formatter import format_content
        from apme_engine.remediation.engine import RemediationEngine
        from apme_engine.remediation.transforms import build_default_registry

        all_files = [File(path=p, content=c) for p, c in session.working_files.items()]

        fix_opts = session.fix_options
        scan_opts = session.scan_options

        ansible_core_version = ""
        collection_specs: list[str] = []
        max_passes = 5
        if fix_opts:
            ansible_core_version = fix_opts.ansible_core_version
            collection_specs = list(fix_opts.collection_specs)
            if fix_opts.max_passes > 0:
                max_passes = fix_opts.max_passes
        elif scan_opts:
            ansible_core_version = scan_opts.ansible_core_version
            collection_specs = list(scan_opts.collection_specs)

        sys.stderr.write(
            f"[session={scan_id}] FixSession: processing {len(all_files)} file(s)\n",
        )
        sys.stderr.flush()

        if not all_files:
            session.status = 3  # COMPLETE
            yield SessionEvent(
                tier1_complete=Tier1Summary(
                    idempotency_ok=True,
                    report=FixReport(),
                ),
            )
            return

        # Phase 1: Format
        yield SessionEvent(
            progress=ProgressUpdate(
                message=f"Formatting {len(all_files)} file(s)...",
                phase="format",
                level=2,  # INFO
            ),
        )
        format_diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            self._format_files,
            list(all_files),
        )
        session.format_diffs = list(format_diffs)

        formatted_files: list[File] = list(all_files)
        format_map: dict[str, bytes] = {d.path: d.formatted for d in format_diffs}

        temp_dir = await asyncio.get_event_loop().run_in_executor(
            None,
            _write_chunked_fs,
            "project",
            list(all_files),
        )
        session.temp_dir = temp_dir

        if format_map:
            formatted_files = []
            for f in all_files:
                if f.path in format_map:
                    new_content = format_map[f.path]
                    (temp_dir / f.path).write_bytes(new_content)
                    session.working_files[f.path] = new_content
                    formatted_files.append(File(path=f.path, content=new_content))
                else:
                    formatted_files.append(f)

        if format_diffs:
            yield SessionEvent(
                progress=ProgressUpdate(
                    message=f"Formatted {len(format_diffs)} file(s)",
                    phase="format",
                    level=2,
                ),
            )

        # Phase 2: Idempotency check
        idem_diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            self._format_files,
            formatted_files,
        )
        session.idempotency_ok = len(idem_diffs) == 0
        if not session.idempotency_ok:
            yield SessionEvent(
                progress=ProgressUpdate(
                    message="Formatter is not idempotent on this input",
                    phase="format",
                    level=3,  # WARNING
                ),
            )

        # Phase 3+4: Scan + Remediate via convergence loop
        yield SessionEvent(
            progress=ProgressUpdate(
                message="Running Tier 1 remediation...",
                phase="tier1",
                level=2,
            ),
        )

        loop = asyncio.get_event_loop()

        def scan_fn(file_paths: list[str]) -> list[ViolationDict]:
            rel_files = []
            for fp in file_paths:
                p = Path(fp)
                rel = str(p.relative_to(temp_dir)) if p.is_absolute() else fp
                rel_files.append(File(path=rel, content=p.read_bytes()))
            coro = self._scan_pipeline(
                temp_dir,
                rel_files,
                scan_id,
                ansible_core_version=ansible_core_version,
                collection_specs=collection_specs,
            )
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            violations, _ = future.result(timeout=300)
            return violations

        registry = build_default_registry()
        # TODO(ADR-025): Wire AIProvider into RemediationEngine when
        # fix_opts.enable_ai is True.  Currently Tier 2 AI proposals are
        # never generated because no ai_provider is supplied.
        engine = RemediationEngine(
            registry=registry,
            scan_fn=scan_fn,
            max_passes=max_passes,
            verbose=True,
        )

        yaml_paths = [str(temp_dir / f.path) for f in formatted_files if f.path.endswith((".yml", ".yaml"))]

        report = await asyncio.get_event_loop().run_in_executor(
            None,
            engine.remediate,
            yaml_paths,
        )

        # Post-remediation format pass
        for patch in report.applied_patches:
            result = format_content(patch.patched, filename=Path(patch.path).name)
            if result.changed:
                patch.patched = result.formatted

        for patch in report.applied_patches:
            patch.diff = "".join(
                difflib.unified_diff(
                    patch.original.splitlines(keepends=True),
                    patch.patched.splitlines(keepends=True),
                    fromfile=f"a/{Path(patch.path).name}",
                    tofile=f"b/{Path(patch.path).name}",
                )
            )

        # Build Tier 1 summary
        tier1_patches: list[FilePatch] = []
        for patch in report.applied_patches:
            rel_path = str(Path(patch.path).relative_to(temp_dir))
            orig = session.original_files.get(rel_path, patch.original.encode("utf-8"))
            proto_patch = FilePatch(
                path=rel_path,
                original=orig,
                patched=patch.patched.encode("utf-8"),
                diff=patch.diff,
                applied_rules=patch.rule_ids,
            )
            tier1_patches.append(proto_patch)
            session.working_files[rel_path] = patch.patched.encode("utf-8")

        session.tier1_patches = tier1_patches
        session.remaining_ai = list(report.remaining_ai)
        session.remaining_manual = list(report.remaining_manual)

        remaining_violations = [violation_dict_to_proto(v) for v in report.remaining_ai + report.remaining_manual]
        session.report = FixReport(
            passes=report.passes,
            fixed=report.fixed,
            remaining_ai=len(report.remaining_ai),
            remaining_manual=len(report.remaining_manual),
            oscillation_detected=report.oscillation_detected,
            remaining_violations=remaining_violations,
        )

        yield SessionEvent(
            progress=ProgressUpdate(
                message=(f"Tier 1 converged: {report.passes} pass(es), {report.fixed} fixed"),
                phase="tier1",
                level=2,
            ),
        )

        yield SessionEvent(
            tier1_complete=Tier1Summary(
                applied_patches=tier1_patches,
                format_diffs=list(format_diffs),
                idempotency_ok=session.idempotency_ok,
                report=session.report,
            ),
        )

        from apme_engine.daemon.scan_events import publish

        publish(
            ScanCompletedEvent(
                scan_id=scan_id,
                project_path="project",
                violations=remaining_violations,
                summary=ScanSummary(
                    total=len(remaining_violations),
                    auto_fixable=session.report.remaining_ai,
                    ai_candidate=session.report.remaining_ai,
                    manual_review=session.report.remaining_manual,
                ),
                source="fix",
            )
        )

        # Only present proposals if AI is enabled via FixOptions
        ai_enabled = fix_opts.enable_ai if fix_opts else False
        if report.remaining_ai and ai_enabled:
            session.current_tier = 2
            proposals = self._build_proposals_from_remaining(
                report.remaining_ai,
                tier=2,
            )
            session.proposals = {p.id: p for p in proposals}
            session.status = 1  # AWAITING_APPROVAL
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=proposals,
                    tier=2,
                    status=1,
                ),
            )
        else:
            session.status = 3  # COMPLETE
            async for event in self._session_build_result(session):
                yield event

    @staticmethod
    def _build_proposals_from_remaining(
        violations: list[ViolationDict],
        *,
        tier: int,
    ) -> list[Proposal]:
        """Convert remaining violations into Proposal protos for client review.

        These proposals intentionally omit before_text/after_text/diff_hunk:
        they represent violations that need AI (Tier 2) or agentic (Tier 3)
        processing to generate actual fixes.  The approval path in
        _session_apply_approved skips proposals without after_text.
        When Tier 2 AI is wired (ADR-025), it will populate these fields.

        Args:
            violations: Violation dicts from the remediation report.
            tier: Remediation tier (2=AI, 3=agentic).

        Returns:
            List of Proposal protos.
        """
        proposals: list[Proposal] = []
        for i, v in enumerate(violations):
            line = v.get("line", 0)
            if isinstance(line, list | tuple):
                line_start = line[0] if line else 0
                line_end = line[1] if len(line) > 1 else line_start
            else:
                line_start = int(line) if line else 0
                line_end = line_start

            proposals.append(
                Proposal(
                    id=f"t{tier}-{i:04d}",
                    file=str(v.get("file", "")),
                    rule_id=str(v.get("rule_id", "")),
                    line_start=line_start,
                    line_end=line_end,
                    explanation=str(v.get("description", "")),
                    confidence=0.0,
                    tier=tier,
                )
            )
        return proposals

    @staticmethod
    def _session_apply_approved(
        session: SessionState,
        approved_ids: set[str],
    ) -> int:
        """Apply approved proposals to session working state.

        Args:
            session: Active session whose working files will be mutated.
            approved_ids: Set of proposal IDs the user accepted.

        Returns:
            Number of proposals successfully applied.
        """
        applied = 0
        for pid in approved_ids:
            proposal = session.proposals.get(pid)
            if not proposal or not proposal.after_text:
                continue
            content = session.working_files.get(proposal.file, b"")
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            if proposal.before_text not in text:
                continue
            new_text = text.replace(proposal.before_text, proposal.after_text, 1)
            session.working_files[proposal.file] = new_text.encode("utf-8")
            session.proposals.pop(pid)
            applied += 1

        if not session.proposals:
            session.status = 3  # COMPLETE
        return applied

    async def _session_build_result(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Build and yield the final SessionResult event.

        Args:
            session: Completed session with working files to diff.

        Yields:
            SessionEvent: Event containing the SessionResult.
        """
        patches: list[FilePatch] = []
        for path, patched in session.working_files.items():
            original = session.original_files.get(path, b"")
            if patched != original:
                diff = "".join(
                    difflib.unified_diff(
                        original.decode("utf-8", errors="replace").splitlines(keepends=True),
                        patched.decode("utf-8", errors="replace").splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    ),
                )
                patches.append(
                    FilePatch(
                        path=path,
                        original=original,
                        patched=patched,
                        diff=diff,
                    )
                )

        remaining_violations = [violation_dict_to_proto(v) for v in session.remaining_ai + session.remaining_manual]  # type: ignore[arg-type]

        yield SessionEvent(
            result=SessionResult(
                patches=patches,
                report=session.report or FixReport(),
                remaining_violations=remaining_violations,
            ),
        )

    async def _session_replay_state(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Re-send current session state on resume.

        Args:
            session: Session to replay state for.

        Yields:
            SessionEvent: Events reflecting the session's current state.
        """
        if session.tier1_patches or session.format_diffs:
            yield SessionEvent(
                tier1_complete=Tier1Summary(
                    applied_patches=session.tier1_patches,
                    format_diffs=session.format_diffs,
                    idempotency_ok=session.idempotency_ok,
                    report=session.report or FixReport(),
                ),
            )
        if session.proposals and session.status == 1:  # AWAITING_APPROVAL
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=list(session.proposals.values()),
                    tier=session.current_tier,
                    status=1,
                ),
            )
        if session.status == 3:  # COMPLETE
            async for event in self._session_build_result(session):
                yield event

    # ── Cache proxy RPCs ──────────────────────────────────────────────

    async def _cache_channel(self) -> grpc.aio.Channel:
        """Get async gRPC channel to CacheMaintainer.

        Returns:
            Open gRPC channel.

        Raises:
            grpc.RpcError: If APME_CACHE_GRPC_ADDRESS is not configured.
        """
        addr = os.environ.get("APME_CACHE_GRPC_ADDRESS")
        if not addr:
            raise grpc.RpcError("APME_CACHE_GRPC_ADDRESS not configured")
        return grpc.aio.insecure_channel(
            addr,
            options=[("grpc.max_receive_message_length", _GRPC_MAX_MSG)],
        )

    async def PullGalaxy(
        self,
        request: cache_pb2.PullGalaxyRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> cache_pb2.PullGalaxyResponse:
        """Proxy PullGalaxy to the CacheMaintainer service.

        Args:
            request: PullGalaxy request.
            context: gRPC servicer context.

        Returns:
            PullGalaxyResponse from the cache service.
        """
        channel = await self._cache_channel()
        try:
            stub = cache_pb2_grpc.CacheMaintainerStub(channel)  # type: ignore[no-untyped-call]
            return await stub.PullGalaxy(request, timeout=120)  # type: ignore[no-any-return]
        finally:
            await channel.close(grace=None)

    async def PullRequirements(
        self,
        request: cache_pb2.PullRequirementsRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> cache_pb2.PullRequirementsResponse:
        """Proxy PullRequirements to the CacheMaintainer service.

        Args:
            request: PullRequirements request.
            context: gRPC servicer context.

        Returns:
            PullRequirementsResponse from the cache service.
        """
        channel = await self._cache_channel()
        try:
            stub = cache_pb2_grpc.CacheMaintainerStub(channel)  # type: ignore[no-untyped-call]
            return await stub.PullRequirements(request, timeout=120)  # type: ignore[no-any-return]
        finally:
            await channel.close(grace=None)

    async def CloneOrg(
        self,
        request: cache_pb2.CloneOrgRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> cache_pb2.CloneOrgResponse:
        """Proxy CloneOrg to the CacheMaintainer service.

        Args:
            request: CloneOrg request.
            context: gRPC servicer context.

        Returns:
            CloneOrgResponse from the cache service.
        """
        channel = await self._cache_channel()
        try:
            stub = cache_pb2_grpc.CacheMaintainerStub(channel)  # type: ignore[no-untyped-call]
            return await stub.CloneOrg(request, timeout=300)  # type: ignore[no-any-return]
        finally:
            await channel.close(grace=None)

    # ── Health RPC (aggregate) ────────────────────────────────────────

    async def Health(
        self,
        request: HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Aggregate health: Primary is ok, plus probe all downstream services.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with aggregate status and downstream service health.
        """
        downstream: list[ServiceHealth] = []

        # Probe validators
        for name, env_var in VALIDATOR_ENV_VARS.items():
            addr = os.environ.get(env_var)
            if not addr:
                continue
            try:
                channel = grpc.aio.insecure_channel(addr)
                try:
                    stub = validate_pb2_grpc.ValidatorStub(channel)  # type: ignore[no-untyped-call]
                    resp = await stub.Health(HealthRequest(), timeout=5)
                    downstream.append(ServiceHealth(name=name, status=resp.status, address=addr))
                finally:
                    await channel.close(grace=None)
            except Exception as e:
                downstream.append(ServiceHealth(name=name, status=f"error: {e}", address=addr))

        # Probe cache
        cache_addr = os.environ.get("APME_CACHE_GRPC_ADDRESS")
        if cache_addr:
            try:
                channel = grpc.aio.insecure_channel(cache_addr)
                try:
                    cache_stub = cache_pb2_grpc.CacheMaintainerStub(channel)  # type: ignore[no-untyped-call]
                    resp = await cache_stub.Health(HealthRequest(), timeout=5)
                    downstream.append(ServiceHealth(name="cache", status=resp.status, address=cache_addr))
                finally:
                    await channel.close(grace=None)
            except Exception as e:
                downstream.append(ServiceHealth(name="cache", status=f"error: {e}", address=cache_addr))

        return HealthResponse(status="ok", downstream=downstream)

    # ── Scan event subscription (ADR-020) ─────────────────────────────

    async def SubscribeScanEvents(
        self,
        request: SubscribeRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> AsyncIterator[ScanCompletedEvent]:
        """Stream ScanCompletedEvent to any connected listener.

        The gateway connects here on startup to receive all scan results
        regardless of origin (CLI, CI, web). The dependency flows
        gateway → Primary, keeping the engine unaware of downstream
        consumers.

        Args:
            request: Subscribe request (unused, reserved for future filters).
            context: gRPC servicer context.

        Yields:
            ScanCompletedEvent: Event for each completed scan.
        """
        from apme_engine.daemon.scan_events import subscribe, unsubscribe

        queue = subscribe()
        try:
            while not context.cancelled():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield event
                except asyncio.TimeoutError:
                    continue
        finally:
            unsubscribe(queue)


async def serve(listen_address: str = "0.0.0.0:50051") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Primary servicer.

    Args:
        listen_address: Host:port to bind (e.g. 0.0.0.0:50051).

    Returns:
        Started gRPC server (caller must wait_for_termination).
    """
    server = grpc.aio.server(
        maximum_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
        options=[
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
        ],
    )
    primary_pb2_grpc.add_PrimaryServicer_to_server(PrimaryServicer(), server)  # type: ignore[no-untyped-call]
    if ":" in listen_address:
        _, _, port = listen_address.rpartition(":")
        server.add_insecure_port(f"[::]:{port}")
    else:
        server.add_insecure_port(listen_address)
    await server.start()
    return server
