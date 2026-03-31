"""Primary daemon: async gRPC server that runs engine then fans out to all validators.

The Primary is the sole API surface for all clients (CLI, web UI, CI).
Clients send file bytes via gRPC streams and receive processed bytes back.
The Primary delegates internally to validators and remediation.
"""

import asyncio
import contextlib
import contextvars
import difflib
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import grpc.aio

from apme.v1 import primary_pb2_grpc, validate_pb2_grpc
from apme.v1.common_pb2 import (
    CollectionRef,
    File,
    HealthRequest,
    HealthResponse,
    ProgressUpdate,
    ProjectManifest,
    PythonPackageRef,
    ScanSummary,
    ServiceHealth,
    ValidatorDiagnostics,
)
from apme.v1.primary_pb2 import (
    AIModelInfo,
    ApprovalAck,
    FileDiff,
    FilePatch,
    FixOptions,
    FixReport,
    FormatRequest,
    FormatResponse,
    ListAIModelsRequest,
    ListAIModelsResponse,
    Proposal,
    ProposalsReady,
    ScanChunk,
    ScanDiagnostics,
    ScanOptions,
    SessionClosed,
    SessionCommand,
    SessionCreated,
    SessionEvent,
    SessionResult,
    Tier1Summary,
)
from apme.v1.reporting_pb2 import (
    FixCompletedEvent,
    ProposalOutcome,
)
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon.event_emitter import emit_fix_completed, start_sinks
from apme_engine.daemon.session import ResourceExhaustedError, SessionState, SessionStore
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.engine.jsonpickle_handlers import register_engine_handlers
from apme_engine.engine.models import AnsibleRunContext, ViolationDict
from apme_engine.log_bridge import attach_collector
from apme_engine.runner import run_scan
from apme_engine.venv_manager.session import (
    VenvSession,
    VenvSessionManager,
    get_dependency_tree,
    list_installed_collections,
    list_installed_packages,
)

logger = logging.getLogger("apme.primary")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_PRIMARY_MAX_RPCS", "16"))
_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — hierarchy+scandata can exceed the 4 MiB default


@dataclass
class _ValidatorResult:
    """Result from a single validator RPC call.

    Attributes:
        violations: List of violation dicts from the validator.
        diagnostics: Optional ValidatorDiagnostics from the response.
        logs: ProgressUpdate entries collected by the validator (ADR-033).
    """

    violations: list[ViolationDict] = field(default_factory=list)
    diagnostics: ValidatorDiagnostics | None = None
    logs: list[ProgressUpdate] = field(default_factory=list)


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


_SNIPPET_CONTEXT_LINES = 10


def _attach_snippets(violations: list[ViolationDict], files: list[File]) -> None:
    """Attach source snippet to each violation from the scanned file content.

    Extracts lines around the violation's line number (10 before, 10 after)
    and stores them as a ``snippet`` key on the violation dict.

    Args:
        violations: Violation dicts to enrich (mutated in place).
        files: File protos with path and content from the scan.
    """
    violated_paths = {str(v.get("file", "")) for v in violations}
    file_lines: dict[str, list[str]] = {}
    for f in files:
        if f.path not in violated_paths:
            continue
        try:
            file_lines[f.path] = f.content.decode("utf-8", errors="replace").splitlines()
        except Exception:  # noqa: BLE001
            continue

    for v in violations:
        fpath = str(v.get("file", ""))
        lines = file_lines.get(fpath)
        if not lines:
            continue
        raw_line = v.get("line")
        if isinstance(raw_line, list | tuple):
            line_no = int(raw_line[0]) if raw_line else 0
        elif isinstance(raw_line, int):
            line_no = raw_line
        else:
            continue
        if line_no < 1:
            continue
        start = max(0, line_no - 1 - _SNIPPET_CONTEXT_LINES)
        end = min(len(lines), line_no + _SNIPPET_CONTEXT_LINES)
        numbered = [f"{i + 1:>4}: {lines[i]}" for i in range(start, end)]
        v["snippet"] = "\n".join(numbered)


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
        logger.debug(
            "Primary: normalized scandata.contexts %d -> %d (dropped non-AnsibleRunContext)",
            len(materialized),
            len(valid),
        )
    scandata.contexts = valid


def _write_chunked_fs(files: list[File]) -> Path:
    """Write request.files into a temp directory; return path to that directory.

    File paths are sanitised: absolute paths and ``..`` segments are rejected
    to prevent writes outside the temp directory.

    Args:
        files: List of File protos with path and content.

    Returns:
        Path to the created temp directory.

    Raises:
        ValueError: If a file path is absolute or escapes the temp root.
    """
    tmp = Path(tempfile.mkdtemp(prefix="apme_primary_")).resolve()
    for f in files:
        rel = Path(f.path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe file path rejected: {f.path!r}")
        path = (tmp / rel).resolve()
        if not path.is_relative_to(tmp):
            raise ValueError(f"Path escapes temp root: {f.path!r}")
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
            logs=list(resp.logs),
        )
    except grpc.RpcError as e:
        logger.error("Validator at %s failed (req=%s): %s", address, req_id, e)
        return _ValidatorResult()
    finally:
        await channel.close(grace=None)


_REQUIREMENTS_PATHS = {"requirements.yml", "collections/requirements.yml"}


def _discover_collection_specs(files: Sequence[File]) -> tuple[list[str], list[str]]:
    """Extract collection specs from requirements.yml files in the uploaded file set.

    Looks for ``requirements.yml`` and ``collections/requirements.yml``.
    Parses the ``collections`` key and returns ``name[:version]`` strings.

    Args:
        files: Uploaded File protos (or duck-typed objects with ``path``/``content``).

    Returns:
        Tuple of (deduplicated collection specifiers, matched file paths).
    """
    import yaml

    specs: dict[str, str] = {}
    found_paths: list[str] = []
    for f in files:
        norm = f.path.replace("\\", "/").lstrip("/")
        if norm not in _REQUIREMENTS_PATHS:
            continue
        found_paths.append(norm)
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
    return list(specs.values()), found_paths


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


def _classify_collections(
    installed: list[tuple[str, str]],
    specified_fqcns: set[str],
    learned_fqcns: set[str],
) -> list[tuple[str, str, str]]:
    """Classify each installed collection by how it was discovered.

    Args:
        installed: ``(fqcn, version)`` pairs from ``list_installed_collections``.
        specified_fqcns: FQCNs explicitly listed in requirements files.
        learned_fqcns: FQCNs discovered via playbook FQCN references.

    Returns:
        List of ``(fqcn, version, source)`` where *source* is one of
        ``"specified"``, ``"learned"``, or ``"dependency"``.
    """
    result: list[tuple[str, str, str]] = []
    for fqcn, version in installed:
        if fqcn in specified_fqcns:
            source = "specified"
        elif fqcn in learned_fqcns:
            source = "learned"
        else:
            source = "dependency"
        result.append((fqcn, version, source))
    return result


def _build_manifest(session: SessionState) -> ProjectManifest:
    """Build a ProjectManifest from session state captured during scanning.

    Constructs ``CollectionRef`` messages from classified ``(fqcn, version,
    source)`` tuples and ``PythonPackageRef`` from ``installed_packages``.

    Args:
        session: Session with manifest fields populated by ``scan_fn``.

    Returns:
        ProjectManifest ready for embedding in FixCompletedEvent.
    """
    collections: list[CollectionRef] = [
        CollectionRef(fqcn=fqcn, version=version, source=source)
        for fqcn, version, source in session.installed_collections
    ]

    packages: list[PythonPackageRef] = [
        PythonPackageRef(name=name, version=ver) for name, ver in session.installed_packages
    ]

    return ProjectManifest(
        ansible_core_version=session.ansible_core_version,
        collections=collections,
        python_packages=packages,
        requirements_files=session.requirements_files,
        dependency_tree=session.dependency_tree,
    )


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

    The Primary is the sole venv authority — it calls
    ``VenvSessionManager.acquire()`` before fanning out to validators,
    passing the resolved ``venv_path`` so validators never write to venvs.
    """

    _venv_mgr: VenvSessionManager | None = None

    def _get_venv_manager(self) -> VenvSessionManager:
        """Return (or create) the singleton VenvSessionManager.

        Returns:
            The shared VenvSessionManager instance.
        """
        if self._venv_mgr is None:
            self._venv_mgr = VenvSessionManager()
        return self._venv_mgr

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
        session_id: str = "",
        progress_callback: Callable[[str, str, float, int], None] | None = None,
    ) -> tuple[
        list[ViolationDict],
        ScanDiagnostics | None,
        str,
        list[list[ProgressUpdate]],
        Mapping[str, object] | None,
        VenvSession | None,
        list[str],
        set[str],
        set[str],
    ]:
        """Core scan pipeline: engine → collection discovery → venv → validators.

        Reused by FixSession (as scan_fn for remediation).

        Every scan gets a session-scoped venv.  The flow is:

        1. **ARI tree build** — if a warm session venv exists its
           ``site-packages`` is passed as ``dependency_dir`` so ARI can
           resolve pre-installed collections.
        2. **Collection discovery** — FQCNs from files + hierarchy payload.
        3. **Venv acquire** — ``VenvSessionManager.acquire()`` creates the
           venv (cold start) or incrementally installs new collections
           (warm hit).  A transient ``session_id`` is generated when the
           client does not provide one.
        4. **Validator fan-out** — all validators receive ``venv_path``.

        Args:
            temp_dir: Directory containing the materialized files.
            files: Original File protos (for ValidateRequest).
            scan_id: Request ID for correlation.
            ansible_core_version: Ansible core version constraint.
            collection_specs: Collection specifiers (may be extended by discovery).
            include_scandata: Whether to include scandata in engine call.
            session_id: Client-provided session ID for venv reuse.
            progress_callback: Optional callback ``(phase, message, fraction)``
                for streaming per-validator progress to callers.

        Returns:
            Tuple of (violations, ScanDiagnostics or None, resolved session_id,
            merged pipeline logs, hierarchy_payload Mapping or None,
            VenvSession or None, requirements file paths found,
            specified collection FQCNs, learned collection FQCNs).
        """
        from apme_engine.validators.ansible._venv import DEFAULT_VERSION
        from apme_engine.venv_manager.session import _venv_site_packages

        scan_t0 = time.monotonic()
        collection_specs = list(collection_specs or [])

        core_version = ansible_core_version or DEFAULT_VERSION
        sid = session_id or uuid.uuid4().hex[:12]

        # Check for warm session venv so ARI can resolve pre-installed collections
        ari_dependency_dir = ""
        warm = self._get_venv_manager().get(sid, core_version)
        if warm and warm.venv_root.is_dir():
            with contextlib.suppress(FileNotFoundError):
                ari_dependency_dir = str(_venv_site_packages(warm.venv_root))
            if ari_dependency_dir:
                logger.debug("Session(%s): warm venv, ARI dependency_dir=%s", sid, ari_dependency_dir)

        # 1. ARI tree build
        ctx = contextvars.copy_context()
        context_obj = await asyncio.get_event_loop().run_in_executor(
            None,
            ctx.run,
            lambda: run_scan(
                str(temp_dir),
                str(temp_dir),
                include_scandata=include_scandata,
                dependency_dir=ari_dependency_dir,
            ),
        )

        if not context_obj.hierarchy_payload:
            logger.warning("Scan: no hierarchy payload produced (req=%s)", scan_id)
            return [], ScanDiagnostics(), sid, [], None, None, [], set(), set()

        # 2. Collection discovery
        discovered, requirements_found = _discover_collection_specs(files)
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

        # 3. Venv acquire (always — creates or incrementally installs)
        venv_session = await asyncio.get_event_loop().run_in_executor(
            None,
            ctx.run,
            self._get_venv_manager().acquire,
            sid,
            core_version,
            collection_specs,
        )
        venv_path = str(venv_session.venv_root)
        if venv_session.failed_collections:
            logger.warning(
                "Venv: %d collection(s) failed to install (session=%s, req=%s): %s — scan will continue without them",
                len(venv_session.failed_collections),
                sid,
                scan_id,
                ", ".join(venv_session.failed_collections),
            )
        logger.info(
            "Venv: ready (%d collections installed, session=%s, req=%s)",
            len(venv_session.installed_collections),
            sid,
            scan_id,
        )

        # 4. Validator fan-out
        content_graph_data = b""
        if context_obj.scandata and hasattr(context_obj.scandata, "content_graph"):
            cg = context_obj.scandata.content_graph
            if cg is not None:
                loop = asyncio.get_event_loop()
                content_graph_data = await loop.run_in_executor(None, lambda: json.dumps(cg.to_dict()).encode())
                logger.debug(
                    "ContentGraph serialized: %d bytes (req=%s)",
                    len(content_graph_data),
                    scan_id,
                )

        validate_request = ValidateRequest(
            request_id=scan_id,
            project_root="",
            files=files,
            hierarchy_payload=json.dumps(context_obj.hierarchy_payload, default=str).encode(),
            ansible_core_version=core_version,
            collection_specs=collection_specs,
            session_id=sid,
            venv_path=venv_path,
            content_graph_data=content_graph_data,
        )

        _pcb = progress_callback

        task_names: list[str] = []
        task_coros: list[Awaitable[_ValidatorResult]] = []
        for name, env_var in VALIDATOR_ENV_VARS.items():
            addr = os.environ.get(env_var)
            if not addr:
                continue
            task_names.append(name)
            task_coros.append(_call_validator(addr, validate_request))

        violations: list[ViolationDict] = []
        validator_diagnostics: list[ValidatorDiagnostics] = []
        validator_logs: list[list[ProgressUpdate]] = []
        fan_out_ms = 0.0

        if task_coros:
            num_validators = len(task_coros)
            if _pcb:
                _pcb("scan", f"Dispatching to {num_validators} validators...", 0.0, 2)
            logger.info("Fan-out: dispatching to %d validators (req=%s)", num_validators, scan_id)
            fan_t0 = time.monotonic()

            validators_done = 0

            async def _run_validator(
                name: str,
                coro: Awaitable[_ValidatorResult],
            ) -> tuple[str, _ValidatorResult]:
                nonlocal validators_done
                try:
                    result: _ValidatorResult = await coro
                except BaseException as exc:
                    validators_done += 1
                    if _pcb:
                        _pcb("scan", f"{name.title()}: error: {exc}", validators_done / num_validators, 4)
                    raise
                else:
                    validators_done += 1
                    if _pcb:
                        count = len(result.violations)
                        _pcb("scan", f"{name.title()}: {count} findings", validators_done / num_validators, 2)
                    return name, result

            named_results = await asyncio.gather(
                *[_run_validator(n, c) for n, c in zip(task_names, task_coros, strict=True)],
                return_exceptions=True,
            )
            fan_out_ms = (time.monotonic() - fan_t0) * 1000

            counts: dict[str, int] = {}
            for vname, item in zip(task_names, named_results, strict=True):
                if isinstance(item, BaseException):
                    logger.error("Validator %s raised (req=%s): %s", vname, scan_id, item)
                    continue
                name, result = item
                counts[name] = len(result.violations)
                violations.extend(result.violations)
                if result.diagnostics:
                    validator_diagnostics.append(result.diagnostics)
                if result.logs:
                    validator_logs.append(list(result.logs))

            parts = " ".join(f"{n.title()}={counts.get(n, 0)}" for n in VALIDATOR_ENV_VARS)
            logger.info("Fan-out: done (%.0fms) %s Total=%d (req=%s)", fan_out_ms, parts, len(violations), scan_id)

        violations = _deduplicate_violations(_sort_violations(violations))
        _attach_snippets(violations, files)

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
        specified_fqcns = {s.split(":")[0] for s in discovered}
        learned_fqcns = {str(c) for c in hierarchy_collections if isinstance(c, str)}

        logger.info("Scan: pipeline done (%.0fms, %d violations, req=%s)", total_ms, len(violations), scan_id)
        return (
            violations,
            diag,
            sid,
            validator_logs,
            context_obj.hierarchy_payload,
            venv_session,
            requirements_found,
            specified_fqcns,
            learned_fqcns,
        )

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

    # ── Format RPCs ───────────────────────────────────────────────────

    async def Format(self, request: FormatRequest, context: grpc.aio.ServicerContext) -> FormatResponse:  # type: ignore[type-arg]
        """Handle unary Format RPC: return diffs for files needing reformatting.

        Args:
            request: Format request containing files.
            context: gRPC servicer context.

        Returns:
            FormatResponse with file diffs.
        """
        with attach_collector() as sink:
            logger.info("Format: start (%d files)", len(request.files))
            t0 = time.monotonic()
            diffs = await asyncio.get_event_loop().run_in_executor(
                None,
                self._format_files,  # type: ignore[arg-type]
                list(request.files),
            )
            dur = (time.monotonic() - t0) * 1000
            logger.info("Format: done (%.0fms, %d files changed)", dur, len(diffs))
            return FormatResponse(diffs=diffs, logs=sink.entries)

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
        with attach_collector() as sink:
            logger.info("FormatStream: start (%d files, req=%s)", len(all_files), scan_id)
            t0 = time.monotonic()
            diffs = await asyncio.get_event_loop().run_in_executor(
                None,
                self._format_files,
                all_files,
            )
            dur = (time.monotonic() - t0) * 1000
            logger.info("FormatStream: done (%.0fms, %d files changed, req=%s)", dur, len(diffs), scan_id)
            return FormatResponse(diffs=diffs, logs=sink.entries)

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
                        peer = context.peer()
                        logger.info(
                            "FixSession: processing %d file(s) (session_id=%s, scan_id=%s, peer=%s)",
                            len(session.original_files),
                            session.session_id,
                            scan_id,
                            peer,
                        )
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
        except ValueError as ve:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(ve))
        except Exception as e:
            logger.exception("FixSession failed (session=%s): %s", scan_id, e)
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
        session.scan_id = scan_id
        session.project_root = first_chunk.project_root or ""
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
        fix_session_id = ""
        if fix_opts:
            ansible_core_version = fix_opts.ansible_core_version
            collection_specs = list(fix_opts.collection_specs)
            fix_session_id = fix_opts.session_id
            if fix_opts.max_passes > 0:
                max_passes = fix_opts.max_passes
        elif scan_opts:
            ansible_core_version = scan_opts.ansible_core_version
            collection_specs = list(scan_opts.collection_specs)
            fix_session_id = scan_opts.session_id

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
        _fmt_start = ProgressUpdate(
            message=f"Formatting {len(all_files)} file(s)...",
            phase="format",
            level=2,  # INFO
        )
        session.progress_logs.append(_fmt_start)
        yield SessionEvent(progress=_fmt_start)
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
            _fmt_done = ProgressUpdate(
                message=f"Formatted {len(format_diffs)} file(s)",
                phase="format",
                level=2,
            )
            session.progress_logs.append(_fmt_done)
            yield SessionEvent(progress=_fmt_done)

        # Phase 2: Idempotency check
        idem_diffs = await asyncio.get_event_loop().run_in_executor(
            None,
            self._format_files,
            formatted_files,
        )
        session.idempotency_ok = len(idem_diffs) == 0
        if not session.idempotency_ok:
            _idem_warn = ProgressUpdate(
                message="Formatter is not idempotent on this input",
                phase="format",
                level=3,  # WARNING
            )
            session.progress_logs.append(_idem_warn)
            yield SessionEvent(progress=_idem_warn)

        # Phase 3+4: Scan + Remediate via convergence loop
        _t1_start = ProgressUpdate(
            message="Running Tier 1 remediation...",
            phase="tier1",
            level=2,
        )
        session.progress_logs.append(_t1_start)
        yield SessionEvent(progress=_t1_start)

        loop = asyncio.get_event_loop()

        from apme_engine.engine.node_index import NodeIndex  # noqa: PLC0415

        node_index_set = False
        engine_ref: list[RemediationEngine | None] = [None]

        # Thread-safe progress queue: the executor thread posts here,
        # the async generator drains and yields SessionEvents.
        _HEARTBEAT_INTERVAL = 15
        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        def _progress_callback(phase: str, message: str, fraction: float = 0.0, level: int = 2) -> None:
            loop.call_soon_threadsafe(
                progress_queue.put_nowait,
                ProgressUpdate(message=message, phase=phase, progress=fraction, level=level),
            )

        manifest_captured = False

        def scan_fn(file_paths: list[str]) -> list[ViolationDict]:
            nonlocal node_index_set, manifest_captured
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
                session_id=fix_session_id,
                progress_callback=_progress_callback,
            )
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            (
                violations,
                _,
                _,
                _,
                hierarchy_payload,
                venv_sess,
                req_files,
                specified_fqcns,
                learned_fqcns,
            ) = future.result(timeout=300)

            if not manifest_captured and venv_sess is not None:
                manifest_captured = True
                session.ansible_core_version = venv_sess.ansible_version
                session.installed_collections = _classify_collections(
                    list_installed_collections(venv_sess.venv_root),
                    specified_fqcns,
                    learned_fqcns,
                )
                session.installed_packages = list_installed_packages(venv_sess.venv_root)
                session.dependency_tree = get_dependency_tree(venv_sess.venv_root)
                session.requirements_files = req_files

            if hierarchy_payload and not node_index_set:
                node_index_set = True
                node_index = NodeIndex(hierarchy_payload)
                if len(node_index) > 0 and engine_ref[0] is not None:
                    engine_ref[0].set_node_index(node_index)
                    logger.info("NodeIndex: indexed %d hierarchy nodes for unit segmentation", len(node_index))

            return violations

        registry = build_default_registry()
        ai_provider = self._resolve_ai_provider(fix_opts)
        engine = RemediationEngine(
            registry=registry,
            scan_fn=scan_fn,
            max_passes=max_passes,
            verbose=True,
            ai_provider=ai_provider,  # type: ignore[arg-type]
            progress_callback=_progress_callback,
        )
        engine_ref[0] = engine

        yaml_paths = [str(temp_dir / f.path) for f in formatted_files if f.path.endswith((".yml", ".yaml"))]

        async def _heartbeat() -> None:
            """Send periodic heartbeats while remediation is running."""
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                progress_queue.put_nowait(ProgressUpdate(message="Processing...", phase="heartbeat", level=1))

        heartbeat_task = asyncio.create_task(_heartbeat())
        remediate_future = loop.run_in_executor(None, engine.remediate, yaml_paths)

        try:
            # Drain progress queue while remediation runs, yielding events
            while not remediate_future.done():
                try:
                    update = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if update is not None:
                    session.progress_logs.append(update)
                    yield SessionEvent(progress=update)

            # Drain any remaining queued progress
            while not progress_queue.empty():
                update = progress_queue.get_nowait()
                if update is not None:
                    session.progress_logs.append(update)
                    yield SessionEvent(progress=update)

            report = remediate_future.result()
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            if not remediate_future.done():
                remediate_future.cancel()

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
        fixed_violation_protos = [violation_dict_to_proto(v) for v in report.fixed_violations]
        session.report = FixReport(
            passes=report.passes,
            fixed=report.fixed,
            remaining_ai=len(report.remaining_ai),
            remaining_manual=len(report.remaining_manual),
            oscillation_detected=report.oscillation_detected,
            remaining_violations=remaining_violations,
            fixed_violations=fixed_violation_protos,
        )

        _t1_done = ProgressUpdate(
            message=(f"Tier 1 converged: {report.passes} pass(es), {report.fixed} fixed"),
            phase="tier1",
            level=2,
        )
        session.progress_logs.append(_t1_done)
        yield SessionEvent(progress=_t1_done)

        yield SessionEvent(
            tier1_complete=Tier1Summary(
                applied_patches=tier1_patches,
                format_diffs=list(format_diffs),
                idempotency_ok=session.idempotency_ok,
                report=session.report,
            ),
        )

        # Only present proposals when the AI engine produced real fixes
        # with before/after text.  Stub proposals (violations without diffs)
        # are not actionable and just confuse the user.
        if report.ai_proposed:
            session.current_tier = 2
            proposals = self._build_proposals_from_ai(report.ai_proposed)
            for p in proposals:
                with contextlib.suppress(ValueError):
                    p.file = str(Path(p.file).relative_to(temp_dir))
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
    def _resolve_ai_provider(fix_opts: FixOptions | None) -> object | None:
        """Create an AbbenayProvider when AI escalation is requested.

        Uses fix_opts.ai_model for the model, falls back to APME_AI_MODEL
        env var.  Abbenay address is auto-discovered or read from
        APME_ABBENAY_ADDR.

        Args:
            fix_opts: FixOptions from the client request (may be None).

        Returns:
            AbbenayProvider instance, or None if AI is not enabled or
            prerequisites are missing.
        """
        if not fix_opts or not fix_opts.enable_ai:
            return None

        try:
            from apme_engine.remediation.abbenay_provider import (  # noqa: PLC0415
                AbbenayProvider,
                discover_abbenay,
            )
        except ImportError:
            logger.warning("AI escalation requested but abbenay_grpc is not installed")
            return None

        addr = os.environ.get("APME_ABBENAY_ADDR") or discover_abbenay()
        if not addr:
            logger.warning("AI escalation requested but no Abbenay daemon found")
            return None

        model = fix_opts.ai_model or os.environ.get("APME_AI_MODEL")
        if not model:
            logger.warning("AI escalation requested but no model specified (--model or APME_AI_MODEL)")
            return None

        token = os.environ.get("APME_ABBENAY_TOKEN")

        try:
            provider = AbbenayProvider(addr, token=token, model=model)
        except ImportError:
            logger.warning("Failed to create AbbenayProvider — abbenay-client not installed")
            return None

        logger.info("AI provider ready: %s model=%s", addr, model)
        return provider

    @staticmethod
    def _build_proposals_from_ai(
        ai_proposals: Sequence[object],
    ) -> list[Proposal]:
        """Convert AIProposal objects into Proposal protos with diff data.

        Each AIPatch within an AIProposal becomes a separate Proposal proto
        with status="proposed". Skipped violations become Proposal protos
        with status="declined" carrying the AI's reason and suggestion.

        Args:
            ai_proposals: AIProposal objects from the remediation engine.

        Returns:
            List of Proposal protos (both proposed and declined).
        """
        from apme_engine.remediation.ai_provider import AIProposal  # noqa: PLC0415

        proposals: list[Proposal] = []
        decline_idx = 0
        for idx, item in enumerate(ai_proposals):
            ap: AIProposal = item  # type: ignore[assignment]

            before_text = ap.original_snippet
            after_text = ap.fixed_snippet
            if before_text.endswith("\n") and after_text and not after_text.endswith("\n"):
                after_text += "\n"

            rule_id = ",".join(ap.rule_ids) if ap.rule_ids else "ai-fix"
            first_patch = ap.patches[0] if ap.patches else None

            proposals.append(
                Proposal(
                    id=f"t2-{idx:04d}",
                    file=ap.file,
                    rule_id=rule_id,
                    line_start=first_patch.line_start if first_patch else 0,
                    line_end=first_patch.line_end if first_patch else 0,
                    before_text=before_text,
                    after_text=after_text,
                    diff_hunk=ap.diff,
                    confidence=ap.confidence,
                    explanation=ap.explanation,
                    tier=2,
                    status="proposed",
                )
            )

            for sk in ap.skipped:
                proposals.append(
                    Proposal(
                        id=f"skip-{decline_idx:04d}",
                        file=ap.file,
                        rule_id=sk.rule_id,
                        line_start=sk.line,
                        line_end=sk.line,
                        explanation=sk.reason,
                        suggestion=sk.suggestion,
                        tier=2,
                        status="declined",
                        confidence=0.0,
                    )
                )
                decline_idx += 1
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
        if not approved_ids:
            session.status = 3  # COMPLETE
            return 0

        applied = 0
        for pid in approved_ids:
            proposal = session.proposals.get(pid)
            if not proposal:
                logger.warning("Skipping proposal %s: not found", pid)
                continue
            content = session.working_files.get(proposal.file, b"")
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            if proposal.before_text not in text:
                logger.warning(
                    "Skipping proposal %s (%s): before_text not found in working file %s",
                    pid,
                    proposal.rule_id,
                    proposal.file,
                )
                continue
            new_text = text.replace(proposal.before_text, proposal.after_text, 1)
            session.working_files[proposal.file] = new_text.encode("utf-8")
            session.approved_proposals.append(
                {
                    "proposal_id": pid,
                    "rule_id": proposal.rule_id,
                    "file": proposal.file,
                    "tier": proposal.tier,
                    "confidence": proposal.confidence,
                }
            )
            session.proposals.pop(pid)
            session.approved_ids.add(pid)
            applied += 1

        session.status = 3  # COMPLETE — user has finished reviewing
        logger.info(
            "Approval result: %d/%d proposals applied (session=%s)",
            applied,
            len(approved_ids),
            session.session_id,
        )
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

        report = session.report or FixReport()

        yield SessionEvent(
            result=SessionResult(
                patches=patches,
                report=report,
                remaining_violations=remaining_violations,
                fixed_violations=list(report.fixed_violations),
            ),
        )

        # Always emit FixCompletedEvent for both check and remediate modes.
        # The gateway's link_scan_to_project() sets the correct scan_type
        # ("check" or "remediate") based on the operation intent (ADR-039).
        await emit_fix_completed(
            self._build_fix_event(
                session,
                remaining_violations,
                list(report.fixed_violations),
                patches,
            )
        )

    @staticmethod
    def _build_fix_event(
        session: SessionState,
        remaining_violations: Sequence[object],
        fixed_violations: Sequence[object] | None = None,
        patches: Sequence[object] | None = None,
    ) -> FixCompletedEvent:
        """Build a FixCompletedEvent from completed session state.

        Args:
            session: Completed session.
            remaining_violations: Proto violations still open.
            fixed_violations: Proto violations that Tier 1 would fix.
            patches: FilePatch objects with per-file diffs.

        Returns:
            FixCompletedEvent ready for emission.
        """
        proposal_outcomes: list[ProposalOutcome] = []
        for meta in session.approved_proposals:
            tier_val = meta.get("tier", 0)
            conf_val = meta.get("confidence", 0.0)
            proposal_outcomes.append(
                ProposalOutcome(
                    proposal_id=str(meta.get("proposal_id", "")),
                    rule_id=str(meta.get("rule_id", "")),
                    file=str(meta.get("file", "")),
                    tier=int(tier_val) if isinstance(tier_val, (int, float, str)) else 0,
                    confidence=float(conf_val) if isinstance(conf_val, (int, float, str)) else 0.0,
                    status="approved",
                )
            )
        for pid, p in session.proposals.items():
            proposal_outcomes.append(
                ProposalOutcome(
                    proposal_id=pid,
                    rule_id=p.rule_id,
                    file=p.file,
                    tier=p.tier,
                    confidence=p.confidence,
                    status="rejected",
                )
            )

        from apme_engine.remediation.partition import count_by_remediation_class

        all_remaining = list(session.remaining_ai) + list(session.remaining_manual)
        report = session.report or FixReport()
        rem_counts = count_by_remediation_class(all_remaining)  # type: ignore[arg-type]
        summary = ScanSummary(
            total=len(all_remaining) + report.fixed,
            auto_fixable=report.fixed,
            ai_candidate=rem_counts.get("ai-candidate", 0),
            manual_review=rem_counts.get("manual-review", 0),
        )

        manifest = _build_manifest(session)

        return FixCompletedEvent(
            scan_id=session.scan_id or session.session_id,
            session_id=session.session_id,
            project_path=session.project_root,
            source="cli",
            remaining_violations=remaining_violations,  # type: ignore[arg-type]
            fixed_violations=fixed_violations or [],  # type: ignore[arg-type]
            summary=summary,
            report=report,
            proposals=proposal_outcomes,
            logs=session.progress_logs,
            patches=patches or [],  # type: ignore[arg-type]
            manifest=manifest,
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

    # ── ListAIModels RPC ────────────────────────────────────────────────

    async def ListAIModels(
        self,
        request: ListAIModelsRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> ListAIModelsResponse:
        """Return models available from the Abbenay daemon.

        Gracefully returns an empty list when Abbenay is unreachable
        or the ``abbenay_grpc`` client is not installed.

        Args:
            request: ListAIModels request (unused).
            context: gRPC servicer context.

        Returns:
            ListAIModelsResponse with available models.
        """
        try:
            from abbenay_grpc import AbbenayClient  # noqa: PLC0415
        except ImportError:
            logger.debug("abbenay_grpc not installed — returning empty model list")
            return ListAIModelsResponse(models=[])

        addr = os.environ.get("APME_ABBENAY_ADDR", "").strip()
        if not addr:
            return ListAIModelsResponse(models=[])

        try:
            if addr.startswith("unix://"):
                client = AbbenayClient(addr)
            else:
                host, sep, port_str = addr.rpartition(":")
                if sep:
                    client = AbbenayClient(host=host or "localhost", port=int(port_str))
                else:
                    client = AbbenayClient(host=addr)
            await client.connect()
            try:
                raw_models = await client.list_models()
            finally:
                await client.disconnect()

            models = [AIModelInfo(id=m.id, provider=m.provider, name=m.name) for m in raw_models]
            return ListAIModelsResponse(models=models)
        except Exception:
            logger.warning("Failed to list AI models from Abbenay at %s", addr, exc_info=True)
            return ListAIModelsResponse(models=[])

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

        return HealthResponse(status="ok", downstream=downstream)


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
    await start_sinks()
    return server
