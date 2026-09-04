"""Engine daemon: async gRPC server that runs engine then fans out to all validators.

The Engine is the sole API surface for all clients (CLI, web UI, CI).
Clients send file bytes via gRPC streams and receive processed bytes back.
The Engine delegates internally to validators and remediation.
"""

from __future__ import annotations

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
from typing import TypeVar

import grpc
import grpc.aio
import httpx

from apme.v1 import engine_pb2_grpc, reporting_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import (
    CollectionRef,
    File,
    GalaxyServerDef,
    HealthRequest,
    HealthResponse,
    ProgressUpdate,
    ProjectManifest,
    PythonPackageRef,
    ScanSummary,
    ServiceHealth,
    ValidatorDiagnostics,
)
from apme.v1.engine_pb2 import (
    AIModelInfo,
    AiTriageReady,
    ApprovalAck,
    FileDiff,
    FilePatch,
    FindingsReady,
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
    SessionError,
    SessionEvent,
    SessionResult,
    Tier1Summary,
)
from apme.v1.reporting_pb2 import (
    FixCompletedEvent,
    ProposalOutcome,
)
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon.deadline import (
    FALLBACK_NON_AI_OPERATION_BUDGET,
    BudgetConfigError,
    check_operation_deadline,
    count_ai_nodes,
    estimate_ai_budget,
    estimate_non_ai_budget,
    is_task_linked_progress,
    parse_ai_concurrency,
)
from apme_engine.daemon.event_emitter import emit_fix_completed, emit_register_rules, start_sinks
from apme_engine.daemon.fs_utils import write_chunked_fs as _write_chunked_fs
from apme_engine.daemon.session import ResourceExhaustedError, SessionState, SessionStore
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.engine.models import RemediationClass, ViolationDict
from apme_engine.graph.content_graph import ContentGraph
from apme_engine.graph.scanner import filter_noqa_violations, graph_rule_opt_in_from_rule_configs
from apme_engine.log_bridge import attach_collector
from apme_engine.remediation.graph_engine import FilePatch as SplicedFilePatch
from apme_engine.runner import run_scan
from apme_engine.venv_manager.session import (
    VenvSession,
    VenvSessionManager,
    get_dependency_tree,
    list_installed_collections,
    list_installed_packages,
)

logger = logging.getLogger("apme.engine")

_ExecutorResult = TypeVar("_ExecutorResult")

_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_ENGINE_MAX_RPCS", "16"))
_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — hierarchy+scandata can exceed the 4 MiB default


@dataclass
class _ValidatorResult:
    """Result from a single validator RPC call.

    Attributes:
        violations: List of violation dicts from the validator.
        diagnostics: Optional ValidatorDiagnostics from the response.
        logs: ProgressUpdate entries collected by the validator (ADR-033).
        error: Set when the validator RPC failed (e.g. ``grpc.RpcError``).
    """

    violations: list[ViolationDict] = field(default_factory=list)
    diagnostics: ValidatorDiagnostics | None = None
    logs: list[ProgressUpdate] = field(default_factory=list)
    error: str | None = None


def _write_session_galaxy_cfg(
    galaxy_servers: Sequence[GalaxyServerDef],
) -> Path | None:
    """Write a session-scoped ``ansible.cfg`` from proto Galaxy server defs (ADR-045).

    The caller is responsible for cleaning up the temp directory
    (typically via ``SessionState.cleanup``).

    Args:
        galaxy_servers: Ordered sequence of ``GalaxyServerDef`` proto messages.

    Returns:
        Path to the written ``ansible.cfg``, or ``None`` if no servers were
        provided or none had a url.
    """
    if not galaxy_servers:
        return None

    from galaxy_proxy.collection_downloader import (  # noqa: PLC0415
        GalaxyServerConfig,
        write_temp_ansible_cfg,
    )

    seen_names: set[str] = set()
    configs: list[GalaxyServerConfig] = []
    for i, s in enumerate(galaxy_servers):
        url = (s.url or "").strip()
        if not url:
            continue
        base_name = s.name or f"server_{i}"
        name = base_name
        suffix = 1
        while name in seen_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        seen_names.add(name)
        configs.append(
            GalaxyServerConfig(
                name=name,
                url=url,
                token=s.token or None,
                auth_url=s.auth_url or None,
            )
        )
    if not configs:
        return None

    cfg_dir = Path(tempfile.mkdtemp(prefix="apme-galaxy-session-"))
    try:
        return write_temp_ansible_cfg(configs, cfg_dir)
    except Exception:
        logger.exception("Failed to write session Galaxy config in %s", cfg_dir)
        return None


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


def _enrich_violations_from_graph(
    violations: list[ViolationDict],
    graph: ContentGraph,
    *,
    fixed: bool,
) -> None:
    """Attach node YAML and type from the graph to each violation.

    Graph-backed violations get ``node_type`` from ``ContentNode.node_type``.
    When progression exists, also set ``original_yaml`` and ``node_line_start``.

    Fixed violations additionally get ``fixed_yaml`` (final approved state)
    and ``co_fixes`` (other rule IDs that also modified this node).

    Args:
        violations: Violation dicts to enrich (mutated in place).
        graph: ContentGraph after convergence.
        fixed: When ``True``, also populate ``fixed_yaml`` and ``co_fixes``.
    """
    for v in violations:
        node_id = str(v.get("path", ""))
        if not node_id:
            continue
        node = graph.get_node(node_id)
        if node is None:
            continue

        v["node_type"] = node.node_type.value
        if not node.progression:
            continue

        v["original_yaml"] = node.progression[0].yaml_lines
        v["node_line_start"] = node.line_start

        if not fixed:
            continue

        approved = next(
            (s for s in reversed(node.progression) if s.approved),
            node.progression[-1],
        )
        v["fixed_yaml"] = approved.yaml_lines

        this_rule = str(v.get("rule_id", ""))
        co_fixes = sorted(
            rec.key[1] for rec in node.violation_ledger.values() if rec.status == "fixed" and rec.key[1] != this_rule
        )
        if co_fixes:
            v["co_fixes"] = co_fixes  # type: ignore[assignment]


def _collect_ai_triage_candidates(session: SessionState) -> list[ViolationDict]:
    """Return open AI-candidate findings on the session graph for escalation triage.

    Snippets use the **current** node YAML (post–Gate 1 / Quick-fix apply),
    not ``progression[0]`` scan baseline — that is what Abbenay will see next.

    Args:
        session: Session with a post–Tier-1 ContentGraph.

    Returns:
        Violation dicts classified as AI-candidate (enriched with node_type).
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415
    from apme_engine.remediation.partition import add_classification_to_violations  # noqa: PLC0415

    graph = session.content_graph
    if not isinstance(graph, ContentGraph):
        return []
    open_violations = [dict(v) for v in graph.query_violations(status="open")]
    add_classification_to_violations(open_violations)
    _enrich_violations_from_graph(open_violations, graph, fixed=False)
    out: list[ViolationDict] = []
    for v in open_violations:
        rc = v.get("remediation_class")
        rc_val = rc.value if isinstance(rc, RemediationClass) else str(rc or "")
        path = str(v.get("path") or "").strip()
        if rc_val != RemediationClass.AI_CANDIDATE.value or not path:
            continue
        # Overwrite scan-baseline snippet with live post–Quick-fix YAML.
        node = graph.get_node(path)
        if node is not None and node.yaml_lines:
            v["original_yaml"] = node.yaml_lines
            v["node_line_start"] = node.line_start
        out.append(v)
    return out


def _filter_violations_by_escalate_targets(
    violations: list[ViolationDict],
    targets: list[tuple[str, frozenset[str]]] | None,
) -> list[ViolationDict]:
    """Keep violations matching AI escalate allow-list.

    Args:
        violations: Open violation dicts.
        targets: ``(path, rule_ids)``; empty ``rule_ids`` means entire path.
            ``None`` = no filter (allow all). Empty list = skip AI.
            Rule IDs are compared after ``normalize_rule_id`` (legacy
            ``native:`` prefix).

    Returns:
        Filtered list (or all / none per ``targets``).
    """
    from apme_engine.remediation.partition import normalize_rule_id  # noqa: PLC0415

    if targets is None:
        return violations
    if not targets:
        return []
    allowed: list[ViolationDict] = []
    for v in violations:
        path = str(v.get("path") or "")
        rule = normalize_rule_id(str(v.get("rule_id") or ""))
        for t_path, rule_ids in targets:
            if path != t_path:
                continue
            if not rule_ids:
                allowed.append(v)
                break
            normalized_ids = frozenset(normalize_rule_id(r) for r in rule_ids)
            if rule in normalized_ids:
                allowed.append(v)
                break
    return allowed


def _decline_skipped_ai_escalation(session: SessionState) -> int:
    """Sticky-decline open AI-candidates not on the escalate allow-list.

    Gate 2 ``remediate`` rescans the full graph after each AI attempt; without
    this, skipped locations reopen as ``open`` and Abbenay runs on them too.

    Args:
        session: Session with ``ai_escalate_targets`` set (not ``None``).

    Returns:
        Number of ledger rows declined.
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415
    from apme_engine.remediation.partition import (  # noqa: PLC0415
        add_classification_to_violations,
        normalize_rule_id,
    )

    targets = session.ai_escalate_targets
    if targets is None:
        return 0
    graph = session.content_graph
    if not isinstance(graph, ContentGraph):
        return 0

    open_violations = [dict(v) for v in graph.query_violations(status="open")]
    add_classification_to_violations(open_violations)
    allowed = _filter_violations_by_escalate_targets(open_violations, targets)
    # Match ContentGraph ledger keys (path + normalized rule_id).
    allowed_keys = {(str(v.get("path") or ""), normalize_rule_id(str(v.get("rule_id") or ""))) for v in allowed}

    skipped: list[ViolationDict] = []
    for v in open_violations:
        rc = v.get("remediation_class")
        rc_val = rc.value if isinstance(rc, RemediationClass) else str(rc or "")
        if rc_val != RemediationClass.AI_CANDIDATE.value:
            continue
        key = (str(v.get("path") or ""), normalize_rule_id(str(v.get("rule_id") or "")))
        if key not in allowed_keys:
            skipped.append(v)

    if not skipped:
        return 0
    n = graph.decline_open_violations(skipped)
    if n:
        logger.info(
            "AI escalation: declined %d skipped AI-candidate(s) on session %s",
            n,
            session.session_id,
        )
    return n


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


async def _call_validator(
    address: str,
    request: ValidateRequest,
    timeout: int = 300,
) -> _ValidatorResult:
    """Call a validator over async gRPC; return violations + diagnostics.

    Args:
        address: gRPC address of the validator (e.g. localhost:50055).
        request: ValidateRequest to send.
        timeout: Request timeout in seconds (default 300 to accommodate
            collection health scanning of many large collections).

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
        return _ValidatorResult(error=str(e))
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
    installed: list[tuple[str, str, str, str]],
    specified_fqcns: set[str],
    learned_fqcns: set[str],
) -> list[tuple[str, str, str, str, str]]:
    """Classify each installed collection by how it was discovered.

    Args:
        installed: ``(fqcn, version, license, supplier)`` tuples from
            ``list_installed_collections``.
        specified_fqcns: FQCNs explicitly listed in requirements files.
        learned_fqcns: FQCNs discovered via playbook FQCN references.

    Returns:
        List of ``(fqcn, version, source, license, supplier)`` where *source*
        is one of ``"specified"``, ``"learned"``, or ``"dependency"``.
    """
    result: list[tuple[str, str, str, str, str]] = []
    for fqcn, version, lic, supplier in installed:
        if fqcn in specified_fqcns:
            source = "specified"
        elif fqcn in learned_fqcns:
            source = "learned"
        else:
            source = "dependency"
        result.append((fqcn, version, source, lic, supplier))
    return result


def _file_proto_from_path(fp: str, temp_dir: Path) -> File:
    """Read a file from disk and return a relative-path File proto.

    Args:
        fp: Absolute or relative path to read.
        temp_dir: Session temp root for relative path computation.

    Returns:
        File proto with path relative to ``temp_dir`` when possible.
    """
    p = Path(fp)
    rel = str(p.relative_to(temp_dir)) if p.is_absolute() else fp
    return File(path=rel, content=p.read_bytes())


def _load_yaml_originals(yaml_paths: list[str], temp_dir: Path) -> dict[str, str]:
    """Load YAML file contents keyed by absolute and relative paths.

    Args:
        yaml_paths: Paths to YAML files on disk.
        temp_dir: Session temp root for relative keys.

    Returns:
        Mapping of path strings to file text.
    """
    originals: dict[str, str] = {}
    for yp in yaml_paths:
        with contextlib.suppress(OSError):
            content = Path(yp).read_text(encoding="utf-8")
            originals[yp] = content
            with contextlib.suppress(ValueError):
                originals[str(Path(yp).relative_to(temp_dir))] = content
    return originals


def _build_manifest(session: SessionState) -> ProjectManifest:
    """Build a ProjectManifest from session state captured during scanning.

    Constructs ``CollectionRef`` messages from classified ``(fqcn, version,
    source, license, supplier)`` tuples and ``PythonPackageRef`` from
    ``(name, version, license, supplier)`` tuples in ``installed_packages``.

    Args:
        session: Session with manifest fields populated by ``scan_fn``.

    Returns:
        ProjectManifest ready for embedding in FixCompletedEvent.
    """
    collections: list[CollectionRef] = [
        CollectionRef(fqcn=fqcn, version=version, source=source, license=lic, supplier=sup)
        for fqcn, version, source, lic, sup in session.installed_collections
    ]

    packages: list[PythonPackageRef] = [
        PythonPackageRef(name=name, version=ver, license=lic, supplier=sup)
        for name, ver, lic, sup in session.installed_packages
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
    "collection_health": "COLLECTION_HEALTH_GRPC_ADDRESS",
    "dep_audit": "DEP_AUDIT_GRPC_ADDRESS",
}

# Core validators — scans fail when any required address is unset (ADR-005).
REQUIRED_VALIDATORS = frozenset({"native", "opa", "ansible"})


class RequiredValidatorDependencyError(RuntimeError):
    """Required validator missing, unreachable, or returned an RPC error."""


def _apply_rule_configs(
    violations: list[ViolationDict],
    rule_configs: list[object],
) -> list[ViolationDict]:
    """Filter and adjust violations based on ``RuleConfig`` overrides (ADR-041).

    - Violations for disabled rules are removed.
    - Severity is overridden when ``RuleConfig.severity`` differs from the
      violation's current value.
    - Enforced flag is attached as ``_enforced`` metadata so downstream
      ignore-annotation processing can respect it.

    Args:
        violations: Mutable list of violation dicts from validators.
        rule_configs: Proto ``RuleConfig`` messages from ``ScanOptions``.

    Returns:
        Filtered list with overrides applied.
    """
    if not rule_configs:
        return violations

    from apme_engine.graph.severity import severity_to_label

    config_map: dict[str, object] = {}
    for rc in rule_configs:
        config_map[rc.rule_id] = rc  # type: ignore[attr-defined]

    filtered: list[ViolationDict] = []
    for v in violations:
        rule_id = str(v.get("rule_id", ""))
        rc = config_map.get(rule_id)
        if rc is not None:
            if not rc.enabled:  # type: ignore[attr-defined]
                continue
            if rc.severity:  # type: ignore[attr-defined]
                from apme_engine.graph.severity import severity_from_proto

                v["severity"] = severity_to_label(severity_from_proto(rc.severity))  # type: ignore[attr-defined]
            if rc.enforced:  # type: ignore[attr-defined]
                v["_enforced"] = True
        filtered.append(v)
    return filtered


_known_rule_ids: set[str] = set()


class EngineServicer(engine_pb2_grpc.EngineServicer):
    """Engine gRPC servicer — sole API surface for all clients.

    Runs engine, fans out to validators, orchestrates format + remediation.
    Clients send file bytes in, receive processed bytes out.

    The Engine is the sole venv authority — it calls
    ``VenvSessionManager.acquire()`` before fanning out to validators,
    passing the resolved ``venv_path`` so validators never write to venvs.
    """

    _venv_mgr: VenvSessionManager | None = None
    _galaxy_proxy_cfg_lock: asyncio.Lock | None = None

    def _get_venv_manager(self) -> VenvSessionManager:
        """Return (or create) the singleton VenvSessionManager.

        Returns:
            The shared VenvSessionManager instance.
        """
        if self._venv_mgr is None:
            self._venv_mgr = VenvSessionManager()
        return self._venv_mgr

    def _get_galaxy_proxy_cfg_lock(self) -> asyncio.Lock:
        """Return the lock guarding temporary ``ANSIBLE_CONFIG`` overrides.

        The local daemon runs Engine and Galaxy Proxy in the same process.
        When a scan provides session-scoped Galaxy credentials, Engine
        temporarily exposes that config via ``ANSIBLE_CONFIG`` so the in-process
        proxy can use it during venv acquisition. The lock serializes that
        process-wide override.

        Returns:
            Lock protecting process-wide Galaxy proxy config activation.
        """
        if self._galaxy_proxy_cfg_lock is None:
            self._galaxy_proxy_cfg_lock = asyncio.Lock()
        return self._galaxy_proxy_cfg_lock

    @contextlib.asynccontextmanager
    async def _activate_galaxy_proxy_config(self, galaxy_cfg_path: Path | None) -> AsyncIterator[None]:
        """Temporarily expose a session-scoped Galaxy config to the proxy.

        This only affects local daemon mode where Engine and Galaxy Proxy share
        a process. Pod deployments use the Gateway's proxy config sync instead.

        Args:
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` path, if any.

        Yields:
            None: Control while the temporary ``ANSIBLE_CONFIG`` override is active.
        """
        if galaxy_cfg_path is None:
            yield
            return

        async with self._get_galaxy_proxy_cfg_lock():
            previous = os.environ.get("ANSIBLE_CONFIG")
            os.environ["ANSIBLE_CONFIG"] = str(galaxy_cfg_path)
            try:
                yield
            finally:
                if previous is None:
                    os.environ.pop("ANSIBLE_CONFIG", None)
                else:
                    os.environ["ANSIBLE_CONFIG"] = previous

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
        galaxy_cfg_path: Path | None = None,
        rule_configs: list[object] | None = None,
        rule_configs_complete: bool = False,
        skip_validators: frozenset[str] = frozenset(),
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
        object | None,
    ]:
        """Core scan pipeline: engine → collection discovery → venv → validators.

        Reused by FixSession (as scan_fn for remediation).

        Every scan gets a session-scoped venv.  The flow is:

        1. **Project load** — if a warm session venv exists its
           ``site-packages`` is passed as ``dependency_dir`` so the
           loader can resolve pre-installed collections.
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
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` for Galaxy auth
                (ADR-045). In daemon mode this is temporarily exposed to the
                in-process Galaxy Proxy during venv acquisition.
            rule_configs: Per-rule overrides from ``ScanOptions`` (ADR-041).
                When provided, disabled rules are filtered and severity is
                overridden after validator fan-out.
            rule_configs_complete: When ``True`` the incoming ``rule_configs``
                represents the full catalog (Gateway path).  The Engine
                performs bidirectional audit and hard-fails on unknown **or**
                missing rule IDs.  When ``False`` (CLI path), unknown IDs
                produce a warning only.
            skip_validators: Validator names to exclude from fan-out
                (e.g. ``{"collection_health", "dep_audit"}``).  Allows
                request-scoped control over optional validators (ADR-051).

        Raises:
            Exception: Pipeline failures (including ``ValueError`` from
                bidirectional rule-catalog audit when complete). Failures are
                recorded as ``status=error`` metrics before re-raise.

        Returns:
            Tuple of (violations, ScanDiagnostics or None, resolved session_id,
            merged pipeline logs, hierarchy_payload Mapping or None,
            VenvSession or None, requirements file paths found,
            specified collection FQCNs, learned collection FQCNs,
            ContentGraph or None).
        """
        scan_t0 = time.monotonic()
        try:
            return await self._execute_scan_pipeline(
                temp_dir,
                files,
                scan_id,
                ansible_core_version=ansible_core_version,
                collection_specs=collection_specs,
                include_scandata=include_scandata,
                session_id=session_id,
                progress_callback=progress_callback,
                galaxy_cfg_path=galaxy_cfg_path,
                rule_configs=rule_configs,
                rule_configs_complete=rule_configs_complete,
                skip_validators=skip_validators,
            )
        except Exception:
            try:
                from apme_engine.observability import record_scan_diagnostics

                record_scan_diagnostics(
                    ScanDiagnostics(total_ms=(time.monotonic() - scan_t0) * 1000.0),
                    status="error",
                )
            except Exception:  # noqa: BLE001 — metrics must never mask scan failures
                logger.debug("Failed to record scan error metrics", exc_info=True)
            raise

    async def _execute_scan_pipeline(
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
        galaxy_cfg_path: Path | None = None,
        rule_configs: list[object] | None = None,
        rule_configs_complete: bool = False,
        skip_validators: frozenset[str] = frozenset(),
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
        object | None,
    ]:
        """Run the scan pipeline body (see ``_scan_pipeline``).

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
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` for Galaxy auth
                (ADR-045). In daemon mode this is temporarily exposed to the
                in-process Galaxy Proxy during venv acquisition.
            rule_configs: Per-rule overrides from ``ScanOptions`` (ADR-041).
                When provided, disabled rules are filtered and severity is
                overridden after validator fan-out.
            rule_configs_complete: When ``True`` the incoming ``rule_configs``
                represents the full catalog (Gateway path).  The Engine
                performs bidirectional audit and hard-fails on unknown **or**
                missing rule IDs.  When ``False`` (CLI path), unknown IDs
                produce a warning only.
            skip_validators: Validator names to exclude from fan-out
                (e.g. ``{"collection_health", "dep_audit"}``).  Allows
                request-scoped control over optional validators (ADR-051).

        Raises:
            RequiredValidatorDependencyError: If a required validator
                (native, opa, ansible) is not configured or its RPC fails.
            ValueError: If ``rule_configs_complete`` is ``True`` and either
                direction of the bidirectional audit fails (unknown IDs the
                Engine cannot execute, or known IDs absent from the config).

        Returns:
            Same tuple as ``_scan_pipeline``.
        """
        from apme_engine.validators.ansible._venv import DEFAULT_VERSION
        from apme_engine.venv_manager.session import _venv_site_packages

        scan_t0 = time.monotonic()
        collection_specs = list(collection_specs or [])

        core_version = ansible_core_version or DEFAULT_VERSION
        sid = session_id or uuid.uuid4().hex[:12]

        # Check for warm session venv so the loader can resolve pre-installed collections
        dependency_dir = ""
        warm = self._get_venv_manager().get(sid, core_version)
        if warm and warm.venv_root.is_dir():
            with contextlib.suppress(FileNotFoundError):
                dependency_dir = str(_venv_site_packages(warm.venv_root))
            if dependency_dir:
                logger.debug("Session(%s): warm venv, dependency_dir=%s", sid, dependency_dir)

        # 1. Project load (parse + build ContentGraph)
        ctx = contextvars.copy_context()
        context_obj = await asyncio.get_event_loop().run_in_executor(
            None,
            ctx.run,
            lambda: run_scan(
                str(temp_dir),
                str(temp_dir),
                include_scandata=include_scandata,
                dependency_dir=dependency_dir,
            ),
        )

        if not context_obj.hierarchy_payload:
            logger.warning("Scan: no hierarchy payload produced (req=%s)", scan_id)
            return [], ScanDiagnostics(), sid, [], None, None, [], set(), set(), None

        # 2. Collection discovery
        discovered, requirements_found = _discover_collection_specs(files)
        hierarchy_collections = context_obj.hierarchy_payload.get("collection_set", [])
        if not isinstance(hierarchy_collections, list):
            hierarchy_collections = []

        logger.info(
            "Collection discovery (req=%s): requirements=%s, hierarchy_fqcns=%s, request_specs=%s",
            scan_id,
            discovered,
            hierarchy_collections,
            collection_specs,
        )

        collection_specs = merge_collection_specs(
            collection_specs,
            discovered,
            hierarchy_collections,
        )
        logger.info("Collection specs merged (req=%s): %s", scan_id, collection_specs)

        # 3. Venv acquire (always — creates or incrementally installs)
        async with self._activate_galaxy_proxy_config(galaxy_cfg_path):
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
        content_graph: object | None = None
        if context_obj.scandata and hasattr(context_obj.scandata, "content_graph"):
            cg = context_obj.scandata.content_graph
            if cg is not None:
                content_graph = cg
                loop = asyncio.get_event_loop()
                content_graph_data = await loop.run_in_executor(
                    None,
                    lambda: json.dumps(cg.to_dict(slim=True), default=str).encode(),
                )
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
            graph_rule_opt_in=graph_rule_opt_in_from_rule_configs(rule_configs),
        )

        _pcb = progress_callback

        validator_targets: list[tuple[str, str]] = []
        missing_required: list[str] = []
        for name, env_var in VALIDATOR_ENV_VARS.items():
            if name in skip_validators:
                logger.debug("Skipping validator %s (request skip flag, req=%s)", name, scan_id)
                continue
            addr = os.environ.get(env_var)
            if not addr:
                if name in REQUIRED_VALIDATORS:
                    missing_required.append(f"{name} ({env_var})")
                continue
            validator_targets.append((name, addr))

        if missing_required:
            msg = (
                "Required validator(s) not configured: "
                f"{', '.join(missing_required)}. Set the corresponding *_GRPC_ADDRESS variables."
            )
            logger.error("%s (req=%s)", msg, scan_id)
            raise RequiredValidatorDependencyError(msg)

        task_names = [name for name, _addr in validator_targets]
        task_coros: list[Awaitable[_ValidatorResult]] = [
            _call_validator(addr, validate_request) for _name, addr in validator_targets
        ]

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
                    rule_ids = sorted({str(v.get("rule_id", "")) for v in result.violations if isinstance(v, dict)})
                    if _pcb:
                        count = len(result.violations)
                        _pcb(
                            "scan",
                            f"{name.title()}: {count} findings {rule_ids}",
                            validators_done / num_validators,
                            2,
                        )
                    logger.info(
                        "Fan-out: %s returned %d violations: %s (req=%s)",
                        name,
                        len(result.violations),
                        rule_ids,
                        scan_id,
                    )
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
                    if vname in REQUIRED_VALIDATORS:
                        msg = f"Required validator {vname} failed: {item}"
                        raise RequiredValidatorDependencyError(msg) from item
                    continue
                name, result = item
                if name in REQUIRED_VALIDATORS and result.error:
                    msg = f"Required validator {name} RPC failed: {result.error}"
                    logger.error("%s (req=%s)", msg, scan_id)
                    raise RequiredValidatorDependencyError(msg)
                counts[name] = len(result.violations)
                violations.extend(result.violations)
                if result.diagnostics:
                    validator_diagnostics.append(result.diagnostics)
                if result.logs:
                    validator_logs.append(list(result.logs))

            parts = " ".join(f"{n.title()}={counts.get(n, 0)}" for n in VALIDATOR_ENV_VARS)
            logger.info("Fan-out: done (%.0fms) %s Total=%d (req=%s)", fan_out_ms, parts, len(violations), scan_id)

        before_noqa = len(violations)
        violations = filter_noqa_violations(
            violations, content_graph if isinstance(content_graph, ContentGraph) else None
        )
        if len(violations) < before_noqa:
            logger.info(
                "Fan-out: dropped %d violation(s) via # noqa (req=%s)",
                before_noqa - len(violations),
                scan_id,
            )

        violations = _deduplicate_violations(_sort_violations(violations))
        if rule_configs:
            unknown, missing = _validate_rule_configs(rule_configs, complete=rule_configs_complete)
            if rule_configs_complete:
                errors: list[str] = []
                if unknown:
                    errors.append(f"unknown rule IDs: {unknown}")
                if missing:
                    errors.append(f"missing rule IDs (known to this engine but absent from config): {missing}")
                if errors:
                    raise ValueError(
                        f"Rule catalog mismatch (bidirectional audit): {'; '.join(errors)}. "
                        "The Gateway catalog is out of sync with this engine."
                    )
            elif unknown:
                logger.warning(
                    "rule_configs references unknown rule IDs (scan=%s): %s — ignoring",
                    scan_id,
                    unknown,
                )
            violations = _apply_rule_configs(violations, rule_configs)
        _attach_snippets(violations, files)

        total_ms = (time.monotonic() - scan_t0) * 1000
        ediag = context_obj.engine_diagnostics
        diag = ScanDiagnostics(
            engine_parse_ms=ediag.parse_ms,
            engine_annotate_ms=ediag.annotate_ms,
            engine_total_ms=ediag.total_ms,
            files_scanned=ediag.files_scanned,
            graph_nodes_built=ediag.graph_nodes_built,
            total_violations=len(violations),
            validators=validator_diagnostics,
            fan_out_ms=fan_out_ms,
            total_ms=total_ms,
        )
        specified_fqcns = {s.split(":")[0] for s in discovered}
        learned_fqcns = {str(c) for c in hierarchy_collections if isinstance(c, str)}

        logger.info("Scan: pipeline done (%.0fms, %d violations, req=%s)", total_ms, len(violations), scan_id)
        try:
            from apme_engine.observability import record_scan_diagnostics

            record_scan_diagnostics(diag, status="ok")
        except Exception:  # noqa: BLE001 — metrics must never break the scan pipeline
            logger.debug("Failed to record scan metrics", exc_info=True)
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
            content_graph,
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
                                operation_budget_seconds=session.operation_budget_s,
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
                    async for event in self._session_handle_approval(session, approved):
                        yield event

                elif oneof == "begin_remediate":
                    if session is None:
                        continue
                    session.touch()
                    async for event in self._session_begin_remediate(session):
                        yield event

                elif oneof == "ai_escalate":
                    if session is None:
                        continue
                    session.touch()
                    targets = [(t.path, frozenset(r for r in t.rule_ids if r)) for t in cmd.ai_escalate.targets]
                    async for event in self._session_handle_ai_escalate(session, targets):
                        yield event

                elif oneof == "extend":
                    if session:
                        session.touch()
                        yield SessionEvent(
                            created=SessionCreated(
                                session_id=session.session_id,
                                ttl_seconds=session.ttl_seconds,
                                operation_budget_seconds=session.operation_budget_s,
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
                    session.reanchor_lifetime_deadline()
                    yield SessionEvent(
                        created=SessionCreated(
                            session_id=session.session_id,
                            ttl_seconds=session.ttl_seconds,
                            operation_budget_seconds=session.operation_budget_s,
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
        except RequiredValidatorDependencyError as e:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
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
        from apme_engine.remediation.transforms import build_default_registry

        all_files = [File(path=p, content=c) for p, c in session.working_files.items()]

        fix_opts = session.fix_options
        scan_opts = session.scan_options

        ansible_core_version = ""
        collection_specs: list[str] = []
        max_passes = 5
        fix_session_id = ""
        galaxy_servers: Sequence[GalaxyServerDef] = ()
        if fix_opts:
            ansible_core_version = fix_opts.ansible_core_version
            collection_specs = list(fix_opts.collection_specs)
            fix_session_id = fix_opts.session_id
            if fix_opts.max_passes > 0:
                max_passes = fix_opts.max_passes
            galaxy_servers = fix_opts.galaxy_servers
        elif scan_opts:
            ansible_core_version = scan_opts.ansible_core_version
            collection_specs = list(scan_opts.collection_specs)
            fix_session_id = scan_opts.session_id
            galaxy_servers = scan_opts.galaxy_servers

        scan_rule_configs: list[object] = []
        scan_rule_configs_complete = False
        if scan_opts and scan_opts.rule_configs:
            scan_rule_configs = list(scan_opts.rule_configs)
            scan_rule_configs_complete = scan_opts.rule_configs_complete

        skip_validators: set[str] = set()
        if scan_opts:
            if scan_opts.skip_collection_health:
                skip_validators.add("collection_health")
            if scan_opts.skip_dep_audit:
                skip_validators.add("dep_audit")

        if galaxy_servers:
            session.galaxy_cfg_path = _write_session_galaxy_cfg(galaxy_servers)
            if session.galaxy_cfg_path:
                logger.info(
                    "Session %s: wrote Galaxy config with %d server(s) at %s",
                    session.session_id,
                    len(galaxy_servers),
                    session.galaxy_cfg_path,
                )

        if not all_files:
            session.status = 3  # COMPLETE
            yield SessionEvent(
                tier1_complete=Tier1Summary(
                    idempotency_ok=True,
                    report=FixReport(),
                ),
            )
            return

        self._begin_non_ai_operation_budget(session, violation_count=0)
        yield SessionEvent(
            created=SessionCreated(
                session_id=session.session_id,
                ttl_seconds=session.ttl_seconds,
                operation_budget_seconds=session.operation_budget_s,
            ),
        )

        # Phase 1: Format
        _fmt_start = ProgressUpdate(
            message=f"Formatting {len(all_files)} file(s)...",
            phase="format",
            level=2,  # INFO
        )
        session.record_progress(task_linked=is_task_linked_progress(_fmt_start.phase))
        self._stamp_progress_update(_fmt_start, session)
        session.progress_logs.append(_fmt_start)
        yield SessionEvent(progress=_fmt_start)
        format_result = await self._supervise_executor(
            session,
            self._format_files,
            list(all_files),
        )
        if isinstance(format_result, SessionEvent):
            yield format_result
            return
        format_diffs = format_result
        session.format_diffs = list(format_diffs)

        formatted_files: list[File] = list(all_files)
        format_map: dict[str, bytes] = {d.path: d.formatted for d in format_diffs}

        temp_dir_result = await self._supervise_executor(
            session,
            _write_chunked_fs,
            list(all_files),
        )
        if isinstance(temp_dir_result, SessionEvent):
            yield temp_dir_result
            return
        temp_dir = temp_dir_result
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
            session.record_progress(task_linked=is_task_linked_progress(_fmt_done.phase))
            self._stamp_progress_update(_fmt_done, session)
            session.progress_logs.append(_fmt_done)
            yield SessionEvent(progress=_fmt_done)

        if (deadline_event := self._check_deadline_event(session)) is not None:
            yield deadline_event
            return

        # Phase 2: Idempotency check
        idem_result = await self._supervise_executor(
            session,
            self._format_files,
            formatted_files,
        )
        if isinstance(idem_result, SessionEvent):
            yield idem_result
            return
        idem_diffs = idem_result
        session.idempotency_ok = len(idem_diffs) == 0
        if not session.idempotency_ok:
            _idem_warn = ProgressUpdate(
                message="Formatter is not idempotent on this input",
                phase="format",
                level=3,  # WARNING
            )
            session.record_progress(task_linked=is_task_linked_progress(_idem_warn.phase))
            self._stamp_progress_update(_idem_warn, session)
            session.progress_logs.append(_idem_warn)
            yield SessionEvent(progress=_idem_warn)

        if (deadline_event := self._check_deadline_event(session)) is not None:
            yield deadline_event
            return

        # Phase 3+4: Scan + Remediate via convergence loop
        _t1_start = ProgressUpdate(
            message="Running Tier 1 remediation...",
            phase="tier1",
            level=2,
        )
        session.record_progress(task_linked=is_task_linked_progress(_t1_start.phase))
        self._stamp_progress_update(_t1_start, session)
        session.progress_logs.append(_t1_start)
        yield SessionEvent(progress=_t1_start)

        loop = asyncio.get_event_loop()

        _HEARTBEAT_INTERVAL = 15
        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        def _progress_callback(
            phase: str,
            message: str,
            fraction: float = 0.0,
            level: int = 2,
            *,
            ai_completed: int = 0,
            ai_total: int = 0,
        ) -> None:
            update = ProgressUpdate(
                message=message,
                phase=phase,
                progress=fraction,
                level=level,
                ai_completed=ai_completed,
                ai_total=ai_total,
            )
            EngineServicer._stamp_progress_update(update, session)
            loop.call_soon_threadsafe(progress_queue.put_nowait, update)

        manifest_captured = False

        captured_graph: list[object | None] = [None]

        registry = build_default_registry()

        yaml_paths = [str(temp_dir / f.path) for f in formatted_files if f.path.endswith((".yml", ".yaml"))]

        async def _heartbeat() -> None:
            """Send periodic heartbeats while remediation is running."""
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                progress_queue.put_nowait(ProgressUpdate(message="Processing...", phase="heartbeat", level=1))

        async def async_scan_fn(file_paths: list[str]) -> list[ViolationDict]:
            nonlocal manifest_captured
            loop = asyncio.get_running_loop()
            rel_files = list(
                await asyncio.gather(
                    *[loop.run_in_executor(None, _file_proto_from_path, fp, temp_dir) for fp in file_paths]
                )
            )
            (
                violations,
                _,
                _,
                validator_logs,
                _hierarchy_payload,
                venv_sess,
                req_files,
                specified_fqcns,
                learned_fqcns,
                graph_obj,
            ) = await self._scan_pipeline(
                temp_dir,
                rel_files,
                scan_id,
                ansible_core_version=ansible_core_version,
                collection_specs=collection_specs,
                session_id=fix_session_id,
                progress_callback=_progress_callback,
                galaxy_cfg_path=session.galaxy_cfg_path,
                rule_configs=scan_rule_configs or None,
                rule_configs_complete=scan_rule_configs_complete,
                skip_validators=frozenset(skip_validators),
            )

            for batch in validator_logs:
                session.progress_logs.extend(batch)

            if graph_obj is not None:
                captured_graph[0] = graph_obj

            if venv_sess is not None and not session.venv_path:
                session.venv_path = str(venv_sess.venv_root)

            if not manifest_captured and venv_sess is not None:
                manifest_captured = True
                av, cols, pkgs, tree, reqs = await loop.run_in_executor(
                    None,
                    lambda: (
                        venv_sess.ansible_version,
                        _classify_collections(
                            list_installed_collections(venv_sess.venv_root),
                            specified_fqcns,
                            learned_fqcns,
                        ),
                        list_installed_packages(venv_sess.venv_root),
                        get_dependency_tree(venv_sess.venv_root),
                        req_files,
                    ),
                )
                session.ansible_core_version = av
                session.installed_collections = cols
                session.installed_packages = pkgs
                session.dependency_tree = tree
                session.requirements_files = reqs

            return violations

        async for event in self._session_graph_remediate(
            session=session,
            scan_id=scan_id,
            registry=registry,
            scan_fn=async_scan_fn,
            captured_graph=captured_graph,
            yaml_paths=yaml_paths,
            temp_dir=temp_dir,
            max_passes=max_passes,
            progress_queue=progress_queue,
            progress_callback=_progress_callback,
            _heartbeat=_heartbeat,
            format_content=format_content,
            format_diffs=format_diffs,
            rule_configs=scan_rule_configs or None,
        ):
            yield event

    @staticmethod
    def _stamp_progress_update(update: ProgressUpdate, session: SessionState) -> None:
        """Attach session-scoped deadline metadata to a progress event.

        Args:
            update: Progress event to stamp in place.
            session: Active session supplying generation and budget.
        """
        update.operation_generation = session.operation_generation
        if session.operation_budget_s > 0:
            update.budget_seconds = session.operation_budget_remaining()

    @staticmethod
    def _progress_matches_phase(update: ProgressUpdate, session: SessionState) -> bool:
        """Return whether a queued progress event belongs to the active phase.

        Args:
            update: Queued progress event.
            session: Active session with current operation_generation.

        Returns:
            True when the event should be applied to the active phase.
        """
        gen = update.operation_generation
        return gen == 0 or gen == session.operation_generation

    def _check_deadline_event(self, session: SessionState) -> SessionEvent | None:
        """Return a terminal error event when deadline enforcement fires.

        Args:
            session: Active session with operation budget set.

        Returns:
            SessionEvent with SessionError when exceeded, else None.
        """
        err = check_operation_deadline(
            operation_budget_s=session.operation_budget_s,
            operation_started_at=session.operation_started_at,
            last_progress_at=session.last_progress_at,
            now=time.monotonic(),
            max_lifetime_deadline_mono=session.max_lifetime_deadline_mono,
        )
        if err is None:
            return None
        return SessionEvent(error=SessionError(code=err.code, message=str(err)))

    async def _supervise_executor(  # type: ignore[explicit-any]
        self,
        session: SessionState,
        func: Callable[..., _ExecutorResult],
        *args: object,
    ) -> _ExecutorResult | SessionEvent:
        """Run blocking work in an executor with deadline/stall supervision (ADR-068).

        ``run_in_executor`` futures cannot be cancelled once the worker thread
        has started. On deadline fire this method returns immediately and
        abandons the in-flight thread so enforcement is not blocked until the
        worker finishes.

        Args:
            session: Active session with operation budget set.
            func: Sync callable to run in the default executor.
            *args: Positional arguments for ``func``.

        Returns:
            ``func``'s return value, or a terminal ``SessionEvent`` on deadline fire.
        """
        loop = asyncio.get_event_loop()
        exec_future = loop.run_in_executor(None, func, *args)
        while not exec_future.done():
            if (err_event := self._check_deadline_event(session)) is not None:
                # run_in_executor futures are not cancellable once running;
                # abandon the future so enforcement is not blocked.
                exec_future.cancel()
                exec_future.add_done_callback(
                    lambda fut: fut.exception() if not fut.cancelled() else None,
                )
                return err_event
            try:
                await asyncio.wait_for(asyncio.shield(exec_future), timeout=1.0)
            except TimeoutError:
                continue
        return exec_future.result()

    @staticmethod
    def _begin_non_ai_operation_budget(
        session: SessionState,
        *,
        violation_count: int = 0,
    ) -> None:
        """Start non-AI phase budget at session processing entry (ADR-068).

        Args:
            session: Session receiving the budget.
            violation_count: Violation count for tier1 margin estimate.
        """
        try:
            budget = estimate_non_ai_budget(violation_count=violation_count)
        except BudgetConfigError as exc:
            logger.error("Invalid operation budget configuration: %s", exc)
            budget = FALLBACK_NON_AI_OPERATION_BUDGET
        session.begin_operation_phase(budget)

    @staticmethod
    def _begin_ai_operation_budget(
        session: SessionState,
        *,
        violations: list[ViolationDict] | None = None,
        registry: object | None = None,
        ai_violations: list[ViolationDict] | None = None,
        max_ai_attempts: int = 2,
        concurrency: int | None = None,
    ) -> None:
        """Re-anchor budget at the AI gate with AI-only estimate (ADR-068).

        Args:
            session: Session receiving the budget.
            violations: Full violation list for tier-2 partitioning.
            registry: Transform registry for tier partitioning.
            ai_violations: Pre-partitioned tier-2 violations (skips partition).
            max_ai_attempts: AI resubmission cap from graph engine.
            concurrency: Parallel AI calls (default env).

        Raises:
            BudgetConfigError: When AI budget inputs are invalid.
        """
        if ai_violations is not None:
            ai_nodes = count_ai_nodes(ai_violations)
        elif violations is not None and registry is not None:
            from apme_engine.remediation.partition import partition_violations  # noqa: PLC0415

            _, tier2, _ = partition_violations(violations, registry)  # type: ignore[arg-type]
            ai_nodes = count_ai_nodes(tier2)
        else:
            ai_nodes = 0
        try:
            budget = estimate_ai_budget(
                ai_node_count=ai_nodes,
                max_ai_attempts=max_ai_attempts,
                concurrency=concurrency,
            )
        except BudgetConfigError as exc:
            logger.error("Invalid AI budget configuration: %s", exc)
            raise
        session.begin_operation_phase(budget)

    async def _cancel_remediate_task(
        self,
        remediate_task: asyncio.Task[object],
    ) -> None:
        """Cancel remediate work and await cleanup (ADR-068).

        Args:
            remediate_task: Running graph remediation task.
        """
        remediate_task.cancel()
        try:
            await remediate_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("remediate_task cleanup failed after deadline enforcement")

    async def _drain_remediate_task(
        self,
        session: SessionState,
        remediate_task: asyncio.Task[object],
        progress_queue: asyncio.Queue[ProgressUpdate | None],
    ) -> AsyncIterator[SessionEvent]:
        """Yield progress until remediate completes or deadline/stall fires.

        Args:
            session: Active session with operation budget set.
            remediate_task: Running ``GraphRemediationEngine.remediate`` task.
            progress_queue: Progress updates from engine and heartbeat.

        Yields:
            SessionEvent: Progress and terminal error events.
        """
        deadline_failed = False
        while not remediate_task.done():
            err_event = self._check_deadline_event(session)
            if err_event is not None:
                deadline_failed = True
                yield err_event
                break
            try:
                update = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if update is not None:
                if not self._progress_matches_phase(update, session):
                    continue
                session.record_progress(task_linked=is_task_linked_progress(update.phase))
                self._stamp_progress_update(update, session)
                session.progress_logs.append(update)
                yield SessionEvent(progress=update)

        if deadline_failed:
            await self._cancel_remediate_task(remediate_task)
            return

        if remediate_task.cancelled():
            return

        if remediate_task.done() and (task_exc := remediate_task.exception()) is not None:
            code = "invalid_budget_config" if isinstance(task_exc, BudgetConfigError) else "remediation_failed"
            yield SessionEvent(error=SessionError(code=code, message=str(task_exc)))
            return

        while not progress_queue.empty():
            update = progress_queue.get_nowait()
            if update is not None:
                if not self._progress_matches_phase(update, session):
                    continue
                session.record_progress(task_linked=is_task_linked_progress(update.phase))
                self._stamp_progress_update(update, session)
                session.progress_logs.append(update)
                yield SessionEvent(progress=update)

    async def _session_graph_remediate(  # type: ignore[explicit-any]  # noqa: PLR0913
        self,
        *,
        session: SessionState,
        scan_id: str,
        registry: object,
        scan_fn: Callable[[list[str]], Awaitable[list[ViolationDict]]],
        captured_graph: list[object | None],
        yaml_paths: list[str],
        temp_dir: Path,
        max_passes: int,
        progress_queue: asyncio.Queue[ProgressUpdate | None],
        progress_callback: Callable[[str, str, float, int], None],
        _heartbeat: Callable[[], Awaitable[None]],
        format_content: Callable[..., object],
        format_diffs: Sequence[object],
        rule_configs: list[object] | None = None,
    ) -> AsyncIterator[SessionEvent]:
        """Graph-engine remediation — in-memory convergence, graph-authoritative.

        Convergence sends dirty nodes to all validators via gRPC.
        Native receives the full graph with a ``dirty_node_ids`` hint;
        other validators receive node-scoped payloads.  No file I/O
        occurs during convergence.  The ContentGraph is
        authoritative for remaining violations — no final re-scan is
        needed.  Approved changes are spliced to disk.

        Args:
            session: Active session state (mutated in place).
            scan_id: Request identifier for logging.
            registry: Transform registry with node transforms.
            scan_fn: Async scan function that calls ``_scan_pipeline``.
            captured_graph: Single-element list holding the captured
                ``ContentGraph`` from the first ``scan_fn`` call.
            yaml_paths: Absolute YAML file paths under ``temp_dir``.
            temp_dir: Working directory with formatted files.
            max_passes: Maximum convergence passes.
            progress_queue: Queue for streaming progress events.
            progress_callback: ``(phase, msg, frac, level)`` callback.
            _heartbeat: Coroutine factory for periodic heartbeats.
            format_content: Formatter function for post-remediation pass.
            format_diffs: Accumulated format diffs from earlier step.
            rule_configs: Per-rule overrides for opt-in audit GraphRules.

        Yields:
            SessionEvent: Progress, Tier1Summary, and result events.
        """
        from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
        from apme_engine.graph.content_graph import ContentGraph, EdgeType, NodeType
        from apme_engine.graph.scanner import (
            graph_rule_opt_in_from_rule_configs,
            load_graph_rules,
            native_rules_dir,
        )
        from apme_engine.remediation.graph_engine import (
            GraphRemediationEngine,
            splice_modifications,
        )
        from apme_engine.remediation.partition import (
            add_classification_to_violations,
        )

        # 1. Initial full-pipeline scan to get violations + graph
        initial_violations = await scan_fn(yaml_paths)

        dep_health_sources = {"collection_health", "dep_audit"}
        dep_health_violations = [v for v in initial_violations if str(v.get("source", "")) in dep_health_sources]
        project_violations = [v for v in initial_violations if str(v.get("source", "")) not in dep_health_sources]
        initial_violations = project_violations

        graph = captured_graph[0]
        if not isinstance(graph, ContentGraph):
            logger.warning(
                "No ContentGraph from scan pipeline; falling back to empty graph (scan_id=%s)",
                scan_id,
            )
            graph = ContentGraph()

        loop = asyncio.get_running_loop()
        originals = await loop.run_in_executor(None, _load_yaml_originals, yaml_paths, temp_dir)

        # 2. Convergence: all validators on dirty nodes via gRPC
        opt_in_rules = graph_rule_opt_in_from_rule_configs(rule_configs)
        rules, missing_opt_in = load_graph_rules(
            rules_dir=native_rules_dir(),
            opt_in_rule_ids=opt_in_rules,
        )
        if missing_opt_in:
            logger.warning(
                "Remediation: requested opt-in graph rules failed to load: %s",
                ", ".join(missing_opt_in),
            )
            _opt_in_warn = ProgressUpdate(
                message=f"Requested audit rule(s) not loaded: {', '.join(missing_opt_in)}",
                phase="graph-tier1",
                level=3,  # WARNING
            )
            session.record_progress(task_linked=is_task_linked_progress(_opt_in_warn.phase))
            self._stamp_progress_update(_opt_in_warn, session)
            session.progress_logs.append(_opt_in_warn)
            yield SessionEvent(progress=_opt_in_warn)

        async def _rescan_bridge(
            g: ContentGraph,
            dirty_ids: frozenset[str],
        ) -> list[ViolationDict]:
            """Rescan dirty nodes via gRPC to all configured validators.

            Native receives the full (slim) graph with ``dirty_node_ids``
            so rules can traverse context beyond the dirty set.  OPA,
            Ansible, and Gitleaks receive node-scoped payloads.  All
            return violations with ``path`` set to ``node_id``.

            Args:
                g: ContentGraph (may have been mutated by transforms).
                dirty_ids: Node IDs that changed since the last pass.

            Returns:
                Merged violation list from all sources.

            Raises:
                RequiredValidatorDependencyError: If a required validator
                    (native, opa, ansible) is not configured or its RPC fails.
            """
            all_violations: list[ViolationDict] = []

            dirty_nodes = [node for nid in sorted(dirty_ids) if (node := g.get_node(nid)) is not None]
            if not dirty_nodes:
                return all_violations

            ext_coros: list[Awaitable[_ValidatorResult]] = []
            ext_names: list[str] = []

            # Native: full graph with dirty_node_ids hint (rules traverse full context)
            native_addr = os.environ.get("NATIVE_GRPC_ADDRESS")
            if not native_addr:
                raise RequiredValidatorDependencyError(
                    "Required validator native not configured for rescan (NATIVE_GRPC_ADDRESS unset)"
                )
            if native_addr:
                graph_data = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: json.dumps(g.to_dict(slim=True), default=str).encode(),
                )
                ext_coros.append(
                    _call_validator(
                        native_addr,
                        ValidateRequest(
                            request_id=f"{scan_id}-rescan",
                            content_graph_data=graph_data,
                            dirty_node_ids=sorted(dirty_ids),
                            graph_rule_opt_in=opt_in_rules,
                        ),
                    )
                )
                ext_names.append("native")

            # Gitleaks: serialize dirty nodes as content_graph_data
            gl_addr = os.environ.get("GITLEAKS_GRPC_ADDRESS")
            if gl_addr:
                gl_nodes = [(n.node_id, n.yaml_lines) for n in dirty_nodes if n.yaml_lines]
                if gl_nodes:
                    gl_graph_data = json.dumps(
                        {
                            "version": 1,
                            "nodes": [{"id": nid, "data": {"yaml_lines": yl}} for nid, yl in gl_nodes],
                            "edges": [],
                        }
                    ).encode()
                    ext_coros.append(
                        _call_validator(
                            gl_addr,
                            ValidateRequest(
                                request_id=f"{scan_id}-rescan",
                                content_graph_data=gl_graph_data,
                            ),
                        )
                    )
                    ext_names.append("gitleaks")

            # OPA: mini hierarchy from dirty nodes + their parent plays
            opa_addr = os.environ.get("OPA_GRPC_ADDRESS")
            if not opa_addr:
                raise RequiredValidatorDependencyError(
                    "Required validator opa not configured for rescan (OPA_GRPC_ADDRESS unset)"
                )
            if opa_addr:
                opa_node_ids: set[str] = {n.node_id for n in dirty_nodes}
                for n in dirty_nodes:
                    for src_id, _attrs in g.edges_to(n.node_id, EdgeType.CONTAINS):
                        parent = g.get_node(src_id)
                        if parent is not None and parent.node_type == NodeType.PLAY:
                            opa_node_ids.add(src_id)
                opa_nodes = [node for nid in sorted(opa_node_ids) if (node := g.get_node(nid)) is not None]
                opa_dicts = [d for n in opa_nodes if (d := content_node_to_opa_dict(n, graph=g))]
                if opa_dicts:
                    opa_payload = {
                        "scan_id": f"{scan_id}-rescan",
                        "hierarchy": [
                            {
                                "root_key": "rescan",
                                "root_type": "rescan",
                                "root_path": "",
                                "nodes": opa_dicts,
                            }
                        ],
                        "collection_set": [],
                        "metadata": {},
                    }
                    ext_coros.append(
                        _call_validator(
                            opa_addr,
                            ValidateRequest(
                                request_id=f"{scan_id}-rescan",
                                hierarchy_payload=json.dumps(opa_payload, default=str).encode(),
                            ),
                        )
                    )
                    ext_names.append("opa")

            # Ansible task checks: hierarchy with dirty task nodes (L057 skipped — no files)
            ans_addr = os.environ.get("ANSIBLE_GRPC_ADDRESS")
            if not ans_addr:
                raise RequiredValidatorDependencyError(
                    "Required validator ansible not configured for rescan (ANSIBLE_GRPC_ADDRESS unset)"
                )
            if ans_addr and session.venv_path:
                task_dicts = [
                    d
                    for n in dirty_nodes
                    if (d := content_node_to_opa_dict(n, graph=g)) and d.get("type") == "taskcall"
                ]
                if task_dicts:
                    ans_payload = {
                        "scan_id": f"{scan_id}-rescan",
                        "hierarchy": [
                            {
                                "root_key": "rescan",
                                "root_type": "rescan",
                                "root_path": "",
                                "nodes": task_dicts,
                            }
                        ],
                        "collection_set": [],
                        "metadata": {},
                    }
                    ans_opts = session.fix_options or session.scan_options
                    ext_coros.append(
                        _call_validator(
                            ans_addr,
                            ValidateRequest(
                                request_id=f"{scan_id}-rescan",
                                hierarchy_payload=json.dumps(ans_payload, default=str).encode(),
                                venv_path=session.venv_path,
                                session_id=ans_opts.session_id if ans_opts else "",
                                ansible_core_version=(ans_opts.ansible_core_version if ans_opts else ""),
                            ),
                        )
                    )
                    ext_names.append("ansible")

            if ext_coros:
                results = await asyncio.gather(*ext_coros, return_exceptions=True)
                for name, result in zip(ext_names, results, strict=True):
                    if isinstance(result, BaseException):
                        if name in REQUIRED_VALIDATORS:
                            msg = f"Required validator {name} failed during rescan: {result}"
                            raise RequiredValidatorDependencyError(msg) from result
                        logger.warning("Rescan: %s failed: %s", name, result)
                        continue
                    if name in REQUIRED_VALIDATORS and result.error:
                        msg = f"Required validator {name} RPC failed during rescan: {result.error}"
                        raise RequiredValidatorDependencyError(msg)
                    all_violations.extend(result.violations)

            return filter_noqa_violations(all_violations, g)

        ai_provider = self._resolve_ai_provider(session.fix_options)
        assess_pause = bool(session.fix_options and session.fix_options.assess_pause)
        # interactive controls Gate 1 after BeginRemediate; assess_pause only
        # defers Tier 1 splice / AI until then (ADR-064 — independent flags).
        interactive = bool(session.fix_options and session.fix_options.interactive)
        defer_tier1 = interactive or assess_pause
        # Option C: when interactive + AI, Gate 1 is Tier 1 only; AI runs after approval.
        # Assess-pause also defers AI until after BeginRemediate.
        skip_ai = defer_tier1 and bool(session.fix_options and session.fix_options.enable_ai)

        ai_concurrency = parse_ai_concurrency()

        graph_engine = GraphRemediationEngine(
            registry=registry,  # type: ignore[arg-type]
            graph=graph,
            rules=rules,
            max_passes=max_passes,
            max_ai_concurrency=ai_concurrency,
            progress_callback=progress_callback,
            rescan_fn=_rescan_bridge,
            ai_provider=ai_provider,  # type: ignore[arg-type]
            ai_phase_start_cb=None,
        )
        if ai_provider and not skip_ai:

            def _on_ai_phase_start(tier2_violations: list[ViolationDict]) -> None:
                """Re-anchor budget at AI gate with AI-only estimate (ADR-068).

                Args:
                    tier2_violations: Tier-2 violations entering the AI phase.
                """
                self._begin_ai_operation_budget(
                    session,
                    ai_violations=tier2_violations,
                    max_ai_attempts=graph_engine._max_ai_attempts,  # noqa: SLF001
                    concurrency=ai_concurrency,
                )

            graph_engine._ai_phase_start_cb = _on_ai_phase_start  # noqa: SLF001
        session.graph_engine = graph_engine

        hb_task: asyncio.Task[None] = asyncio.create_task(_heartbeat())  # type: ignore[arg-type]
        remediate_task = asyncio.create_task(
            graph_engine.remediate(
                initial_violations,
                interactive=defer_tier1,
                skip_ai=skip_ai,
            ),
        )

        try:
            async for event in self._drain_remediate_task(session, remediate_task, progress_queue):
                yield event
                if event.WhichOneof("event") == "error":
                    return

            if remediate_task.cancelled():
                return
            graph_report = remediate_task.result()
        finally:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task
            if not remediate_task.done():
                remediate_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await remediate_task

        # Persist graph + originals on session for approval gate
        session.content_graph = graph
        session.graph_originals = originals

        assess_pause = bool(session.fix_options and session.fix_options.assess_pause)
        interactive = bool(session.fix_options and session.fix_options.interactive)
        defer_tier1 = interactive or assess_pause
        tier1_node_proposals = list(graph_report.tier1_proposals) if defer_tier1 else []

        # 3. Splice approved modifications and write patched files.
        # Interactive / assess-pause: defer splice until ApprovalRequest or
        # BeginRemediate auto-apply (no disk writes yet).
        patches = [] if defer_tier1 and tier1_node_proposals else splice_modifications(graph, originals)

        for patch in patches:
            fmt_result = format_content(patch.patched, filename=Path(patch.path).name)
            if getattr(fmt_result, "changed", False):
                patch.patched = getattr(fmt_result, "formatted", patch.patched)

        for patch in patches:
            patch.diff = "".join(
                difflib.unified_diff(
                    patch.original.splitlines(keepends=True),
                    patch.patched.splitlines(keepends=True),
                    fromfile=f"a/{Path(patch.path).name}",
                    tofile=f"b/{Path(patch.path).name}",
                )
            )

        for patch in patches:
            patch_abs = Path(patch.path)
            if not patch_abs.is_absolute():
                patch_abs = temp_dir / patch_abs
            patch_abs.write_text(patch.patched, encoding="utf-8")

        # 4. Remaining violations — sourced from the graph (authoritative).
        # Copy before enrichment so classification metadata does not mutate
        # the graph-owned NodeState snapshot objects.
        remaining = [dict(v) for v in graph_report.remaining_violations]
        remaining.extend(dep_health_violations)
        add_classification_to_violations(remaining)
        session.dep_health_violations = [dict(v) for v in remaining if str(v.get("source", "")) in dep_health_sources]

        from apme_engine.remediation.partition import count_by_remediation_class

        rem_counts = count_by_remediation_class(remaining)

        # 5. Build Tier 1 summary
        tier1_patches: list[FilePatch] = []
        for patch in patches:
            rel_path = _working_files_key(temp_dir, patch.path)
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
        session.remaining_ai = list(remaining)
        session.remaining_manual = []

        # 6. Enrich violations with node YAML from the graph progression.
        _enrich_violations_from_graph(remaining, graph, fixed=False)
        remaining_protos = [violation_dict_to_proto(v) for v in remaining]
        for fv in graph_report.fixed_violations:
            fv["remediation_class"] = RemediationClass.AUTO_FIXABLE
        _enrich_violations_from_graph(graph_report.fixed_violations, graph, fixed=True)
        fixed_protos = [violation_dict_to_proto(v) for v in graph_report.fixed_violations]
        session.report = FixReport(
            passes=graph_report.passes,
            fixed=graph_report.fixed,
            remaining_ai=rem_counts.get("ai-candidate", 0),
            remaining_manual=rem_counts.get("manual-review", 0),
            oscillation_detected=graph_report.oscillation_detected,
            remaining_violations=remaining_protos,
            fixed_violations=fixed_protos,
        )

        # Deferred Tier 1: transforms are staged — not applied yet.
        # Saying "fixed" / "nodes modified" here misleads after a later partial approval.
        if defer_tier1 and tier1_node_proposals:
            _t1_msg = (
                f"Graph Tier 1 ready for review: {graph_report.passes} pass(es), "
                f"{len(tier1_node_proposals)} proposed fix(es) on "
                f"{graph_report.nodes_modified} node(s) (not applied until approved)"
            )
        else:
            _t1_msg = (
                f"Graph Tier 1 converged: {graph_report.passes} pass(es), "
                f"{graph_report.fixed} fixed, {graph_report.nodes_modified} nodes modified"
            )
        _t1_done = ProgressUpdate(message=_t1_msg, phase="graph-tier1", level=2)
        session.progress_logs.append(_t1_done)
        yield SessionEvent(progress=_t1_done)

        # Interactive / assess-pause: report-only Tier1Summary (patches deferred).
        yield SessionEvent(
            tier1_complete=Tier1Summary(
                applied_patches=tier1_patches,
                format_diffs=list(format_diffs),
                idempotency_ok=session.idempotency_ok,
                report=session.report,
            ),
        )

        # Stage Tier 1 proposals whenever splice was deferred (Gate 1 or
        # assess→auto-apply). Only interactive sets awaiting_tier1_gate.
        if defer_tier1 and tier1_node_proposals:
            t1_proposals = self._build_tier1_proposals(tier1_node_proposals)
            for p in t1_proposals:
                session.proposals[p.id] = p
            session.tier1_proposals = list(tier1_node_proposals)
            session.current_tier = 1
            if interactive:
                session.awaiting_tier1_gate = True

        # ADR-064: assess pause — emit findings, wait for BeginRemediate.
        if assess_pause:
            _dep_sources = frozenset({"collection_health", "dep_audit"})
            finding_protos = [
                v for v in list(remaining_protos) + list(fixed_protos) if (v.source or "") not in _dep_sources
            ]
            session.awaiting_assess = True
            session.status = 1  # AWAITING_APPROVAL (non-terminal)
            session.assess_findings = list(finding_protos)
            yield SessionEvent(
                findings=FindingsReady(
                    violations=finding_protos,
                    status=session.status,
                    ttl_seconds=session.ttl_seconds,
                ),
            )
            return

        # Gate 1: interactive Tier 1 proposals (Option C).
        if interactive and tier1_node_proposals:
            session.status = 1  # AWAITING_APPROVAL
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=list(session.proposals.values()),
                    tier=1,
                    status=session.status,
                ),
            )
            return

        # Interactive + AI but no Tier 1 proposals: skip Gate 1, triage then AI.
        if interactive and session.fix_options and session.fix_options.enable_ai:
            async for event in self._session_enter_ai_phase(session):
                yield event
            return

        # Yield AI proposals for human approval, or complete immediately.
        # Build "declined" entries for AI-candidate violations the AI couldn't fix
        # so the user sees them in the review panel.
        proposed_proposals = self._build_graph_proposals(graph_report.ai_proposals) if graph_report.ai_proposals else []
        proposed_rule_files: set[tuple[str, str]] = set()
        for p in proposed_proposals:
            for raw_rid in p.rule_id.split(","):
                clean_rid = raw_rid.strip()
                if clean_rid:
                    proposed_rule_files.add((clean_rid, p.file))

        declined_proposals = self._build_declined_proposals(
            remaining,
            proposed_rule_files,
            start_idx=len(proposed_proposals),
        )
        all_proposals = proposed_proposals + declined_proposals

        if proposed_proposals:
            for p in proposed_proposals:
                session.proposals[p.id] = p
            session.review_declined_proposals = {p.id: p for p in declined_proposals}
            session.ai_proposals = list(graph_report.ai_proposals) if graph_report.ai_proposals else []
            session.current_tier = 2
            session.status = 1  # AWAITING_APPROVAL

            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=all_proposals,
                    tier=session.current_tier,
                    status=session.status,
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
        except ValueError:
            logger.warning("Failed to create AbbenayProvider — invalid APME_ABBENAY_ADDR %r", addr)
            return None

        logger.info("AI provider ready: %s model=%s", addr, model)
        return provider

    @staticmethod
    def _build_tier1_proposals(
        tier1_node_proposals: Sequence[object],
    ) -> list[Proposal]:
        """Convert ``Tier1NodeProposal`` objects to proto ``Proposal``.

        Args:
            tier1_node_proposals: ``Tier1NodeProposal`` objects from the graph engine.

        Returns:
            List of Proposal protos with ``source="deterministic"``, ``tier=1``.
        """
        from apme_engine.remediation.graph_engine import Tier1NodeProposal  # noqa: PLC0415

        proposals: list[Proposal] = []
        for idx, item in enumerate(tier1_node_proposals):
            tnp: Tier1NodeProposal = item  # type: ignore[assignment]
            rule_id = ",".join(tnp.rule_ids) if tnp.rule_ids else "deterministic"

            diff_hunk = "".join(
                difflib.unified_diff(
                    tnp.before_yaml.splitlines(keepends=True),
                    tnp.after_yaml.splitlines(keepends=True),
                    fromfile=f"a/{tnp.file_path}",
                    tofile=f"b/{tnp.file_path} (Tier 1)",
                )
            )

            summaries = getattr(tnp, "violation_summaries", None) or []
            explanation = "\n".join(str(s) for s in summaries if s) if summaries else f"Deterministic fix for {rule_id}"
            proposals.append(
                Proposal(
                    id=f"t1-{idx:04d}",
                    file=tnp.file_path,
                    rule_id=rule_id,
                    line_start=tnp.line_start,
                    line_end=tnp.line_end,
                    before_text=tnp.before_yaml,
                    after_text=tnp.after_yaml,
                    diff_hunk=diff_hunk,
                    confidence=1.0,
                    explanation=explanation,
                    tier=1,
                    status="proposed",
                    source="deterministic",
                    path=tnp.node_id,
                    node_type=getattr(tnp, "node_type", "") or "",
                )
            )
        return proposals

    @staticmethod
    def _build_graph_proposals(
        ai_node_proposals: Sequence[object],
    ) -> list[Proposal]:
        """Convert graph-based ``AINodeProposal`` objects to proto ``Proposal``.

        Args:
            ai_node_proposals: ``AINodeProposal`` objects from the graph engine.

        Returns:
            List of Proposal protos with ``status="proposed"``.
        """
        from apme_engine.remediation.graph_engine import AINodeProposal  # noqa: PLC0415

        proposals: list[Proposal] = []
        for idx, item in enumerate(ai_node_proposals):
            anp: AINodeProposal = item  # type: ignore[assignment]
            rule_id = ",".join(anp.rule_ids) if anp.rule_ids else "ai-fix"

            diff_hunk = "".join(
                difflib.unified_diff(
                    anp.before_yaml.splitlines(keepends=True),
                    anp.after_yaml.splitlines(keepends=True),
                    fromfile=f"a/{anp.file_path}",
                    tofile=f"b/{anp.file_path} (AI proposed)",
                )
            )

            proposals.append(
                Proposal(
                    id=f"ai-{idx:04d}",
                    file=anp.file_path,
                    rule_id=rule_id,
                    line_start=anp.line_start,
                    line_end=anp.line_end,
                    before_text=anp.before_yaml,
                    after_text=anp.after_yaml,
                    diff_hunk=diff_hunk,
                    confidence=anp.confidence,
                    explanation=anp.explanation,
                    tier=2,
                    status="proposed",
                    source="ai",
                    path=anp.node_id,
                    node_type=getattr(anp, "node_type", "") or "",
                )
            )
        return proposals

    @staticmethod
    def _consolidate_gate2_proposals(
        ai_proposals: list[Proposal],
        tier1_proposals: list[Proposal],
    ) -> list[Proposal]:
        """Return one selectable Gate 2 proposal per graph node.

        AI and post-AI Tier 1 cleanup can target the same node. Approval applies
        to the whole node, so duplicate rows would let conflicting decisions
        override each other in ``_apply_graph_approvals``.

        Args:
            ai_proposals: Gate 2 AI proposals from ``_build_graph_proposals``.
            tier1_proposals: Gate 2 Tier 1 proposals (``g2-t1-*`` ids).

        Returns:
            Ordered proposals with at most one row per ``Proposal.path``.
        """
        by_node: dict[str, Proposal] = {}
        order: list[str] = []
        for proposal in ai_proposals:
            key = (proposal.path or "").strip() or proposal.id
            if key not in by_node:
                order.append(key)
            by_node[key] = proposal
        for tier1 in tier1_proposals:
            key = (tier1.path or "").strip() or tier1.id
            if key in by_node:
                existing = by_node[key]
                merged_rules = {r.strip() for r in existing.rule_id.split(",") if r.strip()}
                merged_rules.update(r.strip() for r in tier1.rule_id.split(",") if r.strip())
                if merged_rules:
                    existing.rule_id = ",".join(sorted(merged_rules))
                existing.after_text = tier1.after_text
                existing.diff_hunk = tier1.diff_hunk
                if tier1.explanation:
                    existing.explanation = (
                        f"{existing.explanation}\n{tier1.explanation}".strip()
                        if existing.explanation
                        else tier1.explanation
                    )
            else:
                if key not in by_node:
                    order.append(key)
                by_node[key] = tier1
        return [by_node[key] for key in order]

    async def _session_begin_remediate(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Leave ADR-064 assess pause and enter Gate 1 (or AI / COMPLETE).

        Args:
            session: Session currently in assess pause.

        Yields:
            SessionEvent: ProposalsReady (Gate 1), AI gate events, or SessionResult.
        """
        if not session.awaiting_assess:
            logger.warning(
                "BeginRemediate ignored — session %s not in assess pause",
                session.session_id,
            )
            return

        session.awaiting_assess = False
        session.touch()

        if session.awaiting_tier1_gate and session.proposals:
            session.status = 1  # AWAITING_APPROVAL
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=list(session.proposals.values()),
                    tier=1,
                    status=session.status,
                ),
            )
            return

        # assess_pause + interactive=false: auto-apply deferred Tier 1, then AI/COMPLETE.
        t1_ids = {pid for pid in session.proposals if pid.startswith("t1-")}
        if t1_ids:
            session.awaiting_tier1_gate = True
            async for event in self._session_handle_approval(session, t1_ids):
                yield event
            return

        if session.fix_options and session.fix_options.enable_ai:
            async for event in self._session_enter_ai_phase(session):
                yield event
            return

        session.status = 3  # COMPLETE
        async for event in self._session_build_result(session):
            yield event

    async def _session_handle_approval(
        self,
        session: SessionState,
        approved_ids: set[str],
    ) -> AsyncIterator[SessionEvent]:
        """Apply approvals and either complete or advance to Gate 2 (AI).

        Args:
            session: Active session.
            approved_ids: Proposal IDs the user accepted.

        Yields:
            SessionEvent: ApprovalAck, optional Gate 2 ProposalsReady, and SessionResult.
        """
        was_tier1_gate = session.awaiting_tier1_gate
        run_ai_gate = was_tier1_gate and bool(session.fix_options and session.fix_options.enable_ai)
        applied, temp_patches = self._session_apply_approved(session, approved_ids, finalize=not run_ai_gate)
        if session.temp_dir is not None and temp_patches:
            await asyncio.get_event_loop().run_in_executor(
                None,
                _write_patches_to_temp_dir,
                session.temp_dir,
                temp_patches,
            )
        if was_tier1_gate:
            files_touched = len(temp_patches) if temp_patches else 0
            report_fixed = session.report.fixed if session.report else applied
            _apply_msg = ProgressUpdate(
                message=(
                    f"Applied {applied} approved Tier 1 proposal(s) "
                    f"({report_fixed} violation(s) fixed, {files_touched} file(s) written); "
                    "declined proposals reverted"
                ),
                phase="graph-tier1",
                level=2,
            )
            session.progress_logs.append(_apply_msg)
            yield SessionEvent(progress=_apply_msg)
        yield SessionEvent(
            approval_ack=ApprovalAck(
                applied_count=applied,
                status=session.status,
                ttl_seconds=session.ttl_seconds,
            ),
        )

        if run_ai_gate:
            async for event in self._session_enter_ai_phase(session):
                yield event
            return

        if session.status == 3:  # COMPLETE
            async for event in self._session_build_result(session):
                yield event

    async def _session_enter_ai_phase(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Pause for AI escalation triage, or skip when no candidates remain.

        Emits ``AiTriageReady`` and returns when there are AI-candidate findings.
        Empty candidate set skips triage and AI (COMPLETE).

        Args:
            session: Session after Tier 1 apply (graph spliced).

        Yields:
            SessionEvent: AiTriageReady, or SessionResult when nothing to escalate.
        """
        candidates = _collect_ai_triage_candidates(session)
        if not candidates:
            session.status = 3  # COMPLETE
            async for event in self._session_build_result(session):
                yield event
            return

        protos = [violation_dict_to_proto(v) for v in candidates]
        session.ai_triage_candidates = list(protos)
        session.awaiting_ai_triage = True
        session.ai_escalate_targets = None
        session.status = 4  # AWAITING_AI_TRIAGE
        session.touch()
        yield SessionEvent(
            ai_triage=AiTriageReady(
                candidates=protos,
                status=session.status,
                ttl_seconds=session.ttl_seconds,
            ),
        )

    async def _session_handle_ai_escalate(
        self,
        session: SessionState,
        targets: list[tuple[str, frozenset[str]]],
    ) -> AsyncIterator[SessionEvent]:
        """Leave AI escalation triage and run Gate 2 on the allow-list.

        Args:
            session: Session in ``awaiting_ai_triage``.
            targets: ``(path, rule_ids)`` allow-list; empty list skips AI.

        Yields:
            SessionEvent: Gate 2 ProposalsReady or SessionResult.
        """
        if not session.awaiting_ai_triage:
            logger.warning(
                "AiEscalate ignored — session %s not in AI triage",
                session.session_id,
            )
            return

        session.awaiting_ai_triage = False
        session.ai_escalate_targets = list(targets)
        session.touch()
        # Sticky-decline Skipped locations before Gate 2 so post-AI rescans
        # cannot reopen them and send them to Abbenay.
        _decline_skipped_ai_escalation(session)

        if not targets:
            session.status = 3  # COMPLETE — user skipped all AI escalation
            async for event in self._session_build_result(session):
                yield event
            return

        async for event in self._session_run_ai_gate(session):
            yield event

    async def _session_run_ai_gate(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Run Tier 2 AI on the post-Gate-1 graph and emit Gate 2 proposals.

        Args:
            session: Session after Tier 1 approval (graph already spliced).
                ``ai_escalate_targets`` filters which open findings Abbenay sees.

        Yields:
            SessionEvent: Progress, ProposalsReady (Gate 2), or SessionResult if no AI proposals.
        """
        from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415
        from apme_engine.remediation.graph_engine import GraphRemediationEngine  # noqa: PLC0415
        from apme_engine.remediation.partition import (  # noqa: PLC0415
            add_classification_to_violations,
            count_by_remediation_class,
        )

        graph = session.content_graph
        engine = session.graph_engine
        if not isinstance(engine, GraphRemediationEngine) or not isinstance(graph, ContentGraph):
            session.status = 3
            async for event in self._session_build_result(session):
                yield event
            return

        session.status = 2  # PROCESSING
        session.current_tier = 2
        # Snapshot Gate-1 (or format-only) bytes before AI mutates the graph.
        # Decline-all must restore these — splice-after-reject can be a no-op
        # when hashes match originals while working_files still hold leaked AI.
        session.pre_gate2_files = dict(session.working_files)
        _ai_msg = ProgressUpdate(
            message="Starting AI escalation (Tier 1 decisions already applied)",
            phase="graph-ai",
            level=2,
        )
        session.progress_logs.append(_ai_msg)
        yield SessionEvent(progress=_ai_msg)

        # Gate 2 pending proposals must contain only current AI proposals.
        # Preserve anything already pending as telemetry-visible rejections.
        for pid, proposal in list(session.proposals.items()):
            _record_rejected_proposal(session, pid, proposal)
            session.proposals.pop(pid, None)

        # Continue from current open violations only. skip_tier1 prevents
        # re-applying Gate 1 declines; sticky declined ledger rows are the
        # other half of that guarantee (ADR-062 Option C).
        # interactive=True keeps AI + post-AI Tier 1 unapproved until Gate 2
        # ApprovalRequest — never auto-approve deterministic cleanup on top of
        # pending AI YAML (that leaked into PRs when users accepted nothing).
        open_violations = [dict(v) for v in graph.query_violations(status="open")]
        open_violations = _filter_violations_by_escalate_targets(
            open_violations,
            session.ai_escalate_targets,
        )
        # Empty allow-list after triage means skip AI (COMPLETE). Unfiltered
        # empty open set still calls remediate (legacy / mock test path).
        if session.ai_escalate_targets is not None and not open_violations:
            session.status = 3
            async for event in self._session_build_result(session):
                yield event
            return

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _gate2_progress(
            phase: str,
            message: str,
            fraction: float = 0.0,
            level: int = 2,
            *,
            ai_completed: int = 0,
            ai_total: int = 0,
        ) -> None:
            update = ProgressUpdate(
                message=message,
                phase=phase,
                progress=fraction,
                level=level,
                ai_completed=ai_completed,
                ai_total=ai_total,
            )
            EngineServicer._stamp_progress_update(update, session)
            loop.call_soon_threadsafe(progress_queue.put_nowait, update)

        engine._progress_cb = _gate2_progress  # noqa: SLF001

        async def _gate2_heartbeat() -> None:
            while True:
                await asyncio.sleep(15)
                progress_queue.put_nowait(
                    ProgressUpdate(message="Processing...", phase="heartbeat", level=1),
                )

        ai_concurrency = parse_ai_concurrency()
        try:
            self._begin_ai_operation_budget(
                session,
                violations=open_violations,
                registry=engine._registry,  # noqa: SLF001
                max_ai_attempts=engine._max_ai_attempts,  # noqa: SLF001
                concurrency=ai_concurrency,
            )
        except BudgetConfigError as exc:
            yield SessionEvent(error=SessionError(code="invalid_budget_config", message=str(exc)))
            return

        hb_task: asyncio.Task[None] = asyncio.create_task(_gate2_heartbeat())
        remediate_task = asyncio.create_task(
            engine.remediate(
                open_violations,
                interactive=True,
                skip_ai=False,
                skip_tier1=True,
            ),
        )

        try:
            async for event in self._drain_remediate_task(session, remediate_task, progress_queue):
                yield event
                if event.WhichOneof("event") == "error":
                    return

            if remediate_task.cancelled():
                return
            graph_report = remediate_task.result()
        finally:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task
            if not remediate_task.done():
                remediate_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await remediate_task

        remaining = [dict(v) for v in graph_report.remaining_violations]
        remaining.extend(session.dep_health_violations)
        add_classification_to_violations(remaining)
        rem_counts = count_by_remediation_class(remaining)
        _enrich_violations_from_graph(remaining, graph, fixed=False)
        remaining_protos = [violation_dict_to_proto(v) for v in remaining]
        # Gate 2 interactive: ledger ``fixed`` is authoritative (do not add to
        # prev_fixed — that double-counts Gate 1 fixes already in the ledger).
        fixed = [dict(fv) for fv in graph.query_violations(status="fixed")]
        _enrich_violations_from_graph(fixed, graph, fixed=True)
        fixed_protos = [violation_dict_to_proto(v) for v in fixed]
        session.report = FixReport(
            passes=(session.report.passes if session.report else 0) + graph_report.passes,
            fixed=len(fixed_protos),
            remaining_ai=rem_counts.get("ai-candidate", 0),
            remaining_manual=rem_counts.get("manual-review", 0),
            oscillation_detected=bool(session.report and session.report.oscillation_detected)
            or graph_report.oscillation_detected,
            remaining_violations=remaining_protos,
            fixed_violations=fixed_protos,
        )
        session.remaining_ai = list(remaining)

        proposed_proposals: list[Proposal] = []
        ai_built: list[Proposal] = []
        if graph_report.ai_proposals:
            ai_built = self._build_graph_proposals(graph_report.ai_proposals)
        gate2_t1_built: list[Proposal] = []
        if getattr(graph_report, "tier1_proposals", None):
            gate2_t1_raw = self._build_tier1_proposals(graph_report.tier1_proposals)
            for offset, proposal in enumerate(gate2_t1_raw):
                proposal.id = f"g2-t1-{offset:04d}"
                proposal.tier = 2
            gate2_t1_built = gate2_t1_raw
        proposed_proposals = self._consolidate_gate2_proposals(ai_built, gate2_t1_built)
        proposed_rule_files: set[tuple[str, str]] = set()
        for p in proposed_proposals:
            for raw_rid in p.rule_id.split(","):
                clean_rid = raw_rid.strip()
                if clean_rid:
                    proposed_rule_files.add((clean_rid, p.file))
        declined_proposals = self._build_declined_proposals(
            remaining,
            proposed_rule_files,
            start_idx=len(proposed_proposals),
        )
        all_proposals = proposed_proposals + declined_proposals

        if all_proposals:
            # Do NOT splice into working_files until Gate 2 approve/decline.
            for p in proposed_proposals:
                session.proposals[p.id] = p
            session.review_declined_proposals = {p.id: p for p in declined_proposals}
            session.ai_proposals = list(graph_report.ai_proposals) if graph_report.ai_proposals else []
            session.tier1_proposals = list(getattr(graph_report, "tier1_proposals", None) or [])
            session.status = 1  # AWAITING_APPROVAL
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=all_proposals,
                    tier=2,
                    status=session.status,
                ),
            )
        else:
            # No AI proposals to review — drop any staged unapproved AI/det
            # progression, then sync working_files from approved snapshots only.
            _reject_unapproved_graph_progress(graph)
            originals = session.graph_originals or {}
            from apme_engine.formatter import format_content  # noqa: PLC0415
            from apme_engine.remediation.graph_engine import splice_modifications  # noqa: PLC0415

            patches = splice_modifications(graph, originals)
            for patch in patches:
                fmt_result = format_content(patch.patched, filename=Path(patch.path).name)
                if getattr(fmt_result, "changed", False):
                    patch.patched = getattr(fmt_result, "formatted", patch.patched)
            _write_patches_to_session(session, patches)
            if session.temp_dir is not None and patches:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    _write_patches_to_temp_dir,
                    session.temp_dir,
                    patches,
                )
            session.status = 3
            async for event in self._session_build_result(session):
                yield event

    @staticmethod
    def _session_apply_approved(
        session: SessionState,
        approved_ids: set[str],
        *,
        finalize: bool = True,
    ) -> tuple[int, list[SplicedFilePatch] | None]:
        """Apply approved proposals to session working state.

        For graph-based proposals (``id`` starts with ``"ai-"`` or ``"t1-"``),
        the ContentGraph already holds the pending changes.  Approved nodes
        are promoted; rejected nodes are reverted.  Post-approval,
        ``splice_modifications`` re-generates working files.

        For legacy file-based proposals, text-based find/replace is used.

        Args:
            session: Active session whose working files will be mutated.
            approved_ids: Set of proposal IDs the user accepted.
            finalize: When true, mark session COMPLETE. When false (Gate 1
                with AI still pending), leave status for the AI gate.

        Returns:
            Number of proposals successfully applied.
        """
        if not approved_ids and not session.proposals:
            session.review_declined_proposals.clear()
            if finalize:
                session.status = 3  # COMPLETE
            session.awaiting_tier1_gate = False
            return 0, None

        graph = session.content_graph
        originals = session.graph_originals

        has_graph_proposals = graph is not None and any(
            pid.startswith(("ai-", "t1-", "g2-t1-")) for pid in session.proposals
        )

        temp_patches: list[SplicedFilePatch] | None = None
        if has_graph_proposals and graph is not None and originals is not None:
            applied, rejected_nodes, approved_nodes, temp_patches = _apply_graph_approvals(
                session,
                graph,
                originals,
                approved_ids,
            )
            _reconcile_after_approval(
                session,
                graph,
                rejected_nodes,
                approved_node_ids=approved_nodes,
            )
        else:
            applied = _apply_text_approvals(session, approved_ids)

        session.awaiting_tier1_gate = False
        session.review_declined_proposals.clear()
        if finalize:
            session.status = 3  # COMPLETE — user has finished reviewing
        else:
            session.status = 2  # PROCESSING — Gate 2 AI next
        logger.info(
            "Approval result: %d/%d proposals applied (session=%s finalize=%s)",
            applied,
            len(approved_ids),
            session.session_id,
            finalize,
        )
        return applied, temp_patches

    @staticmethod
    def _build_declined_proposals(
        remaining_violations: Sequence[Mapping[str, object]],
        proposed_rule_files: set[tuple[str, str]],
        start_idx: int = 0,
    ) -> list[Proposal]:
        """Build declined proposals for AI-candidate violations the AI couldn't fix.

        These let the user see all AI-candidate violations in the review panel,
        not just the ones the AI successfully produced fixes for.

        Args:
            remaining_violations: Remaining violations after remediation.
            proposed_rule_files: Set of (rule_id, file) already covered by proposed proposals.
            start_idx: Starting index for declined proposal IDs.

        Returns:
            List of Proposal protos with ``status="declined"``.
        """
        from apme_engine.engine.models import RemediationClass  # noqa: PLC0415

        declined: list[Proposal] = []
        idx = start_idx
        for v in remaining_violations:
            rc = v.get("remediation_class")
            rc_val = rc.value if hasattr(rc, "value") else str(rc) if rc else ""
            if rc_val != RemediationClass.AI_CANDIDATE.value:
                continue
            rule_id = str(v.get("rule_id", ""))
            file_path = str(v.get("file", ""))
            if (rule_id, file_path) in proposed_rule_files:
                continue
            raw_line = v.get("line")
            line_start = 0
            if raw_line is not None:
                try:
                    if isinstance(raw_line, (list, tuple)):
                        line_start = int(str(raw_line[0])) if raw_line else 0
                    else:
                        line_start = int(str(raw_line))
                except (TypeError, ValueError, IndexError):
                    line_start = 0
            declined.append(
                Proposal(
                    id=f"ai-declined-{idx:04d}",
                    file=file_path,
                    rule_id=rule_id,
                    line_start=line_start,
                    tier=2,
                    status="declined",
                    suggestion=str(v.get("message", "")),
                    explanation="AI could not generate a fix for this violation.",
                    source="ai",
                    path=str(v.get("path", "") or ""),
                    node_type=str(v.get("node_type", "") or ""),
                )
            )
            idx += 1
        return declined

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

        remaining_violations = [violation_dict_to_proto(v) for v in session.remaining_ai + session.remaining_manual]

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
        for meta in session.rejected_proposals.values():
            tier_val = meta.get("tier", 0)
            conf_val = meta.get("confidence", 0.0)
            proposal_outcomes.append(
                ProposalOutcome(
                    proposal_id=str(meta.get("proposal_id", "")),
                    rule_id=str(meta.get("rule_id", "")),
                    file=str(meta.get("file", "")),
                    tier=int(tier_val) if isinstance(tier_val, (int, float, str)) else 0,
                    confidence=float(conf_val) if isinstance(conf_val, (int, float, str)) else 0.0,
                    status="rejected",
                )
            )
        for pid, p in session.proposals.items():
            if pid in session.rejected_proposals:
                continue
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
        rem_counts = count_by_remediation_class(all_remaining)
        summary = ScanSummary(
            total=len(all_remaining) + report.fixed,
            auto_fixable=report.fixed,
            ai_candidate=rem_counts.get("ai-candidate", 0),
            manual_review=rem_counts.get("manual-review", 0),
        )

        manifest = _build_manifest(session)

        graph_json = ""
        if session.content_graph is not None:
            try:
                graph_json = json.dumps(
                    session.content_graph.to_dict(),  # type: ignore[attr-defined]
                    default=str,
                )
            except Exception:
                logger.warning("Failed to serialize ContentGraph for event", exc_info=True)

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
            content_graph_json=graph_json,
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
        if session.awaiting_assess and session.assess_findings:
            from apme.v1.common_pb2 import Violation as ViolationProto  # noqa: PLC0415

            findings = [v for v in session.assess_findings if isinstance(v, ViolationProto)]
            yield SessionEvent(
                findings=FindingsReady(
                    violations=findings,
                    status=1,
                    ttl_seconds=session.ttl_seconds,
                ),
            )
        elif session.awaiting_ai_triage and session.ai_triage_candidates:
            from apme.v1.common_pb2 import Violation as ViolationProto  # noqa: PLC0415

            cands = [v for v in session.ai_triage_candidates if isinstance(v, ViolationProto)]
            yield SessionEvent(
                ai_triage=AiTriageReady(
                    candidates=cands,
                    status=4,  # AWAITING_AI_TRIAGE
                    ttl_seconds=session.ttl_seconds,
                ),
            )
        elif (
            (session.proposals or session.review_declined_proposals)
            and session.status == 1
            and not session.awaiting_assess
            and not session.awaiting_ai_triage
        ):
            review_proposals = list(session.proposals.values()) + list(session.review_declined_proposals.values())
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=review_proposals,
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
        addr = os.environ.get("APME_ABBENAY_ADDR", "").strip()
        if not addr:
            return ListAIModelsResponse(models=[])

        try:
            from apme_engine.remediation.abbenay_provider import (  # noqa: PLC0415
                make_abbenay_client,
            )

            client = make_abbenay_client(addr)
            await client.connect()  # type: ignore[attr-defined]
            try:
                raw_models = await client.list_models()  # type: ignore[attr-defined]
            finally:
                await client.disconnect()  # type: ignore[attr-defined]

            models = [AIModelInfo(id=m.id, provider=m.provider, name=m.name) for m in raw_models]
            return ListAIModelsResponse(models=models)
        except ImportError:
            logger.debug("abbenay_grpc not installed — returning empty model list")
            return ListAIModelsResponse(models=[])
        except Exception:
            logger.warning("Failed to list AI models from Abbenay at %s", addr, exc_info=True)
            return ListAIModelsResponse(models=[])

    # ── Health RPC (aggregate) ────────────────────────────────────────

    async def Health(
        self,
        request: HealthRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> HealthResponse:
        """Aggregate health including required validators and Galaxy Proxy.

        Engine is ok only when required validators and Galaxy Proxy are
        configured and healthy.

        Required validators (native, OPA, Ansible) missing from the environment
        yield an unhealthy aggregate status so probes fail before scan setup.
        Galaxy Proxy (``APME_GALAXY_PROXY_URL``) must respond ``ok`` on
        ``/health`` — it is a core service, not optional.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with aggregate status and downstream service health.
        """
        downstream: list[ServiceHealth] = []
        unhealthy = False

        # Probe validators
        for name, env_var in VALIDATOR_ENV_VARS.items():
            addr = os.environ.get(env_var)
            if not addr:
                if name in REQUIRED_VALIDATORS:
                    unhealthy = True
                    downstream.append(
                        ServiceHealth(
                            name=name,
                            status=f"error: {env_var} not configured",
                            address="",
                        )
                    )
                continue
            try:
                channel = grpc.aio.insecure_channel(addr)
                try:
                    stub = validate_pb2_grpc.ValidatorStub(channel)  # type: ignore[no-untyped-call]
                    resp = await stub.Health(HealthRequest(), timeout=5)
                    status = resp.status
                    if name in REQUIRED_VALIDATORS and status != "ok":
                        unhealthy = True
                    downstream.append(ServiceHealth(name=name, status=status, address=addr))
                finally:
                    await channel.close(grace=None)
            except Exception as e:  # noqa: BLE001 - health probe must degrade
                if name in REQUIRED_VALIDATORS:
                    unhealthy = True
                downstream.append(ServiceHealth(name=name, status=f"error: {e}", address=addr))

        # Galaxy Proxy is required (HTTP /health) — sole collection install path.
        proxy_url = os.environ.get("APME_GALAXY_PROXY_URL", "").strip()
        if not proxy_url:
            unhealthy = True
            downstream.append(
                ServiceHealth(
                    name="galaxy_proxy",
                    status="error: APME_GALAXY_PROXY_URL not configured",
                    address="",
                )
            )
        else:
            health_url = proxy_url.rstrip("/") + "/health"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    http_resp = await client.get(health_url)
                proxy_ok = False
                detail = f"HTTP {http_resp.status_code}"
                if http_resp.status_code == 200:
                    try:
                        payload = http_resp.json()
                    except ValueError:
                        payload = None
                    proxy_ok = isinstance(payload, dict) and payload.get("status") == "ok"
                    if not proxy_ok:
                        reported = payload.get("status") if isinstance(payload, dict) else None
                        detail = f"status={reported!r}" if reported is not None else "invalid /health body"
                if proxy_ok:
                    downstream.append(ServiceHealth(name="galaxy_proxy", status="ok", address=proxy_url))
                else:
                    unhealthy = True
                    downstream.append(
                        ServiceHealth(
                            name="galaxy_proxy",
                            status=f"error: {detail}",
                            address=proxy_url,
                        )
                    )
            except Exception as e:  # noqa: BLE001 - health probe must degrade
                unhealthy = True
                downstream.append(ServiceHealth(name="galaxy_proxy", status=f"error: {e}", address=proxy_url))

        return HealthResponse(
            status="unhealthy" if unhealthy else "ok",
            downstream=downstream,
        )


async def serve(listen_address: str = "0.0.0.0:50051") -> grpc.aio.Server:
    """Create, bind, and start async gRPC server with Engine servicer.

    Args:
        listen_address: Host:port to bind (e.g. 0.0.0.0:50051).

    Returns:
        Started gRPC server (caller must wait_for_termination).

    Raises:
        RuntimeError: If the listen address cannot be bound.
    """
    server = grpc.aio.server(
        maximum_concurrent_rpcs=_MAX_CONCURRENT_RPCS,
        options=[
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
        ],
    )
    engine_pb2_grpc.add_EngineServicer_to_server(EngineServicer(), server)  # type: ignore[no-untyped-call]
    bound_port = server.add_insecure_port(listen_address)
    if bound_port == 0:
        msg = f"Engine failed to bind {listen_address}"
        raise RuntimeError(msg)
    await _collect_rule_catalog()
    await server.start()
    await start_sinks()
    await _push_rule_catalog_to_gateway()
    return server


def _working_files_key(temp_dir: Path | None, path: str) -> str:
    """Normalize a patch path to a relative ``working_files`` key.

    Absolute paths under ``temp_dir`` are rewritten as relative keys so
    splice updates do not create duplicate absolute/relative entries.

    Args:
        temp_dir: Session temp directory root, or ``None``.
        path: Patch path from splice (relative or absolute).

    Returns:
        Relative path string when under ``temp_dir``; otherwise ``path``.
    """
    patch_path = Path(path)
    if temp_dir is None:
        return path
    try:
        return str(patch_path.relative_to(temp_dir))
    except ValueError:
        try:
            return str(patch_path.resolve().relative_to(temp_dir.resolve()))
        except ValueError:
            return path


def _write_patches_to_temp_dir(temp_dir: Path, patches: Sequence[SplicedFilePatch]) -> None:
    """Write spliced patch contents into ``temp_dir`` (blocking I/O).

    Path rules: relative paths that contain ``..`` segments are always
    rejected. Absolute paths are allowed only when the resolved target
    stays under ``temp_dir``. Write failures raise (no silent ``OSError``
    suppress) so later AI-gate / validator rescans cannot read stale temp
    content.

    Args:
        temp_dir: Session temp directory root.
        patches: Splice results with ``path`` and ``patched`` attributes.

    Raises:
        ValueError: If a patch path escapes ``temp_dir`` or uses relative ``..``.
        OSError: If a write fails.
    """
    root = temp_dir.resolve()
    for patch in patches:
        rel = Path(patch.path)
        if rel.is_absolute():
            path = rel.resolve()
        else:
            if ".." in rel.parts:
                raise ValueError(f"Unsafe patch path rejected: {patch.path!r}")
            path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Patch path escapes temp root: {patch.path!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(patch.patched, encoding="utf-8")
        except OSError:
            logger.exception(
                "Failed to write patch to temp_dir path=%s temp_dir=%s",
                path,
                root,
            )
            raise


def _reject_unapproved_graph_progress(graph: object) -> set[str]:
    """Reject every node with unapproved AI or deterministic progression.

    Args:
        graph: ContentGraph (or ignored if wrong type).

    Returns:
        Node IDs that were rejected.
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415

    if not isinstance(graph, ContentGraph):
        return set()
    rejected: set[str] = set()
    for node in graph.nodes():
        has_unapproved = any(
            (not entry.approved) and entry.source in ("ai", "deterministic") for entry in node.progression
        )
        if has_unapproved:
            graph.reject_node(node.node_id)
            rejected.add(node.node_id)
    return rejected


def _write_patches_to_session(
    session: SessionState,
    patches: list[SplicedFilePatch],
) -> None:
    """Update ``working_files`` from spliced patches (in-memory only).

    Disk sync is the caller's job via ``run_in_executor(_write_patches_to_temp_dir)``
    so the grpc.aio event loop is never blocked (ADR-007).

    Args:
        session: Active session.
        patches: Spliced file patches to materialize.
    """
    for patch in patches:
        rel_path = _working_files_key(session.temp_dir, patch.path)
        session.working_files[rel_path] = patch.patched.encode("utf-8")


def _restore_pre_gate2_files(session: SessionState) -> list[SplicedFilePatch]:
    """Restore ``working_files`` from the pre-Gate-2 snapshot.

    Returns FilePatch objects so the async approval handler can write temp_dir
    via ``run_in_executor`` (ADR-007). Does not touch disk itself.

    Args:
        session: Session that may hold ``pre_gate2_files``.

    Returns:
        Patches representing the restored snapshot (empty when none).
    """
    if not session.pre_gate2_files:
        return []
    session.working_files = dict(session.pre_gate2_files)
    restored: list[SplicedFilePatch] = []
    for rel_path, content in session.pre_gate2_files.items():
        restored.append(
            SplicedFilePatch(
                path=rel_path,
                original="",
                patched=content.decode("utf-8", errors="replace"),
                diff="",
                rule_ids=[],
            )
        )
    return restored


def _apply_graph_approvals(
    session: SessionState,
    graph: object,
    originals: dict[str, str],
    approved_ids: set[str],
) -> tuple[int, set[str], set[str], list[SplicedFilePatch]]:
    """Apply graph-based approvals: approve/reject nodes, re-splice files.

    Args:
        session: Active session.
        graph: ContentGraph with pending AI transforms.
        originals: Original file contents for splicing.
        approved_ids: Proposal IDs the user accepted.

    Returns:
        Tuple of (proposals applied, rejected node IDs, approved node IDs, patches).
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415
    from apme_engine.remediation.graph_engine import (  # noqa: PLC0415
        AINodeProposal,
        splice_modifications,
    )

    if not isinstance(graph, ContentGraph):
        return (_apply_text_approvals(session, approved_ids), set(), set(), [])

    ai_proposals: list[AINodeProposal] = [p for p in session.ai_proposals if isinstance(p, AINodeProposal)]
    from apme_engine.remediation.graph_engine import Tier1NodeProposal  # noqa: PLC0415

    tier1_proposals: list[Tier1NodeProposal] = [p for p in session.tier1_proposals if isinstance(p, Tier1NodeProposal)]

    # Fallback for older in-memory proposals that predate Proposal.path.
    proposal_node_map: dict[str, str] = {}
    for idx, anp in enumerate(ai_proposals):
        proposal_node_map[f"ai-{idx:04d}"] = anp.node_id
    for idx, tnp in enumerate(tier1_proposals):
        proposal_node_map[f"t1-{idx:04d}"] = tnp.node_id
        proposal_node_map[f"g2-t1-{idx:04d}"] = tnp.node_id

    applied = 0
    rejected_node_ids: set[str] = set()
    approved_node_ids: set[str] = set()
    all_proposal_ids = list(session.proposals.keys())

    for pid in all_proposal_ids:
        proposal = session.proposals.get(pid)
        if not proposal:
            continue

        # Prefer Proposal.path (node_id wire field); enum maps are fallback only.
        node_id = (getattr(proposal, "path", None) or "").strip() or proposal_node_map.get(pid)
        if not node_id:
            continue

        if pid in approved_ids:
            graph.approve_node(node_id)
            approved_node_ids.add(node_id)
            session.approved_proposals.append(
                {
                    "proposal_id": pid,
                    "rule_id": proposal.rule_id,
                    "file": proposal.file,
                    "tier": proposal.tier,
                    "confidence": proposal.confidence,
                    "source": proposal.source,
                }
            )
            session.approved_ids.add(pid)
            applied += 1
            session.proposals.pop(pid, None)
        else:
            graph.reject_node(node_id)
            rejected_node_ids.add(node_id)
            _record_rejected_proposal(session, pid, proposal)
            session.proposals.pop(pid, None)

    # Revert staged AI / deterministic transforms that never got a proposal
    # (or shared a node with a declined proposal).
    for node in graph.nodes():
        if node.node_id in approved_node_ids:
            continue
        has_unapproved = any(
            (not entry.approved) and entry.source in ("ai", "deterministic") for entry in node.progression
        )
        if has_unapproved:
            graph.reject_node(node.node_id)
            rejected_node_ids.add(node.node_id)

    # Always start from the pre-Gate-2 snapshot when present so decline-all
    # cannot leave leaked AI bytes in working_files after a no-op splice.
    restored_patches = _restore_pre_gate2_files(session) if session.pre_gate2_files else []

    from apme_engine.formatter import format_content

    patches = splice_modifications(graph, originals)
    for patch in patches:
        fmt_result = format_content(patch.patched, filename=Path(patch.path).name)
        if getattr(fmt_result, "changed", False):
            patch.patched = getattr(fmt_result, "formatted", patch.patched)

    _write_patches_to_session(session, patches)

    # Prefer splice results over restore-only rows for the same path.
    if restored_patches:
        by_path = {_working_files_key(session.temp_dir, p.path): p for p in restored_patches}
        for patch in patches:
            by_path[_working_files_key(session.temp_dir, patch.path)] = patch
        patches = list(by_path.values())

    return (applied, rejected_node_ids, approved_node_ids, patches)


def _reconcile_after_approval(
    session: SessionState,
    graph: object,
    rejected_node_ids: set[str],
    approved_node_ids: set[str] | None = None,
) -> None:
    """Reconcile session accounting after AI proposals are approved/rejected.

    Promotes ``proposed`` violations on approved nodes to ``fixed``,
    transitions ``proposed`` violations on rejected nodes to ``declined``,
    then queries the graph ledger for authoritative counts.

    Args:
        session: Active session to reconcile.
        graph: ContentGraph after approve/reject mutations.
        rejected_node_ids: Node IDs whose AI proposals were rejected.
        approved_node_ids: Node IDs whose AI proposals were accepted.
            When omitted, only rejected nodes are updated (safe default).
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415

    if not isinstance(graph, ContentGraph):
        return

    approved = approved_node_ids or set()
    # Promote only explicitly approved nodes; decline rejected + leftover proposed.
    for node in graph.nodes():
        nid = node.node_id
        if nid in approved:
            graph.approve_proposed(nid)
        else:
            graph.decline_proposed(nid)

    # Remaining = open + declined + ai_abstained (all unresolved violations).
    # Post-approval, AI has already had its chance — graph-derived violations
    # are manual review regardless of what classify_violation would say.
    open_violations = graph.query_violations(status="open")
    declined_violations = graph.query_violations(status="declined")
    ai_abstained_violations = graph.query_violations(status="ai_abstained")
    remaining = [dict(v) for v in open_violations + declined_violations + ai_abstained_violations]
    for v in remaining:
        v["remediation_class"] = RemediationClass.MANUAL_REVIEW
    _enrich_violations_from_graph(remaining, graph, fixed=False)
    remaining.extend(dict(v) for v in session.dep_health_violations)

    fixed = [dict(v) for v in graph.query_violations(status="fixed")]
    for v in fixed:
        v["remediation_class"] = RemediationClass.AUTO_FIXABLE
    _enrich_violations_from_graph(fixed, graph, fixed=True)

    session.remaining_ai = []
    session.remaining_manual = []
    for violation in remaining:
        rc = violation.get("remediation_class")
        rc_val = rc.value if isinstance(rc, RemediationClass) else str(rc) if rc is not None else ""
        if rc_val == RemediationClass.AI_CANDIDATE.value:
            session.remaining_ai.append(violation)
        else:
            session.remaining_manual.append(violation)

    old_report = session.report or FixReport()

    remaining_protos = [violation_dict_to_proto(v) for v in session.remaining_ai + session.remaining_manual]
    fixed_protos = [violation_dict_to_proto(v) for v in fixed]

    session.report = FixReport(
        passes=old_report.passes,
        fixed=len(fixed),
        remaining_ai=len(session.remaining_ai),
        remaining_manual=len(session.remaining_manual),
        oscillation_detected=old_report.oscillation_detected,
        remaining_violations=remaining_protos,
        fixed_violations=fixed_protos,
    )

    logger.info(
        "Post-approval reconciliation: %d fixed, %d remaining (%d declined), %d rejected nodes (session=%s)",
        len(fixed),
        len(remaining),
        len(declined_violations),
        len(rejected_node_ids),
        session.session_id,
    )


def _apply_text_approvals(
    session: SessionState,
    approved_ids: set[str],
) -> int:
    """Apply legacy text-based approvals via find/replace.

    Args:
        session: Active session whose working files will be mutated.
        approved_ids: Set of proposal IDs the user accepted.

    Returns:
        Number of proposals successfully applied.
    """
    applied = 0
    for pid in list(approved_ids):
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

    for pid, proposal in list(session.proposals.items()):
        _record_rejected_proposal(session, pid, proposal)
        session.proposals.pop(pid, None)

    return applied


def _record_rejected_proposal(
    session: SessionState,
    proposal_id: str,
    proposal: Proposal,
) -> None:
    """Persist rejected proposal metadata for FixCompletedEvent telemetry.

    Args:
        session: Active session storing telemetry snapshots.
        proposal_id: Proposal identifier that was rejected.
        proposal: Proposal proto carrying rule/file/tier metadata.
    """
    session.rejected_proposals.setdefault(
        proposal_id,
        {
            "proposal_id": proposal_id,
            "rule_id": proposal.rule_id,
            "file": proposal.file,
            "tier": proposal.tier,
            "confidence": proposal.confidence,
        },
    )


_cached_register_request: reporting_pb2.RegisterRulesRequest | None = None


async def _collect_rule_catalog() -> None:
    """Collect built-in rules and populate ``_known_rule_ids``.

    This is a **hard requirement** and must complete before the gRPC
    server starts.  If catalog collection fails or returns no rules,
    the Engine cannot perform bidirectional audit (ADR-041) and must
    not serve scans.

    The collected rules are cached for the subsequent best-effort
    Gateway push (``_push_rule_catalog_to_gateway``).

    Raises:
        RuntimeError: If catalog collection fails or returns zero rules.
    """
    import os
    import platform

    global _known_rule_ids, _cached_register_request  # noqa: PLW0603

    from apme_engine.rule_catalog import collect_all_rules

    rules = collect_all_rules()
    if not rules:
        raise RuntimeError(
            "Rule catalog collection returned zero rules. "
            "The Engine cannot start without an authoritative catalog (ADR-041)."
        )

    _known_rule_ids = {r.rule_id for r in rules}
    logger.info("Known rule IDs populated: %d rules", len(_known_rule_ids))

    pod_id = os.environ.get("APME_POD_ID", "").strip() or platform.node()
    is_authority = os.environ.get("APME_RULE_AUTHORITY", "true").strip().lower() in (
        "true",
        "1",
        "yes",
    )

    _cached_register_request = reporting_pb2.RegisterRulesRequest(
        pod_id=pod_id,
        is_authority=is_authority,
        rules=rules,
    )


async def _push_rule_catalog_to_gateway() -> None:
    """Push the collected rule catalog to the Gateway (best-effort).

    Must be called after ``_collect_rule_catalog`` and ``start_sinks``.
    The Engine is authoritative even without a Gateway (CLI-only /
    daemon mode), so failures here are logged but do not prevent serving.

    The cached request is cleared after this call regardless of outcome;
    the retry loop in ``emit_register_rules`` captures its own reference.
    """
    global _cached_register_request  # noqa: PLW0603

    request = _cached_register_request
    _cached_register_request = None
    if request is None:
        logger.warning("No cached rule catalog; skipping Gateway push")
        return
    try:
        await emit_register_rules(request)
    except Exception:
        logger.warning("Gateway push failed (best-effort); local catalog is authoritative", exc_info=True)


def _validate_rule_configs(
    rule_configs: list[object],
    *,
    complete: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate rule IDs in configs against this Engine's known catalog.

    Performs a forward check (unknown IDs) always.  When *complete* is
    ``True`` (Gateway path), also performs a reverse check (missing IDs)
    to detect catalog drift.

    Args:
        rule_configs: Proto RuleConfig messages.
        complete: If ``True``, treat *rule_configs* as the full catalog
            and check for missing IDs (bidirectional audit).

    Returns:
        Tuple of (unknown_ids, missing_ids).  *missing_ids* is always
        empty when *complete* is ``False``.
    """
    if not _known_rule_ids or not rule_configs:
        return [], []
    config_ids: set[str] = set()
    unknown: list[str] = []
    for rc in rule_configs:
        rid: str = rc.rule_id  # type: ignore[attr-defined]
        config_ids.add(rid)
        if rid not in _known_rule_ids:
            unknown.append(rid)
    missing: list[str] = []
    if complete:
        missing = sorted(_known_rule_ids - config_ids)
    return unknown, missing
