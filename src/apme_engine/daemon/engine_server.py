"""Engine daemon: async gRPC server that runs engine then fans out to all validators.

The Engine is the sole API surface for all clients (CLI, web UI, CI).
Clients send file bytes via gRPC streams and receive processed bytes back.
The Engine delegates internally to validators and remediation.
"""

from __future__ import annotations

import asyncio
import configparser
import contextlib
import contextvars
import datetime
import difflib
import json
import logging
import os
import posixpath
import re
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePath, PurePosixPath
from typing import Protocol, TypeVar, cast

import grpc
import grpc.aio
from google.protobuf.message import Message

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
from apme.v1.validate_pb2 import ValidateRequest, ValidateRequestChunk
from apme_engine.cache import BoundedCache
from apme_engine.daemon import engine_health, engine_upload
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
from apme_engine.daemon.fs_utils import canonicalize_upload_entries
from apme_engine.daemon.fs_utils import write_chunked_fs as _write_chunked_fs
from apme_engine.daemon.session import (
    ResourceExhaustedError,
    SessionState,
    SessionStore,
    limit_venv_builds,
)
from apme_engine.daemon.validator_errors import RULE_MISSING_VENV, RULE_VALIDATOR_FAILURE
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.engine.finder import TaskfileSourceContext
from apme_engine.engine.models import RemediationClass, ViolationDict
from apme_engine.graph.content_graph import ContentGraph, ViolationRecord
from apme_engine.graph.scanner import filter_noqa_violations, graph_rule_opt_in_from_rule_configs, parse_noqa
from apme_engine.log_bridge import attach_collector
from apme_engine.remediation.graph_engine import AIPolicyContext, SpliceConflictError, SpliceOutcome
from apme_engine.remediation.graph_engine import FilePatch as SplicedFilePatch
from apme_engine.rule_ids import normalize_rule_id
from apme_engine.runner import run_scan
from apme_engine.venv_manager.session import (
    VenvSession,
    VenvSessionManager,
    get_dependency_tree,
    list_installed_collections,
    list_installed_packages,
    venv_read_guard,
    venv_write_guard,
    wait_out_worker,
)
from apme_engine.violation_identity import diagnostic_discriminator

logger = logging.getLogger("apme.engine")

_ExecutorResult = TypeVar("_ExecutorResult")


@dataclass(frozen=True)
class _FormatBatchResult:
    """Result of batch formatting: diffs for changed files and skip diagnostics.

    Attributes:
        diffs: FileDiff protos for files whose content changed.
        skipped: ``(path, reason)`` pairs for files that could not be formatted.
    """

    diffs: list[FileDiff]
    skipped: list[tuple[str, str]] = field(default_factory=list)


_MAX_CONCURRENT_RPCS = int(os.environ.get("APME_ENGINE_MAX_RPCS", "16"))
_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — hierarchy+scandata can exceed the 4 MiB default
_GRPC_STREAM_CHUNK_BYTES = 8 * 1024 * 1024
_MAX_STREAMED_VALIDATE_REQUEST_BYTES = 512 * 1024 * 1024

# These budgets apply to the expanded wire representation, not graph node count.
# A normal graph can contain tens of thousands of nodes, each with many fields.
_TRANSPORT_JSON_MAX_BYTES = _GRPC_MAX_MSG
_TRANSPORT_JSON_MAX_VALUES = 5_000_000
_TRANSPORT_JSON_MAX_DEPTH = 256


class GraphTransportError(ValueError):
    """Graph content cannot be transported safely; validation is incomplete."""


def _bounded_transport_json(value: object) -> bytes:
    """Convert and bound an expanded JSON document before encoding it.

    Memoized subtree costs charge every alias occurrence while avoiding alias
    expansion during conversion. Shared containers retain their ordered values;
    cycles, excessive depth, and oversized output abort without truncation.
    Unsafe or unsupported content raises ``GraphTransportError``.

    Args:
        value: Graph or hierarchy data containing inert YAML values.

    Returns:
        Compact ASCII JSON bytes preserving accepted document values.

    """
    memo: dict[int, tuple[object, int, int, int]] = {}
    active: set[int] = set()

    def check(values: int, size: int, depth: int) -> None:
        """Reject unsafe costs before allocating the expanded document.

        Args:
            values: Expanded value and key occurrences.
            size: Expanded JSON bytes.
            depth: Container nesting depth.

        Raises:
            GraphTransportError: If any transport budget is exceeded.
        """
        if values > _TRANSPORT_JSON_MAX_VALUES or size > _TRANSPORT_JSON_MAX_BYTES:
            raise GraphTransportError("Graph transport exceeds assessment limits; validation is incomplete")
        if depth > _TRANSPORT_JSON_MAX_DEPTH:
            raise GraphTransportError("Graph transport is excessively nested; validation is incomplete")

    def string_size(text: str) -> int:
        """Count escaped ASCII JSON bytes using bounded temporary chunks.

        Args:
            text: Literal string or normalized scalar key.

        Returns:
            Exact JSON string size including quotes.
        """
        check(1, len(text) + 2, 0)
        size = 2
        for offset in range(0, len(text), 4096):
            size += len(json.encoder.encode_basestring_ascii(text[offset : offset + 4096])) - 2
            check(1, size, 0)
        return size

    def convert(current: object, nesting: int = 0) -> tuple[object, int, int, int]:
        """Normalize one subtree and calculate its expanded transport costs.

        Args:
            current: Source subtree, whose container identity may be shared.
            nesting: Parent container count.

        Returns:
            Normalized value, occurrence count, JSON bytes, and subtree depth.

        Raises:
            GraphTransportError: If source content cannot be represented safely.
        """
        check(1, 0, nesting)
        identity = id(current)
        if identity in active:
            raise GraphTransportError("Graph transport contains cyclic content; validation is incomplete")
        if identity in memo:
            cached = memo[identity]
            check(cached[1], cached[2], nesting + cached[3])
            return cached
        if current is None:
            result: tuple[object, int, int, int] = (None, 1, 4, 0)
        elif isinstance(current, bool):
            result = (current, 1, 4 if current else 5, 0)
        elif isinstance(current, str):
            result = (current, 1, string_size(current), 0)
        elif isinstance(current, int | float):
            try:
                number = json.dumps(current)
            except ValueError:
                raise GraphTransportError(
                    "Graph transport contains an oversized number; validation is incomplete"
                ) from None
            result = (current, 1, len(number), 0)
        elif isinstance(current, datetime.date | datetime.time | PurePath):
            text = str(current)
            result = (text, 1, string_size(text), 0)
        elif isinstance(current, bytes):
            # Python's bytes representation may use four characters per byte.
            # Bound the conversion itself before creating that representation.
            check(1, len(current) * 4 + 3, nesting)
            text = str(current)
            result = (text, 1, string_size(text), 0)
        elif isinstance(current, dict | list | tuple):
            check(len(current) + 1, 2 + max(0, len(current) - 1), nesting + 1)
            active.add(identity)
            count, size, height = 1, 2 + max(0, len(current) - 1), 1
            mapping: dict[str, object] = {}
            sequence: list[object] = []
            items = current.items() if isinstance(current, dict) else enumerate(current)
            for key, item in items:
                if isinstance(current, dict):
                    if isinstance(key, str):
                        name = key
                    elif key is None or isinstance(key, bool | int | float):
                        try:
                            name = json.dumps(key)
                        except ValueError:
                            raise GraphTransportError(
                                "Graph transport contains an oversized key; validation is incomplete"
                            ) from None
                    else:
                        raise GraphTransportError(
                            "Graph transport contains an unsupported key; validation is incomplete"
                        )
                    if name in mapping:
                        raise GraphTransportError(
                            "Graph transport contains colliding mapping keys; validation is incomplete"
                        )
                    size += string_size(name) + 1
                    count += 1
                    check(count, size, nesting + height)
                converted, child_count, child_size, child_depth = convert(item, nesting + 1)
                count += child_count
                size += child_size
                height = max(height, child_depth + 1)
                check(count, size, nesting + height)
                if isinstance(current, dict):
                    mapping[name] = converted
                else:
                    sequence.append(converted)
            active.remove(identity)
            result = (mapping if isinstance(current, dict) else sequence, count, size, height)
        else:
            raise GraphTransportError("Graph transport contains an unsupported value; validation is incomplete")
        check(result[1], result[2], nesting + result[3])
        memo[identity] = result
        return result

    normalized, _count, _size, _depth = convert(value)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _serialize_graph_transport(graph: ContentGraph, *, slim: bool = True) -> bytes:
    """Serialize a complete graph without truncating authored data.

    Unsafe content raises ``GraphTransportError``.

    Args:
        graph: Graph supplying the public transport representation.
        slim: Whether to omit progression and state history.

    Returns:
        Bounded JSON bytes for validator transport or reporting.

    """
    return _bounded_transport_json(graph.to_dict(slim=slim))


_ANSIBLE_PLUGIN_DIR_NAMES = frozenset({"action_plugins", "library", "module_utils", "modules", "plugins"})
_ANSIBLE_COLLECTION_PART = "ansible_collections"


def _ansible_context_files(files: Mapping[str, bytes]) -> list[File]:
    """Select uploaded files needed to reconstruct Ansible project context.

    Task rescans carry their task payload separately, so Ansible only needs
    project config and local plugin sources in addition to the task payload.
    Plugin sources are selected from configured roots, standard role plugin
    directories, and collection plugin trees rather than arbitrary directories
    whose names happen to resemble plugin locations.

    Args:
        files: Current project files keyed by normalized relative path.

    Returns:
        Config and plugin files required for Ansible plugin resolution.
    """
    configs = [(path, content) for path, content in files.items() if PurePosixPath(path).name == "ansible.cfg"]
    plugin_roots: set[str] = set()

    def add_roots(raw_paths: str, base: str = ".") -> None:
        for raw_path in raw_paths.split(os.pathsep):
            value = raw_path.strip()
            if not value or posixpath.isabs(value):
                continue
            normalized = posixpath.normpath(posixpath.join(base, value))
            if normalized != ".." and not normalized.startswith("../"):
                plugin_roots.add(normalized.rstrip("/"))

    plugin_roots.update({"library", "action_plugins"})

    for config_path, content in configs:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(content.decode("utf-8"))
        except (UnicodeDecodeError, configparser.Error):
            continue
        if parser.has_section("defaults"):
            for option in ("library", "action_plugins"):
                if parser.has_option("defaults", option):
                    add_roots(parser.get("defaults", option), posixpath.dirname(config_path) or ".")

    for env_name in (
        "ANSIBLE_LIBRARY",
        "ANSIBLE_ACTION_PLUGINS",
    ):
        add_roots(os.environ.get(env_name, ""))

    selected: list[File] = []
    for path, content in files.items():
        parsed = PurePosixPath(path)
        parts = parsed.parts
        is_config = parsed.name == "ansible.cfg"
        collection_root = next(
            (index for index, part in enumerate(parts) if part == _ANSIBLE_COLLECTION_PART and len(parts) > index + 3),
            None,
        )
        in_collection_plugins = (
            collection_root is not None and len(parts) > collection_root + 3 and parts[collection_root + 3] == "plugins"
        )
        in_role_plugin_dir = any(
            part == "roles" and len(parts) > index + 2 and parts[index + 2] in _ANSIBLE_PLUGIN_DIR_NAMES
            for index, part in enumerate(parts)
        )
        in_configured_root = any(
            root == "." or path == root or path.startswith(root.rstrip("/") + "/") for root in plugin_roots
        )
        is_plugin_source = parsed.suffix == ".py" and (
            in_collection_plugins or in_role_plugin_dir or in_configured_root
        )
        is_collection_metadata = collection_root is not None and (
            parts[collection_root + 3 :] == ("MANIFEST.json",)
            or parts[collection_root + 3 :] == ("meta", "runtime.yml")
        )
        if is_config or is_plugin_source or is_collection_metadata:
            selected.append(File(path=path, content=content))
    return selected


# Venv guard contention: when another session's install holds the
# generation write guard, re-queue with backoff instead of aborting the
# scan on first contention. Only guard-acquisition timeouts retry —
# a TimeoutError raised by the install worker itself re-raises at once.
_VENV_GUARD_ACQUIRE_RETRIES = 3
_VENV_GUARD_ACQUIRE_BACKOFF_S = 5.0


class _VenvWriteGuardTimeout(TimeoutError):
    """Raised when the venv write guard remains contended after retries."""


class _VenvReadGuardTimeout(TimeoutError):
    """Raised when the venv read guard remains contended during scan fan-out."""


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


def _normalized_session_galaxy_servers(
    galaxy_servers: Sequence[GalaxyServerDef],
) -> list[tuple[str, str, GalaxyServerDef]]:
    """Return session Galaxy servers with unique config section names.

    Args:
        galaxy_servers: Ordered scan-scoped Galaxy server definitions.

    Returns:
        Tuples of normalized section name, trimmed URL, and source proto.
    """
    seen_names: set[str] = set()
    entries: list[tuple[str, str, GalaxyServerDef]] = []
    for i, server in enumerate(galaxy_servers):
        url = (server.url or "").strip()
        if not url:
            continue
        base_name = (server.name or "").strip() or f"server_{i}"
        name = base_name
        suffix = 1
        while name.upper() in seen_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        seen_names.add(name.upper())
        entries.append((name, url, server))
    return entries


def _session_galaxy_server_payloads(
    galaxy_servers: Sequence[GalaxyServerDef],
) -> list[dict[str, object]] | None:
    """Build the request-scoped proxy payload from session Galaxy settings.

    Args:
        galaxy_servers: Ordered scan-scoped Galaxy server definitions.

    Returns:
        JSON-ready server configs, or ``None`` when no server URL was given.
    """
    payloads: list[dict[str, object]] = []
    for name, url, server in _normalized_session_galaxy_servers(galaxy_servers):
        payload: dict[str, object] = {
            "name": name,
            "url": url,
            "token": server.token,
            "auth_url": server.auth_url,
            "validate_certs": server.validate_certs if server.HasField("validate_certs") else None,
        }
        payloads.append(payload)
    return payloads or None


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

    configs: list[GalaxyServerConfig] = []
    for name, url, server in _normalized_session_galaxy_servers(galaxy_servers):
        configs.append(
            GalaxyServerConfig(
                name=name,
                url=url,
                token=server.token or None,
                auth_url=server.auth_url or None,
                validate_certs=server.validate_certs if server.HasField("validate_certs") else None,
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
    """Remove source duplicates while preserving distinct argspec diagnostics.

    Args:
        violations: List of violation dicts (may contain duplicates).

    Returns:
        Deduplicated list preserving first occurrence order.
    """
    seen: set[tuple[str, str, str | int | tuple[int, ...] | bool | None, str]] = set()
    out: list[ViolationDict] = []
    for v in violations:
        line: str | int | list[int] | tuple[int, ...] | bool | None = v.get("line")
        if isinstance(line, list | tuple):
            line = tuple(line)
        dedup_key = (str(v.get("rule_id", "")), str(v.get("file", "")), line, diagnostic_discriminator(v))
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
    from apme_engine.remediation.proposal_yaml import normalize_proposal_yaml  # noqa: PLC0415

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

        baseline = node.baseline_state()
        if baseline is None:
            continue
        v["original_yaml"] = normalize_proposal_yaml(baseline.yaml_lines)
        v["node_line_start"] = node.line_start

        if not fixed:
            continue

        approved = next(
            (s for s in reversed(node.progression) if s.approved),
            node.progression[-1],
        )
        v["fixed_yaml"] = normalize_proposal_yaml(approved.yaml_lines)

        this_rule = str(v.get("rule_id", ""))
        co_fixes = sorted(
            rec.key[1] for rec in node.violation_ledger.values() if rec.status == "fixed" and rec.key[1] != this_rule
        )
        if co_fixes:
            v["co_fixes"] = co_fixes  # type: ignore[assignment]


_FILE_FINDING_PATH = re.compile(r"^\$(?:\.|\[|$)")


def _is_file_finding(violation: Mapping[str, object]) -> bool:
    """Distinguish file diagnostics from findings on graph nodes.

    File diagnostics locate a position in uploaded source with a YAML path
    (``$``, ``$.driver.name``, ``$[0]``); graph findings reference a node ID.
    Matching the whole path shape keeps findings without a path from being
    mistaken for graph findings and then looked up as an empty node ID.

    Args:
        violation: Finding payload from a validator.

    Returns:
        True for source-position findings rather than graph-node findings.
    """
    return _FILE_FINDING_PATH.match(str(violation.get("path", ""))) is not None


def _filter_file_noqa(violations: list[ViolationDict]) -> list[ViolationDict]:
    """Apply source-local noqa to file findings after rule enforcement.

    Args:
        violations: Findings with original source excerpts and override metadata.

    Returns:
        Findings excluding unenforced, explicitly suppressed file diagnostics.
    """
    return [
        violation
        for violation in violations
        if not _is_file_finding(violation)
        or violation.get("_enforced")
        or str(violation.get("rule_id", "")).upper()
        not in parse_noqa(str(violation.get("original_yaml", "")).split("\n", 1)[0])
    ]


async def _refresh_session_file_violations(session: SessionState) -> None:
    """Reassess file findings against the session's currently applied bytes.

    The complete snapshot can also raise GraphTransportError during transport.

    Args:
        session: Session with the complete applied file snapshot.

    Raises:
        RequiredValidatorDependencyError: Native is unavailable or assessment fails.

    """
    address = os.environ.get("NATIVE_GRPC_ADDRESS")
    if not address:
        raise RequiredValidatorDependencyError("Required validator native not configured for file rescan")
    files = [File(path=path, content=content) for path, content in session.working_files.items()]
    result = await _call_validator(
        address,
        ValidateRequest(request_id=f"{session.session_id}-file-rescan", files=files),
        stream_large_request=True,
        fail_on_infra=True,
    )
    if result.error:
        raise RequiredValidatorDependencyError(f"Required validator native file rescan failed: {result.error}")
    configs, _complete = _scan_rule_audit_inputs(session.scan_options)
    findings = [v for v in result.violations if _is_file_finding(v)]
    findings = _apply_rule_configs(findings, configs)
    findings = _filter_file_noqa(_deduplicate_violations(_sort_violations(findings)))
    _attach_snippets(findings, files)
    session.file_violations = findings


async def _finish_pending_file_reassessment(session: SessionState) -> None:
    """Write pending patches and refresh findings before completing a session.

    Args:
        session: Session holding applied patches and the continuation to resume.
    """
    if session.temp_dir is not None and session.pending_file_patches:
        await asyncio.get_running_loop().run_in_executor(
            None, _write_patches_to_temp_dir, session.temp_dir, session.pending_file_patches
        )
    session.pending_file_patches = []
    await _refresh_session_file_violations(session)
    _sync_session_file_report(session)
    session.pending_file_reassessment = ""


def _unattached_key(violation: ViolationDict) -> tuple[str, str, object, str]:
    """Identify one run-level finding for merge decisions.

    Args:
        violation: Finding without a source position or graph node.

    Returns:
        Comparable identity for the finding's reported content.
    """
    line = violation.get("line")
    return (
        str(violation.get("rule_id", "")),
        str(violation.get("file", "")),
        tuple(line) if isinstance(line, list) else line,
        str(violation.get("message", "")),
    )


def _extend_with_unattached(remaining: list[ViolationDict], session: SessionState) -> None:
    """Add retained run-level findings that no other source reproduces.

    Args:
        remaining: Report findings assembled from the ledger and file state.
        session: Session retaining findings with neither a node nor a position.
    """
    present = {_unattached_key(item) for item in remaining}
    for item in session.unattached_violations:
        key = _unattached_key(item)
        if key in present:
            continue
        present.add(key)
        remaining.append(dict(item))


def _sync_session_file_report(session: SessionState) -> None:
    """Replace file findings in an existing report after applied changes.

    Args:
        session: Session with freshly reassessed file findings.
    """
    from apme_engine.remediation.partition import add_classification_to_violations, count_by_remediation_class

    remaining = [v for v in session.remaining_ai + session.remaining_manual if not _is_file_finding(v)]
    remaining.extend(dict(v) for v in session.file_violations)
    add_classification_to_violations(remaining)
    session.remaining_ai = [v for v in remaining if v.get("remediation_class") == RemediationClass.AI_CANDIDATE]
    session.remaining_manual = [v for v in remaining if v.get("remediation_class") != RemediationClass.AI_CANDIDATE]
    if session.report is not None:
        counts = count_by_remediation_class(remaining)
        session.report.remaining_ai = counts.get("ai-candidate", 0)
        session.report.remaining_manual = counts.get("manual-review", 0)
        del session.report.remaining_violations[:]
        session.report.remaining_violations.extend(violation_dict_to_proto(v) for v in remaining)


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
    from apme_engine.remediation.proposal_yaml import normalize_proposal_yaml  # noqa: PLC0415

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
            v["original_yaml"] = normalize_proposal_yaml(node.yaml_lines)
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
    from apme_engine.remediation.partition import add_classification_to_violations  # noqa: PLC0415

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
    *,
    stream_large_request: bool = False,
    fail_on_infra: bool = False,
) -> _ValidatorResult:
    """Call a validator over async gRPC; return violations + diagnostics.

    Args:
        address: gRPC address of the validator (e.g. localhost:50055).
        request: ValidateRequest to send.
        timeout: Request timeout in seconds (default 300 to accommodate
            collection health scanning of many large collections).
        stream_large_request: Send oversized requests through the validator's
            streaming endpoint instead of a unary gRPC call.
        fail_on_infra: Treat infrastructure findings as a failed assessment.

    Returns:
        _ValidatorResult with violations and optional diagnostics.

    Raises:
        GraphTransportError: The complete request exceeds its supported transport.
    """
    req_id = request.request_id or ""
    request_size = request.ByteSize()
    request_limit = _MAX_STREAMED_VALIDATE_REQUEST_BYTES if stream_large_request else _GRPC_MAX_MSG
    if request_size > request_limit:
        transport = "streamed" if stream_large_request else "unary"
        raise GraphTransportError(
            f"Validator request to {address} is {request_size} bytes, exceeding the {transport} request limit "
            f"({request_limit} bytes); validation is incomplete"
        )
    channel = grpc.aio.insecure_channel(
        address,
        options=[
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
        ],
    )
    stub = validate_pb2_grpc.ValidatorStub(channel)  # type: ignore[no-untyped-call]
    try:
        if stream_large_request and request_size > _GRPC_MAX_MSG:
            serialized_request = await asyncio.get_running_loop().run_in_executor(None, request.SerializeToString)

            async def request_chunks() -> AsyncIterator[ValidateRequestChunk]:
                for offset in range(0, len(serialized_request), _GRPC_STREAM_CHUNK_BYTES):
                    yield ValidateRequestChunk(
                        serialized_request_chunk=serialized_request[offset : offset + _GRPC_STREAM_CHUNK_BYTES]
                    )

            resp = await stub.ValidateStream(request_chunks(), timeout=timeout)
        else:
            resp = await stub.Validate(request, timeout=timeout)
        violations = [violation_proto_to_dict(v) for v in resp.violations]
        infra_errors = [
            v
            for v in violations
            if normalize_rule_id(str(v.get("rule_id", ""))) in {RULE_MISSING_VENV, RULE_VALIDATOR_FAILURE}
        ]
        if fail_on_infra and infra_errors:
            return _ValidatorResult(error=str(infra_errors[0].get("message") or "Validator infrastructure error"))
        return _ValidatorResult(
            violations=violations,
            diagnostics=resp.diagnostics if resp.HasField("diagnostics") else None,
            logs=list(resp.logs),
        )
    except grpc.RpcError as e:
        logger.error("Validator at %s failed (req=%s): %s", address, req_id, e)
        return _ValidatorResult(error=str(e))
    finally:
        await channel.close(grace=None)


def _validator_request(name: str, request: ValidateRequest) -> ValidateRequest:
    """Select complete inputs consumed by one built-in validator.

    Native and Gitleaks need files plus graph data; OPA only uses hierarchy
    data. Ansible needs all three for syntax and node checks. Collection health
    and dependency audit read the session venv. Never truncate a required input
    to make a request fit its transport limit.

    Args:
        name: Built-in validator name.
        request: Complete scan inputs and request metadata.

    Returns:
        A separate request retaining the validator's complete input fields.

    Raises:
        ValueError: The validator has no declared input requirements.
    """
    if name not in VALIDATOR_ENV_VARS:
        raise ValueError(f"Unknown validator input requirements: {name}")
    selected = ValidateRequest(
        request_id=request.request_id,
        project_root=request.project_root,
        ansible_core_version=request.ansible_core_version,
        collection_specs=request.collection_specs,
        session_id=request.session_id,
        venv_path=request.venv_path,
        dirty_node_ids=request.dirty_node_ids,
        graph_rule_opt_in=request.graph_rule_opt_in,
    )
    if name in {"native", "ansible", "gitleaks"}:
        selected.files.extend(request.files)
        selected.content_graph_data = request.content_graph_data
    if name in {"opa", "ansible"}:
        selected.hierarchy_payload = request.hierarchy_payload
    return selected


_REQUIREMENTS_PATHS = {"requirements.yml", "collections/requirements.yml"}


def _discover_collection_specs(files: Sequence[File]) -> tuple[list[str], list[str]]:
    """Extract collection specs from requirements.yml files in the uploaded file set.

    Looks for ``requirements.yml`` and ``collections/requirements.yml``.
    Parses the ``collections`` key and returns ``name[:version]`` strings.
    Bare string entries are stripped; duplicate bare names resolve
    last-wins so a later version override replaces an earlier pin.

    Version constraints (``>=1.0.0``, ``>=1.0.0,<2.0.0``, pinned ``1.2.3``)
    are passed through verbatim as ``name:version`` — never degraded to a
    bare name.  The sole exception is ``"*"`` (any version), which is
    equivalent to a bare name.  Downstream
    :func:`~apme_engine.venv_manager.venv_collections._spec_to_pip`
    translates the constraint into a PEP 440 pip specifier end-to-end.

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
            from apme_engine.git_requirements import is_git_requirement

            if is_git_requirement(entry):
                continue
            if isinstance(entry, str):
                spec = entry.strip()
                if not spec:
                    continue
                # Match validate_collection_spec's canonical form before the
                # spec is sent to the proxy, whose parser rejects inner spaces.
                spec = spec.replace(" ", "")
                bare = spec.split(":")[0].strip()
                if not bare:
                    continue
                specs[bare] = spec
            elif isinstance(entry, dict) and entry.get("name"):
                name = str(entry["name"]).strip()
                version = entry.get("version")
                version_text = str(version).strip().replace(" ", "") if version is not None else ""
                spec = f"{name}:{version_text}" if version_text and version_text != "*" else name
                specs[name] = spec
    return list(specs.values()), found_paths


def _has_git_collection_requirements(files: Sequence[File]) -> bool:
    """Check uploaded collection requirements for a Git source.

    Args:
        files: Uploaded project files.

    Returns:
        Whether a collection declares a Git source.
    """
    import yaml

    from apme_engine.git_requirements import git_entries

    for file in files:
        if file.path.replace("\\", "/").lstrip("/") not in _REQUIREMENTS_PATHS:
            continue
        try:
            data = yaml.safe_load(file.content.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError):
            continue
        if any(kind == "collections" for kind, _ in git_entries(data)):
            return True
    return False


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


class CollectionDependencyError(RuntimeError):
    """Required collection(s) could not be resolved or downloaded.

    Raised when the venv session reports failed collections, causing the
    scan to abort rather than continue with incomplete dependencies.
    """

    def __init__(self, failed_collections: list[str]) -> None:
        """Initialize with the list of collections that failed to install.

        Args:
            failed_collections: Collection specs that could not be installed.
        """
        self.failed_collections = failed_collections
        failed_list = ", ".join(failed_collections)
        super().__init__(
            f"Scan aborted: {len(failed_collections)} required collection(s) "
            f"could not be resolved or downloaded from configured Galaxy "
            f"servers: {failed_list}"
        )


def _scan_rule_audit_inputs(scan_opts: ScanOptions | None) -> tuple[list[object], bool]:
    """Extract the ADR-041 rule-catalog audit inputs from scan options.

    The completeness flag is forwarded independently of list emptiness: an
    empty catalog attested complete must still hard-fail the bidirectional
    audit (missing IDs) instead of silently scanning with the audit skipped.

    Args:
        scan_opts: Scan options from the client's first upload chunk, if any.

    Returns:
        Tuple of (rule configs, completeness flag).
    """
    if scan_opts is None:
        return [], False
    return list(scan_opts.rule_configs), scan_opts.rule_configs_complete


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
        norm = normalize_rule_id(rc.rule_id)  # type: ignore[attr-defined]
        if norm in config_map:
            logger.warning(
                "Duplicate normalized rule ID %r in rule_configs — last-wins; "
                "divergent enabled/severity flags resolve order-dependently",
                norm,
            )
        config_map[norm] = rc

    filtered: list[ViolationDict] = []
    for v in violations:
        rule_id = normalize_rule_id(str(v.get("rule_id", "")))
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


def _build_ai_policy_context(
    rule_configs: Sequence[object] | None,
    ansible_core_version: str = "",
    collection_specs: Sequence[str] | None = None,
) -> AIPolicyContext:
    """Assemble live policy for Tier 2 AI prompts from request context.

    Resolves per-rule severity/enabled overrides from ``RuleConfig``
    protos using the existing severity helpers (same mapping as
    :func:`_apply_rule_configs`), plus the target ansible-core version
    and collection pins from scan/fix options. The engine never fetches
    policy itself — this only passes through available scan/project
    context fields. Suppression hashes stay empty: suppressions live in
    the Gateway/CLI layer, which the engine cannot query (ADR-020).

    Args:
        rule_configs: Proto ``RuleConfig`` messages from ``ScanOptions``.
        ansible_core_version: Target ansible-core version (may be empty).
        collection_specs: Pinned collection specs (may be empty).

    Returns:
        ``AIPolicyContext`` with ``live_policy_available`` true when at
        least one live-policy signal resolved; unavailable (fail closed)
        when resolution yields nothing or raises.
    """
    try:
        from apme_engine.graph.severity import severity_from_proto, severity_to_label

        resolved: dict[str, str] = {}
        enabled: dict[str, bool] = {}
        for rc in rule_configs or []:
            norm = normalize_rule_id(str(getattr(rc, "rule_id", "") or ""))
            if not norm:
                continue
            enabled[norm] = bool(getattr(rc, "enabled", False))
            sev = int(getattr(rc, "severity", 0) or 0)
            if sev:
                resolved[norm] = severity_to_label(severity_from_proto(sev))
        pins = [str(s) for s in (collection_specs or []) if str(s)]
        core = str(ansible_core_version or "")
        available = bool(resolved or enabled or core or pins)
        return AIPolicyContext(
            resolved_severities=resolved,
            rule_enabled=enabled,
            suppression_hashes=[],
            ansible_core_version=core,
            collection_pins=pins,
            live_policy_available=available,
        )
    except Exception:
        logger.warning("AI policy resolution failed; failing closed to unavailable", exc_info=True)
        return AIPolicyContext()


_known_rule_ids: set[str] = set()

#: Bound on cached per-config Galaxy locks (LRU, least-recently-used
#: evicted first). Config identities are content hashes, but distinct
#: credential churn is still untrusted input — the cap stops unbounded
#: growth. See :meth:`EngineServicer._get_galaxy_proxy_cfg_lock_for`.
_MAX_GALAXY_CFG_LOCKS = 512


def _galaxy_cfg_content_key(galaxy_cfg_path: Path | None) -> str:
    """Return the lock-map key for a session Galaxy config.

    Keys by content hash (SHA-256 over the sorted server names + URLs),
    not the temp path: every session config is written to a unique temp
    path, so a path key would never repeat — sessions with identical
    credentials would never share a lock while the map grew one entry
    per session. Server entries are sorted before hashing so the same
    set in a different order still shares one lock. ``None`` (public
    Galaxy) maps to ``"__none__"``; an unreadable or unparsable path
    falls back to the raw file-bytes hash, then the path string, so
    locking never raises.

    Args:
        galaxy_cfg_path: Session-scoped ``ansible.cfg`` path, if any.

    Returns:
        Content-hash key (``"galaxy:<hex>"``), ``"__none__"``, or a
        fallback key when the file cannot be read.
    """
    import configparser
    import hashlib

    if galaxy_cfg_path is None:
        return "__none__"
    try:
        raw = Path(galaxy_cfg_path).read_bytes()
    except OSError:
        return str(galaxy_cfg_path)
    try:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(raw.decode("utf-8"))
        pairs = sorted(
            (name.partition("galaxy_server.")[2], cfg[name].get("url", ""))
            for name in cfg.sections()
            if name.startswith("galaxy_server.") and name != "galaxy_server."
        )
        if pairs:
            canonical = "\0".join(f"{name}\0{url}" for name, url in pairs)
            return f"galaxy:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    except (configparser.Error, UnicodeDecodeError, ValueError):
        pass
    return f"galaxy:{hashlib.sha256(raw).hexdigest()}"


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
    _galaxy_cfg_locks: BoundedCache[str, asyncio.Lock] | None = None

    def _get_venv_manager(self) -> VenvSessionManager:
        """Return (or create) the singleton VenvSessionManager.

        Returns:
            The shared VenvSessionManager instance.
        """
        if self._venv_mgr is None:
            self._venv_mgr = VenvSessionManager()
        return self._venv_mgr

    async def _acquire_venv_session_with_retries(
        self,
        *,
        sid: str,
        core_version: str,
        collection_specs: list[str] | None,
        galaxy_cfg_path: Path | None,
        galaxy_server_configs: list[dict[str, object]] | None = None,
        scan_id: str,
        ctx: contextvars.Context,
        acquire_fn: Callable[[dict[str, str] | None], VenvSession] | None = None,
    ) -> VenvSession:
        """Cold-build/install a venv with guard-contention retries (#5).

        Extracted from the scan pipeline so the contention branches are
        unit-testable: guard ``TimeoutError`` before the worker starts
        re-queues with backoff (up to ``_VENV_GUARD_ACQUIRE_RETRIES``),
        while a worker ``TimeoutError`` after the worker started fails
        at once (retrying would redo a failed install).

        Args:
            sid: Session identity for the venv.
            core_version: Ansible-core version pin.
            collection_specs: Collection specs for the install.
            galaxy_cfg_path: Session Galaxy config path (or None).
            galaxy_server_configs: Request-scoped Galaxy settings for the proxy.
            scan_id: Request ID for log lines.
            ctx: Context to run the blocking acquire under.
            acquire_fn: Optional override for the acquire call, receiving the
                Galaxy env snapshot (e.g. to install Git collection wheels).

        Returns:
            The acquired venv session.

        Raises:
            TimeoutError: On guard exhaustion or worker timeout.
            _VenvWriteGuardTimeout: When the guard remains contended after retries.
        """
        worker_started = False
        for _guard_attempt in range(_VENV_GUARD_ACQUIRE_RETRIES):
            try:
                async with (
                    # Wait for equivalent Galaxy configuration before
                    # occupying a per-venv write guard or global build slot.
                    self._activate_galaxy_proxy_config(galaxy_cfg_path) as galaxy_env,
                    venv_write_guard(sid, core_version),
                    limit_venv_builds() as release_on,
                ):
                    loop = asyncio.get_event_loop()

                    def _run_acquire() -> VenvSession:
                        if acquire_fn is not None:
                            return ctx.run(acquire_fn, galaxy_env)
                        return ctx.run(
                            self._get_venv_manager().acquire,
                            sid,
                            core_version,
                            collection_specs,
                            galaxy_env,
                            galaxy_server_configs,
                        )

                    acquire_future = loop.run_in_executor(None, _run_acquire)
                    # Hold the build slot until the worker finishes, even if cancelled.
                    release_on(cast(asyncio.Future[object], acquire_future))
                    worker_started = True
                    # Wait out the worker on cancellation (holds the
                    # write guard until the mutating thread finishes
                    # instead of splitting exclusion on cancel).
                    return await wait_out_worker(acquire_future)
            except TimeoutError:
                if worker_started:
                    # The install worker itself timed out — not guard
                    # contention. Fail at once; retrying would redo a
                    # failed install and mask the real error.
                    raise
                if _guard_attempt + 1 >= _VENV_GUARD_ACQUIRE_RETRIES:
                    raise _VenvWriteGuardTimeout(
                        f"Venv write guard stayed contended for session={sid} req={scan_id}"
                    ) from None
                logger.info(
                    "Venv write guard busy (session=%s req=%s); re-queueing install (%d/%d)",
                    sid,
                    scan_id,
                    _guard_attempt + 1,
                    _VENV_GUARD_ACQUIRE_RETRIES,
                )
                await asyncio.sleep(_VENV_GUARD_ACQUIRE_BACKOFF_S * (_guard_attempt + 1))
        raise _VenvWriteGuardTimeout(f"Venv write guard stayed contended for session={sid} req={scan_id}")

    def _get_galaxy_proxy_cfg_lock(self) -> asyncio.Lock:
        """Return the legacy process-wide Galaxy config lock.

        Retained for backward compatibility; new code scopes by config
        hash via :meth:`_get_galaxy_proxy_cfg_lock_for` so sessions with
        unrelated credentials do not serialize on one lock.

        Returns:
            Lock protecting process-wide Galaxy proxy config activation.
        """
        if self._galaxy_proxy_cfg_lock is None:
            self._galaxy_proxy_cfg_lock = asyncio.Lock()
        return self._galaxy_proxy_cfg_lock

    def _get_galaxy_proxy_cfg_lock_for(self, galaxy_cfg_path: Path | None) -> asyncio.Lock:
        """Return the lock for a Galaxy config identity.

        The key is a content hash (SHA-256 over the file bytes, which
        encode the sorted server names + URLs), **not** the temp path:
        session configs live at unique temp paths, so keying by path
        would never share — identical credentials would never serialize
        while the map grew without bound. ``None`` (no session config /
        public Galaxy) shares one lock.

        The map is a :class:`BoundedCache` capped at
        ``_MAX_GALAXY_CFG_LOCKS`` (512) with idle-aware eviction, so
        distinct credential churn cannot grow it without bound; only
        UNLOCKED entries are evicted (evicting a held lock would split
        mutual exclusion — a newcomer would share a fresh lock while the
        old holder still runs). When every entry is locked the map
        temporarily grows over cap.

        Args:
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` path, if any.

        Returns:
            Asyncio lock scoped to the config identity.
        """
        if self._galaxy_cfg_locks is None:
            self._galaxy_cfg_locks = BoundedCache(
                maxsize=_MAX_GALAXY_CFG_LOCKS,
                is_idle=lambda lk: not lk.locked(),
            )
        key = _galaxy_cfg_content_key(galaxy_cfg_path)
        return self._galaxy_cfg_locks.get_or_create(key, asyncio.Lock)

    def _clear_galaxy_cfg_locks(self) -> None:
        """Drop all cached Galaxy config locks (test helper)."""
        self._galaxy_cfg_locks = None

    def _galaxy_proxy_env_snapshot(self, galaxy_cfg_path: Path | None) -> dict[str, str]:
        """Build an env snapshot carrying the session Galaxy config.

        Does not mutate ``os.environ``. The snapshot is only passed to local
        install subprocesses; proxy preparation receives structured server
        settings in its authenticated request.

        Args:
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` path, if any.

        Returns:
            Copy of ``os.environ`` with ``ANSIBLE_CONFIG`` set when a
            session config is present.
        """
        env = dict(os.environ)
        if galaxy_cfg_path is not None:
            env["ANSIBLE_CONFIG"] = str(galaxy_cfg_path)
        return env

    @contextlib.asynccontextmanager
    async def _activate_galaxy_proxy_config(self, galaxy_cfg_path: Path | None) -> AsyncIterator[dict[str, str]]:
        """Provide a session-scoped Galaxy env snapshot without global mutation.

        Sessions with identical Galaxy configs serialize on a per-config-hash
        lock, while sessions with unrelated credentials can proceed in parallel.
        The proxy receives request-scoped server settings and never reads this
        snapshot from process-global state.

        Args:
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` path, if any.

        Yields:
            dict[str, str]: Env snapshot with ``ANSIBLE_CONFIG`` set when
            applicable. ``os.environ`` is left unchanged; pass the snapshot
            explicitly to local venv install subprocesses.
        """
        if galaxy_cfg_path is None:
            yield dict(os.environ)
            return

        async with self._get_galaxy_proxy_cfg_lock_for(galaxy_cfg_path):
            yield self._galaxy_proxy_env_snapshot(galaxy_cfg_path)

    # ── internal: reusable scan pipeline ──────────────────────────────

    async def _scan_pipeline(
        self,
        temp_dir: Path,
        files: list[File],
        scan_id: str,
        *,
        dependency_files: Mapping[str, bytes] | None = None,
        ansible_context_files: Sequence[File] = (),
        ansible_core_version: str = "",
        collection_specs: list[str] | None = None,
        include_scandata: bool = True,
        session_id: str = "",
        progress_callback: Callable[[str, str, float, int], None] | None = None,
        galaxy_cfg_path: Path | None = None,
        galaxy_server_configs: list[dict[str, object]] | None = None,
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
            dependency_files: Gateway-fetched Git dependency snapshot.
            ansible_context_files: Project config and local plugin files for Ansible.
            ansible_core_version: Ansible core version constraint.
            collection_specs: Collection specifiers (may be extended by discovery).
            include_scandata: Whether to include scandata in engine call.
            session_id: Client-provided session ID for venv reuse.
            progress_callback: Optional callback ``(phase, message, fraction)``
                for streaming per-validator progress to callers.
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` for Galaxy auth
                (ADR-045), passed to local install subprocesses.
            galaxy_server_configs: Request-scoped Galaxy settings sent to the
                authenticated proxy preparation endpoint.
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
                dependency_files=dependency_files,
                ansible_context_files=ansible_context_files,
                ansible_core_version=ansible_core_version,
                collection_specs=collection_specs,
                include_scandata=include_scandata,
                session_id=session_id,
                progress_callback=progress_callback,
                galaxy_cfg_path=galaxy_cfg_path,
                galaxy_server_configs=galaxy_server_configs,
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
        dependency_files: Mapping[str, bytes] | None = None,
        ansible_context_files: Sequence[File] = (),
        ansible_core_version: str = "",
        collection_specs: list[str] | None = None,
        include_scandata: bool = True,
        session_id: str = "",
        progress_callback: Callable[[str, str, float, int], None] | None = None,
        galaxy_cfg_path: Path | None = None,
        galaxy_server_configs: list[dict[str, object]] | None = None,
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
            dependency_files: Gateway-fetched Git dependency snapshot.
            ansible_context_files: Project config and local plugin files for Ansible.
            ansible_core_version: Ansible core version constraint.
            collection_specs: Collection specifiers (may be extended by discovery).
            include_scandata: Whether to include scandata in engine call.
            session_id: Client-provided session ID for venv reuse.
            progress_callback: Optional callback ``(phase, message, fraction)``
                for streaming per-validator progress to callers.
            galaxy_cfg_path: Session-scoped ``ansible.cfg`` for Galaxy auth
                (ADR-045), passed to local install subprocesses.
            galaxy_server_configs: Request-scoped Galaxy settings sent to the
                authenticated proxy preparation endpoint.
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
            CollectionDependencyError: If one or more required collections
                could not be installed into the session venv.
            _VenvReadGuardTimeout: If a concurrent venv install keeps the
                scan fan-out read guard unavailable past its timeout.
            RequiredValidatorDependencyError: If a required validator
                (native, opa, ansible) is not configured or its RPC fails.
            GraphTransportError: A validator's complete input cannot be transported.
            ValueError: If ``rule_configs_complete`` is ``True`` and either
                direction of the bidirectional audit fails (unknown IDs the
                Engine cannot execute, or known IDs absent from the config).

        Returns:
            Same tuple as ``_scan_pipeline``.
        """
        from apme_engine.daemon.git_dependencies import build_git_collection_wheels, install_git_dependencies
        from apme_engine.validators.ansible._venv import DEFAULT_VERSION
        from apme_engine.venv_manager.session import _venv_site_packages
        from apme_engine.venv_manager.venv_collections import LocalCollectionWheel, _requirements_hash

        scan_t0 = time.monotonic()
        collection_specs = list(collection_specs or [])

        core_version = ansible_core_version or DEFAULT_VERSION
        sid = session_id or uuid.uuid4().hex[:12]

        git_collections: set[str] = set()
        git_wheels: dict[str, LocalCollectionWheel] | None = None

        def acquire_scoped(venv_id: str, specs: list[str], galaxy_env: dict[str, str] | None = None) -> VenvSession:
            nonlocal git_wheels
            manager = self._get_venv_manager()
            if git_collections and dependency_files:
                if git_wheels is None:
                    git_wheels = build_git_collection_wheels(dependency_files)
                return manager.acquire(
                    venv_id, core_version, specs, galaxy_env, galaxy_server_configs, local_wheels=git_wheels
                )
            return manager.acquire(venv_id, core_version, specs, galaxy_env, galaxy_server_configs)

        if dependency_files:
            git_collections = {
                ".".join(path.split("/")[1:3]) for path in dependency_files if path.startswith("ansible_collections/")
            }
            snapshot_prefix = "-git-wheel-" if git_collections else "-git-"
            sid += snapshot_prefix + engine_upload._hash_upload_files(dependency_files)[:16]
            discovered_specs, _ = _discover_collection_specs(files)
            initial_specs = merge_collection_specs(collection_specs, discovered_specs, [])
            initial_specs = [spec for spec in initial_specs if spec.split(":")[0] not in git_collections]
            initial_specs.extend(sorted(git_collections))
            git_base_sid = sid
            sid = f"{git_base_sid}-{_requirements_hash(initial_specs)[:16]}"
            loop = asyncio.get_running_loop()
            git_venv = await loop.run_in_executor(
                None,
                self._get_venv_manager().peek_warm,
                sid,
                core_version,
                initial_specs,
                galaxy_server_configs,
            )
            if git_venv is None:
                # Bind the Galaxy env snapshot for this acquisition too: the
                # dependency venv installs collections that may live on a
                # private Galaxy, and discarding the snapshot made this call
                # fall back to public galaxy.ansible.com (401/404 for private
                # requirements) even though the config was resolved above.
                async with (
                    # Config lock before the global build slot, matching
                    # ``_acquire_venv_session_with_retries``: the reverse
                    # order lets a normal scan hold the config lock while
                    # waiting for the slot a git-dependency scan holds while
                    # waiting for that same config lock (deadlock until the
                    # semaphore timeout).
                    self._activate_galaxy_proxy_config(galaxy_cfg_path) as git_galaxy_env,
                    limit_venv_builds() as release_on,
                ):
                    acquire_future = loop.run_in_executor(None, acquire_scoped, sid, initial_specs, git_galaxy_env)
                    release_on(cast(asyncio.Future[object], acquire_future))
                    git_venv = await asyncio.shield(acquire_future)
            if git_venv.failed_collections:
                raise CollectionDependencyError(git_venv.failed_collections)
            await asyncio.get_running_loop().run_in_executor(
                None, install_git_dependencies, git_venv.venv_root, dependency_files
            )

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
            # File validators still assess configuration-only snapshots.
            context_obj.hierarchy_payload = {"hierarchy": []}

        # 2. Collection discovery
        discovered, requirements_found = _discover_collection_specs(files)
        if not dependency_files and _has_git_collection_requirements(files):
            logger.warning(
                "Git collection requirements were skipped because no dependency snapshot was supplied (req=%s)", scan_id
            )
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
        collection_specs = [spec for spec in collection_specs if spec.split(":")[0] not in git_collections]
        collection_specs.extend(sorted(git_collections))
        logger.info("Collection specs merged (req=%s): %s", scan_id, collection_specs)
        if dependency_files:
            # A changed Galaxy request gets a distinct venv, so incremental pip
            # installs cannot overwrite a Git snapshot already used by readers.
            sid = f"{git_base_sid}-{_requirements_hash(collection_specs)[:16]}"

        # 3. Venv acquire (always — creates or incrementally installs).
        # PE-20: cap concurrent pip/galaxy builds across sessions so one
        # unauthenticated client cannot starve scans. Warm hits never take
        # a build slot: peek first without the semaphore, and only take a
        # slot when a cold build or incremental install is actually needed
        # (acquire re-checks under the same file lock, so the peek cannot
        # race a concurrent build into an inconsistent venv).
        # peek_warm installs nothing, so it runs outside the Galaxy env
        # snapshot; the build slot is acquired before snapshotting the
        # config so a session never waits up to _VENV_BUILD_WAIT_S while
        # holding its per-config Galaxy lock. Lock ordering is generation
        # write guard -> build semaphore -> per-config Galaxy lock
        # (outermost first): a cold build blocked on another session's
        # install waits holding no scarce slots. Taking the semaphore
        # first would pin one of only
        # APME_SESSION_MAX_CONCURRENT_VENV_BUILDS slots (and the Galaxy
        # lock) while blocked on the generation lock, starving unrelated
        # sessions. The generation guard itself fails fast after its own
        # timeout instead of waiting forever. The snapshot is passed
        # explicitly to the acquire downloader (per-call env in the
        # executor thread) — os.environ is never mutated, so concurrent
        # sessions cannot observe each other's Galaxy credentials via the
        # in-process proxy. Sessions sharing one config identity serialize
        # on a per-hash lock; unrelated creds proceed concurrently.
        # #7 wedge verdict (evidence, not reorder): no lock-order wedge
        # exists here. Generation guards are keyed per
        # ``(session_id, core_version)`` (venv_manager/session.py:
        # ``get_generation_lock``), so distinct sids never contend on the
        # write/read guards; and the write guard above is released when
        # its ``async with`` exits, strictly before the validator fan-out
        # takes ``venv_read_guard(sid, core_version)`` below — the two
        # guards are sequential, never nested. Both guards carry bounded
        # timeouts (default 60s) and the semaphore wait is bounded by
        # ``APME_SESSION_VENV_BUILD_WAIT_S``, so even a hung peer fails
        # fast instead of wedging. See
        # tests/test_galaxy_env_isolation.py::test_concurrent_public_galaxy_no_read_wedge.
        venv_session = await asyncio.get_event_loop().run_in_executor(
            None,
            ctx.run,
            self._get_venv_manager().peek_warm,
            sid,
            core_version,
            collection_specs,
            galaxy_server_configs,
        )
        if venv_session is None:
            venv_session = await self._acquire_venv_session_with_retries(
                sid=sid,
                core_version=core_version,
                collection_specs=collection_specs,
                galaxy_cfg_path=galaxy_cfg_path,
                galaxy_server_configs=galaxy_server_configs,
                scan_id=scan_id,
                ctx=ctx,
                acquire_fn=lambda galaxy_env: acquire_scoped(sid, collection_specs, galaxy_env),
            )
        # Release the built Git wheel payloads (potentially large in memory)
        # once the final acquire has used them.
        git_wheels = None
        venv_path = str(venv_session.venv_root)
        if venv_session.failed_collections:
            logger.error(
                "collection_dependency_failed session=%s req=%s failed_collections=%s outcome=scan_aborted",
                sid,
                scan_id,
                ", ".join(venv_session.failed_collections),
            )
            raise CollectionDependencyError(venv_session.failed_collections)
        if dependency_files:
            await asyncio.get_running_loop().run_in_executor(
                None, install_git_dependencies, venv_session.venv_root, dependency_files
            )
            if _requirements_hash(collection_specs) != _requirements_hash(initial_specs):
                # Hierarchy discovery selected a different environment. Rebuild
                # against its complete dependencies before serializing the graph.
                dependency_dir = str(_venv_site_packages(venv_session.venv_root))
                context_obj = await asyncio.get_running_loop().run_in_executor(
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
                    context_obj.hierarchy_payload = {"hierarchy": []}
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
                    lambda: _serialize_graph_transport(cg),
                )
                logger.debug(
                    "ContentGraph serialized: %d bytes (req=%s)",
                    len(content_graph_data),
                    scan_id,
                )

        hierarchy_data = await asyncio.get_event_loop().run_in_executor(
            None, _bounded_transport_json, context_obj.hierarchy_payload
        )
        validate_request = ValidateRequest(
            request_id=scan_id,
            project_root="",
            files=files,
            hierarchy_payload=hierarchy_data,
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
        task_coros: list[Awaitable[_ValidatorResult]] = []
        for name, addr in validator_targets:
            request = _validator_request(name, validate_request)
            if name == "ansible" and ansible_context_files:
                files_by_path: dict[str, File] = {file.path: file for file in files}
                files_by_path.update({file.path: file for file in ansible_context_files})
                del request.files[:]
                request.files.extend(files_by_path.values())
            task_coros.append(
                _call_validator(
                    addr,
                    request,
                    stream_large_request=name in {"native", "ansible"},
                    fail_on_infra=name == "native",
                )
            )

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

            # Hold the generation read lock for the whole fan-out so an
            # incremental install (write side) cannot mutate the venv an
            # in-flight validator is executing against (N15). This is taken
            # strictly after the venv-acquire write guard above was
            # released (sequential, never nested), and guards are keyed per
            # (session_id, core_version) — distinct sids do not contend
            # (see the #7 wedge verdict comment at venv acquisition).
            try:
                async with venv_read_guard(sid, core_version):
                    named_results = await asyncio.gather(
                        *[_run_validator(n, c) for n, c in zip(task_names, task_coros, strict=True)],
                        return_exceptions=True,
                    )
            except TimeoutError as exc:
                raise _VenvReadGuardTimeout(
                    f"Venv read guard stayed contended for session={sid} req={scan_id}"
                ) from exc
            fan_out_ms = (time.monotonic() - fan_t0) * 1000

            counts: dict[str, int] = {}
            for vname, item in zip(task_names, named_results, strict=True):
                if isinstance(item, BaseException):
                    logger.error("Validator %s raised (req=%s): %s", vname, scan_id, item)
                    if isinstance(item, GraphTransportError):
                        raise GraphTransportError(str(item)) from item
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
        if rule_configs or rule_configs_complete:
            configs = rule_configs if rule_configs is not None else []
            unknown, missing, divergent = _validate_rule_configs(configs, complete=rule_configs_complete)
            if rule_configs_complete:
                # ADR-041 §5: the Gateway path performs a bidirectional audit
                # and hard-fails on unknown *or* missing rule IDs so catalog
                # skew forces upgrade completion instead of scanning silently.
                # (Relaxing this to degraded-continue needs an ADR-041
                # amendment plus a client-visible degraded flag — tracked.)
                # Divergent repeats fail closed the same way: conflicting
                # enabled/severity flags for one rule would otherwise resolve
                # by row order while the audit attests in-sync.
                errors: list[str] = []
                if unknown:
                    errors.append(f"unknown rule IDs: {unknown}")
                if missing:
                    errors.append(f"missing rule IDs (known to this engine but absent from config): {missing}")
                if divergent:
                    errors.append(
                        f"divergent rule configs for {divergent}: same rule with conflicting "
                        "enabled/severity/enforced flags — refusing to guess"
                    )
                if errors:
                    raise ValueError(
                        f"Rule catalog mismatch (bidirectional audit): {'; '.join(errors)}. "
                        "The Gateway catalog is out of sync with this engine."
                    )
            elif unknown or divergent:
                logger.warning(
                    "rule_configs references unknown rule IDs (scan=%s): %s — ignoring; divergent repeats: %s",
                    scan_id,
                    unknown,
                    divergent,
                )
            violations = _apply_rule_configs(violations, configs)
        violations = _filter_file_noqa(violations)
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
        specified_fqcns = {s.split(":")[0] for s in discovered} | git_collections
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
    def _format_files(files: list[File]) -> _FormatBatchResult:
        """Format YAML files and return diffs plus per-file skip diagnostics.

        Per-file failures are skipped so one unformattable file cannot abort
        the FixSession format phase. Callers must surface ``skipped`` so
        dump/load failures are not presented as "already formatted."

        Args:
            files: File protos to format.

        Returns:
            ``_FormatBatchResult`` with changed-file diffs and skipped paths.
        """
        from apme_engine.engine.finder import role_roots_from_paths, taskfile_source_context
        from apme_engine.formatter import format_content

        diffs: list[FileDiff] = []
        skipped: list[tuple[str, str]] = []
        role_roots = role_roots_from_paths([file.path for file in files])
        for f in files:
            if not f.path.endswith((".yml", ".yaml")):
                continue
            try:
                text = f.content.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("Skipping format for %s: not valid UTF-8", f.path)
                skipped.append((f.path, "not valid UTF-8"))
                continue
            try:
                context = taskfile_source_context(f.path, role_roots=role_roots)
                result = format_content(text, filename=f.path, source_context=context)
            except Exception as exc:
                # Defense in depth: one unformattable file must not abort the scan.
                reason = f"unexpected error: {exc}"
                logger.warning("Skipping format for %s: %s", f.path, reason, exc_info=True)
                skipped.append((f.path, reason))
                continue
            if result.error:
                logger.warning("Skipping format for %s: %s", f.path, result.error)
                skipped.append((f.path, result.error))
                continue
            if result.changed:
                diffs.append(
                    FileDiff(
                        path=f.path,
                        original=f.content,
                        formatted=result.formatted.encode("utf-8"),
                        diff=result.diff,
                    )
                )
        return _FormatBatchResult(diffs=diffs, skipped=skipped)

    @staticmethod
    async def _accumulate_chunks(
        request_stream: AsyncIterator[ScanChunk],
    ) -> tuple[list[File], str, str, ScanOptions | None, FixOptions | None]:
        """Drain a ScanChunk stream into accumulated state.

        File paths use the same shared canonicalizer as FixSession uploads
        and the Gateway WebSocket upload
        (:func:`apme_engine.daemon.fs_utils.canonicalize_upload_entries`), so
        one payload is accepted-or-rejected identically on every ingress path
        (a path escaping the session root, or two distinct raw paths that
        collapse onto one canonical key, raises ``ValueError`` here exactly as
        on upload).

        Args:
            request_stream: Async iterator of ScanChunk messages.

        Returns:
            Tuple of (files, scan_id, project_root, scan_options, fix_options).

        Raises:
            ValueError: If a file path escapes the session root, if two
                distinct raw paths collapse onto one canonical key, or if the
                per-session aggregate upload caps
                (``APME_SESSION_MAX_UPLOAD_BYTES`` /
                ``APME_SESSION_MAX_UPLOAD_FILES``) would be exceeded — the
                same admission policy as FixSession uploads.
        """  # noqa: DOC502 -- the raise lives in canonicalize_upload_entries
        all_files: list[File] = []
        raw_provenance: dict[str, str] = {}
        total_bytes = 0
        max_files = engine_upload._SESSION_MAX_UPLOAD_FILES
        max_bytes = engine_upload._SESSION_MAX_UPLOAD_BYTES
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
            # One chunk may repeat a path (e.g. `a.yml` + `./a.yml` normalize
            # alike): last copy wins, accounted once — the same shared
            # canonicalize+dedup+collision check as FixSession
            # `_session_upload_append`, so one payload is accepted-or-rejected
            # identically on every ingress.
            # Raw-path provenance spans chunks (see
            # ``plan_upload_raw_path_provenance``): per-chunk canonicalization
            # alone would let ``a\\b.yml`` in one chunk and ``a/b.yml`` in the
            # next each pass, with the later file silently replacing the
            # earlier one in the accumulated set.
            planned_provenance = engine_upload.plan_upload_raw_path_provenance(
                raw_provenance,
                [f.path for f in chunk.files],  # type: ignore[attr-defined]
            )
            staged_files = [
                File(path=canonical_path, content=content)
                for canonical_path, content in canonicalize_upload_entries(
                    [(f.path, f.content) for f in chunk.files]  # type: ignore[attr-defined]
                )
            ]
            for normalized in staged_files:
                existing = next(
                    (i for i, ef in enumerate(all_files) if ef.path == normalized.path),
                    None,
                )
                if existing is None:
                    all_files.append(normalized)
                    total_bytes += len(normalized.content)
                else:
                    total_bytes += len(normalized.content) - len(all_files[existing].content)
                    all_files[existing] = normalized
                if len(all_files) > max_files:
                    raise ValueError(f"Upload file limit exceeded: {len(all_files)} files (max {max_files})")
                if total_bytes > max_bytes:
                    raise ValueError(f"Upload size limit exceeded: {total_bytes} bytes (max {max_bytes} bytes)")
            # Commit raw-path provenance only once this chunk passed the
            # per-file caps, so a rejected chunk leaves nothing behind.
            raw_provenance.update(planned_provenance)
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
            # Same shared canonicalizer as every other ingress: one payload is
            # accepted-or-rejected identically on all RPCs. Escaping or
            # colliding paths abort INVALID_ARGUMENT exactly as on FixSession
            # uploads.
            try:
                normalized = [
                    File(path=canonical_path, content=content)
                    for canonical_path, content in canonicalize_upload_entries(
                        [(f.path, f.content) for f in request.files]  # type: ignore[attr-defined]
                    )
                ]
            except ValueError as ve:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(ve))
            ctx = contextvars.copy_context()
            batch = await asyncio.get_event_loop().run_in_executor(
                None,
                ctx.run,
                lambda: self._format_files(normalized),
            )
            dur = (time.monotonic() - t0) * 1000
            logger.info(
                "Format: done (%.0fms, %d files changed, %d skipped)",
                dur,
                len(batch.diffs),
                len(batch.skipped),
            )
            return FormatResponse(diffs=batch.diffs, logs=sink.entries)

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
        try:
            all_files, scan_id, *_ = await self._accumulate_chunks(request_stream)
        except ValueError as ve:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(ve))
        with attach_collector() as sink:
            logger.info("FormatStream: start (%d files, req=%s)", len(all_files), scan_id)
            t0 = time.monotonic()
            ctx = contextvars.copy_context()
            batch = await asyncio.get_event_loop().run_in_executor(
                None,
                ctx.run,
                lambda: self._format_files(all_files),
            )
            dur = (time.monotonic() - t0) * 1000
            logger.info(
                "FormatStream: done (%.0fms, %d files changed, %d skipped, req=%s)",
                dur,
                len(batch.diffs),
                len(batch.skipped),
                scan_id,
            )
            return FormatResponse(diffs=batch.diffs, logs=sink.entries)

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
            asyncio.CancelledError: Re-raised after cleaning up upload-created
                sessions on client disconnect.
            ValueError: Aborts INVALID_ARGUMENT on bad uploads (escaping
                paths, over-cap streams, post-seal protocol violations);
                sealed sessions survive for approve/resume.
            Exception: Propagates unexpected errors after logging.

        Note:
            ``CollectionDependencyError`` is caught and mapped to gRPC
            ``FAILED_PRECONDITION`` so clients receive an actionable status
            instead of ``UNKNOWN``.
        """
        store = self._get_session_store()
        session: SessionState | None = None
        upload_created_session_id: str | None = None
        scan_id = ""

        def _drop_unsealed_created() -> None:
            """Remove upload-created sessions that never completed processing."""
            if upload_created_session_id:
                created = store.get(upload_created_session_id)
                if created is None or not created.upload_sealed:
                    store.remove(upload_created_session_id)

        try:
            async for cmd in request_stream:
                oneof = cmd.WhichOneof("command")
                if (
                    session is not None
                    and session.approval_in_flight
                    and oneof in {"approve", "begin_remediate", "ai_escalate", "upload", "close"}
                ):
                    yield SessionEvent(
                        error=SessionError(
                            code="approval_in_progress",
                            recoverable=True,
                            message="An approval is already in progress; retry after it finishes",
                        )
                    )
                    continue

                if oneof == "upload":
                    chunk: ScanChunk = cmd.upload
                    if session is None:
                        # First upload chunk — start accumulating
                        session, scan_id = await self._session_upload_start(
                            store,
                            chunk,
                        )
                        upload_created_session_id = session.session_id
                        yield SessionEvent(
                            created=SessionCreated(
                                session_id=session.session_id,
                                ttl_seconds=session.ttl_seconds,
                                operation_budget_seconds=session.operation_budget_s,
                            ),
                        )

                    if chunk.last and session.upload_sealed:
                        if engine_upload._hash_upload_chunk(chunk) == session.upload_sealed_chunk_hash:
                            logger.warning(
                                "FixSession: duplicate terminal upload chunk ignored (session_id=%s, scan_id=%s)",
                                session.session_id,
                                scan_id,
                            )
                            async for event in self._session_replay_state(session):
                                yield event
                            continue
                        raise ValueError(
                            "Retried terminal upload chunk carries different file bytes than the "
                            "sealed upload — replaying it would silently drop the corrected files. "
                            "Start a new session for corrected files; the sealed session is kept "
                            "for approve/resume."
                        )

                    engine_upload._session_upload_append(session, chunk)
                    session.touch()

                    if chunk.last:
                        session.upload_sealed = True
                        session.upload_sealed_hash = engine_upload._hash_upload_files(session.original_files)
                        session.upload_sealed_chunk_hash = engine_upload._hash_upload_chunk(chunk)
                        peer = context.peer()
                        logger.info(
                            "FixSession: processing %d file(s) (session_id=%s, scan_id=%s, peer=%s)",
                            len(session.original_files),
                            session.session_id,
                            scan_id,
                            peer,
                        )

                        assert session is not None
                        bound_session = session

                        def _unseal_after_failure(_s: SessionState = bound_session) -> None:
                            _s.upload_sealed = False
                            _s.upload_sealed_hash = ""
                            _s.upload_sealed_chunk_hash = ""
                            _s.reset_partial_run_state()

                        process_failed = False
                        try:
                            async for event in self._session_process(session, scan_id):
                                if event.WhichOneof("event") == "error":
                                    process_failed = True
                                yield event
                        except Exception:
                            # Processing failed: unseal so a retried terminal
                            # chunk re-runs instead of replaying partial state.
                            # (Cancellation takes the disconnect-cleanup path,
                            # which removes the session outright.)
                            _unseal_after_failure()
                            raise
                        if process_failed:
                            _unseal_after_failure()

                elif oneof == "approve":
                    if session is None:
                        continue
                    session.touch()
                    approved = set(cmd.approve.approved_ids)
                    async for event in self._session_handle_approval(
                        session, approved, approval_token=cmd.approve.approval_token
                    ):
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
                    if upload_created_session_id and (
                        session is None or upload_created_session_id != session.session_id
                    ):
                        store.remove(upload_created_session_id)
                    upload_created_session_id = None
                    yield SessionEvent(closed=SessionClosed())
                    return

        except asyncio.CancelledError:
            # Discard incomplete uploads/processing, but retain sealed approval
            # continuations and stable gates so resume cannot lose committed work
            # or race cleanup against an owned writer still finishing its phase.
            if upload_created_session_id:
                created = store.get(upload_created_session_id)
                resumable = (
                    created is not None
                    and created.upload_sealed
                    and (created.approval_in_flight or created.status in {1, 3, 4})
                )
                if not resumable:
                    store.remove(upload_created_session_id)
            raise
        except ResourceExhaustedError as e:
            _drop_unsealed_created()
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(e))
        except (_VenvWriteGuardTimeout, _VenvReadGuardTimeout) as e:
            _drop_unsealed_created()
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"Venv is busy; retry the scan: {e}")
        except CollectionDependencyError as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        except RequiredValidatorDependencyError as e:
            _drop_unsealed_created()
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
        except ValueError as ve:
            # The stream is dead: drop sessions this stream created so a
            # rejected upload cannot squat a slot until TTL reap (PE-35 caps
            # stop RAM blowup; this stops session-table exhaustion).
            # A sealed (already processed, approval-capable) session survives:
            # the client can resume/approve it on a fresh stream instead of
            # losing it to a stray post-seal chunk.
            _drop_unsealed_created()
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

        # Same normalization as _session_upload_append so materialized files
        # always match original_files keys.
        all_files = [
            File(path=engine_upload._normalize_upload_path(p), content=c) for p, c in session.working_files.items()
        ]

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

        galaxy_server_configs = _session_galaxy_server_payloads(galaxy_servers)

        scan_rule_configs, scan_rule_configs_complete = _scan_rule_audit_inputs(scan_opts)

        skip_validators: set[str] = set()
        if scan_opts:
            if scan_opts.skip_collection_health:
                skip_validators.add("collection_health")
            if scan_opts.skip_dep_audit:
                skip_validators.add("dep_audit")

        if galaxy_servers:
            # Retry after reset_partial_run_state() may still hold the prior
            # config path; remove it so token-bearing temp dirs are not orphaned.
            session._cleanup_galaxy_cfg()
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
        format_batch = format_result
        format_diffs = format_batch.diffs
        session.format_diffs = list(format_diffs)

        if format_batch.skipped:
            _fmt_skip = ProgressUpdate(
                message=(
                    f"Skipped formatting {len(format_batch.skipped)} file(s): "
                    + "; ".join(f"{p} ({r})" for p, r in format_batch.skipped[:5])
                    + ("…" if len(format_batch.skipped) > 5 else "")
                ),
                phase="format",
                level=3,  # WARNING
            )
            session.record_progress(task_linked=is_task_linked_progress(_fmt_skip.phase))
            self._stamp_progress_update(_fmt_skip, session)
            session.progress_logs.append(_fmt_skip)
            yield SessionEvent(progress=_fmt_skip)

        formatted_files: list[File] = list(all_files)
        format_map: dict[str, bytes] = {d.path: d.formatted for d in format_diffs}
        format_skipped_paths = {p for p, _ in format_batch.skipped}

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

        # Phase 2: Idempotency check (exclude files skipped on the first pass —
        # they were never reformatted, so a second-pass skip is not an
        # idempotency failure; report them separately below).
        idem_inputs = [f for f in formatted_files if f.path not in format_skipped_paths]
        idem_result = await self._supervise_executor(
            session,
            self._format_files,
            idem_inputs,
        )
        if isinstance(idem_result, SessionEvent):
            yield idem_result
            return
        idem_batch = idem_result
        idem_diffs = idem_batch.diffs
        session.idempotency_ok = len(idem_diffs) == 0 and not idem_batch.skipped
        if not session.idempotency_ok:
            _idem_parts = []
            if idem_diffs:
                _idem_parts.append("Formatter is not idempotent on this input")
            if idem_batch.skipped:
                _idem_parts.append(
                    f"Idempotency check skipped {len(idem_batch.skipped)} file(s) that formatted on the first pass"
                )
            _idem_warn = ProgressUpdate(
                message="; ".join(_idem_parts),
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
            supplied_paths = {file.path for file in rel_files}
            rel_files.extend(file for file in formatted_files if file.path not in supplied_paths)
            (
                violations,
                session_diag,
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
                dependency_files=session.dependency_files,
                ansible_context_files=_ansible_context_files(session.working_files),
                ansible_core_version=ansible_core_version,
                collection_specs=collection_specs,
                session_id=fix_session_id,
                progress_callback=_progress_callback,
                galaxy_cfg_path=session.galaxy_cfg_path,
                galaxy_server_configs=galaxy_server_configs,
                rule_configs=scan_rule_configs or None,
                rule_configs_complete=scan_rule_configs_complete,
                skip_validators=frozenset(skip_validators),
            )

            # First full-project pass owns the aggregate diagnostics surfaced on
            # SessionResult / FixCompletedEvent (issue #512); later convergence
            # passes only re-scan mutated files.
            if session.scan_diagnostics is None and session_diag is not None:
                session.scan_diagnostics = session_diag

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
            rule_configs: Per-rule overrides for validator results and opt-in
                audit GraphRules, applied consistently to scans and rescans.

        Yields:
            SessionEvent: Progress, Tier1Summary, and result events.
        """
        from apme_engine.engine.graph_opa_payload import content_nodes_to_opa_dicts
        from apme_engine.graph.content_graph import ContentGraph, EdgeType, NodeType
        from apme_engine.graph.scanner import (
            graph_rule_opt_in_from_rule_configs,
            load_graph_rules,
            native_rules_dir,
        )
        from apme_engine.remediation.graph_engine import (
            GraphRemediationEngine,
            RescanResult,
            splice_with_outcome,
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

        # File findings have source/YAML paths rather than graph node IDs.
        # A graph-only rescan cannot establish that they have been fixed.
        session.file_violations = [dict(v) for v in initial_violations if _is_file_finding(v)]
        initial_violations = [v for v in initial_violations if not _is_file_finding(v)]
        # Findings that locate neither a source position nor a node describe the
        # run itself (e.g. skipped checks). Neither the graph ledger nor a file
        # rescan can reproduce them, so they are retained for every report.
        session.unattached_violations = [dict(v) for v in initial_violations if not str(v.get("path", ""))]
        initial_violations = [v for v in initial_violations if str(v.get("path", ""))]

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
        ) -> RescanResult:
            """Rescan dirty nodes via gRPC to all configured validators.

            Native receives the full (slim) graph with ``dirty_node_ids``
            so rules can traverse context beyond the dirty set.  OPA,
            Ansible, and Gitleaks receive node-scoped payloads.  All
            return violations with ``path`` set to ``node_id``.

            Args:
                g: ContentGraph (may have been mutated by transforms).
                dirty_ids: Node IDs that changed since the last pass.

            Returns:
                Merged findings and independent assessment coverage.

            Raises:
                RequiredValidatorDependencyError: If a required validator
                    (native, opa, ansible) is not configured or its RPC fails.
                GraphTransportError: A validator's complete input cannot be transported.
            """
            from apme_engine.argspec_coverage import ARGSPEC_COVERAGE_KEY, decode_argspec_coverage

            all_violations: list[ViolationDict] = []
            # No response, old validators and malformed metadata cannot prove
            # argument findings fixed. Only Ansible can establish this coverage.
            unassessed = decode_argspec_coverage(None)

            dirty_nodes = [node for nid in sorted(dirty_ids) if (node := g.get_node(nid)) is not None]
            if not dirty_nodes:
                return RescanResult(violations=all_violations, unassessed=unassessed)

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
                    lambda: _serialize_graph_transport(g),
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
                        stream_large_request=True,
                        fail_on_infra=True,
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
                opa_dicts = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: content_nodes_to_opa_dicts(opa_nodes, g)
                )
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
                    opa_data = await asyncio.get_event_loop().run_in_executor(
                        None, _bounded_transport_json, opa_payload
                    )
                    ext_coros.append(
                        _call_validator(
                            opa_addr,
                            ValidateRequest(
                                request_id=f"{scan_id}-rescan",
                                hierarchy_payload=opa_data,
                            ),
                        )
                    )
                    ext_names.append("opa")

            # Ansible: dirty task payload plus the same project config/plugins
            # used in the initial scan. dirty_node_ids skips full syntax checks.
            ans_addr = os.environ.get("ANSIBLE_GRPC_ADDRESS")
            if not ans_addr:
                raise RequiredValidatorDependencyError(
                    "Required validator ansible not configured for rescan (ANSIBLE_GRPC_ADDRESS unset)"
                )
            if ans_addr and session.venv_path:
                task_dicts = [d for d in opa_dicts if d.get("type") == "taskcall" and d.get("key") in dirty_ids]
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
                    ans_data = await asyncio.get_event_loop().run_in_executor(
                        None, _bounded_transport_json, ans_payload
                    )
                    ext_coros.append(
                        _call_validator(
                            ans_addr,
                            ValidateRequest(
                                request_id=f"{scan_id}-rescan",
                                hierarchy_payload=ans_data,
                                files=_ansible_context_files(session.working_files),
                                dirty_node_ids=sorted(dirty_ids),
                                venv_path=session.venv_path,
                                session_id=ans_opts.session_id if ans_opts else "",
                                ansible_core_version=(ans_opts.ansible_core_version if ans_opts else ""),
                            ),
                            stream_large_request=True,
                        )
                    )
                    ext_names.append("ansible")

            if ext_coros:
                results = await asyncio.gather(*ext_coros, return_exceptions=True)
                for name, result in zip(ext_names, results, strict=True):
                    if isinstance(result, BaseException):
                        if isinstance(result, GraphTransportError):
                            raise GraphTransportError(str(result)) from result
                        if name in REQUIRED_VALIDATORS:
                            msg = f"Required validator {name} failed during rescan: {result}"
                            raise RequiredValidatorDependencyError(msg) from result
                        logger.warning("Rescan: %s failed: %s", name, result)
                        continue
                    if name in REQUIRED_VALIDATORS and result.error:
                        msg = f"Required validator {name} RPC failed during rescan: {result.error}"
                        raise RequiredValidatorDependencyError(msg)
                    all_violations.extend(result.violations)
                    if name == "ansible":
                        metadata = result.diagnostics.metadata if result.diagnostics is not None else {}
                        unassessed = decode_argspec_coverage(metadata.get(ARGSPEC_COVERAGE_KEY))

            all_violations = _apply_rule_configs(all_violations, rule_configs or [])
            return RescanResult(violations=filter_noqa_violations(all_violations, g), unassessed=unassessed)

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

        # Live policy for Tier 2 prompts comes from request context already
        # on the session (RuleConfig overrides, target ansible-core
        # version, collection pins) — fix_options first, scan_options as
        # fallback, mirroring the scan-context resolution above.
        _policy_opts = session.fix_options or session.scan_options
        _policy_version = str(getattr(_policy_opts, "ansible_core_version", "") or "")
        _policy_specs = [str(s) for s in (getattr(_policy_opts, "collection_specs", None) or [])]
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
            ai_policy_context=_build_ai_policy_context(
                rule_configs,
                _policy_version,
                _policy_specs,
            ),
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
        try:
            splice_outcome = None if defer_tier1 and tier1_node_proposals else splice_with_outcome(graph, originals)
        except SpliceConflictError as exc:
            yield SessionEvent(error=SessionError(code="remediation_failed", message=str(exc)))
            return
        patches = splice_outcome.patches if splice_outcome is not None else []
        if splice_outcome is not None and splice_outcome.unpatched:
            # Never treat an empty patch list as full success (#8): files
            # skipped or spliced from an approximate baseline are explicit.
            logger.warning(
                "Splice left %d file(s) unpatched: %s",
                splice_outcome.unpatched_count,
                ", ".join(splice_outcome.unpatched[:8]),
            )

        # Only findings whose own node's edits reached the returned bytes
        # count as fixed: a file that produced no patch still holds the
        # original source, and a node skipped while a sibling's edit patched
        # the same file never landed either — its finding stays unresolved
        # (and is flagged as skipped so it is distinguishable from one that
        # never had a fix).
        unspliced_fixed: list[ViolationDict] = []
        if splice_outcome is not None:
            landed_fixed, unspliced_fixed = _unspliced_fixed_violations(
                graph_report.fixed_violations,
                graph,
                _landed_node_ids(splice_outcome),
            )
            if unspliced_fixed:
                logger.warning(
                    "Reporting %d finding(s) unresolved: their edit did not reach the output bytes (%s)",
                    len(unspliced_fixed),
                    ", ".join(sorted({str(v.get("file", "")) for v in unspliced_fixed if v.get("file")})[:8]),
                )
                graph_report.fixed_violations = landed_fixed
                graph_report.fixed = len(landed_fixed)

        _format_spliced_patches(patches, originals, temp_dir, formatter=format_content)

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

        if patches:
            for patch in patches:
                session.working_files[_working_files_key(temp_dir, patch.path)] = patch.patched.encode("utf-8")
            await _refresh_session_file_violations(session)

        # 4. Remaining violations — sourced from the graph (authoritative).
        # Copy before enrichment so classification metadata does not mutate
        # the graph-owned NodeState snapshot objects.
        remaining = [dict(v) for v in graph_report.remaining_violations]
        remaining.extend(dict(v) for v in unspliced_fixed)
        remaining.extend(dep_health_violations)
        remaining.extend(dict(v) for v in session.file_violations)
        _extend_with_unattached(remaining, session)
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
        session.remaining_ai = [v for v in remaining if not _is_file_finding(v)]
        session.remaining_manual = [v for v in remaining if _is_file_finding(v)]

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
                    approval_token=_current_approval_token(session),
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
                    approval_token=_current_approval_token(session),
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
    def _normalized_proposal_yaml_pair(
        before_yaml: str,
        after_yaml: str,
        fromfile: str,
        tofile: str,
    ) -> tuple[str, str, str]:
        """Normalize proposal YAML and build a unified diff from the result.

        Args:
            before_yaml: Original YAML text.
            after_yaml: Proposed YAML text.
            fromfile: Label for the diff "from" side.
            tofile: Label for the diff "to" side.

        Returns:
            Tuple of normalized before text, normalized after text, and unified diff.
        """
        from apme_engine.remediation.proposal_yaml import normalize_proposal_yaml

        before = normalize_proposal_yaml(before_yaml)
        after = normalize_proposal_yaml(after_yaml)
        diff_hunk = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=fromfile,
                tofile=tofile,
            )
        )
        return before, after, diff_hunk

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

            before_text, after_text, diff_hunk = EngineServicer._normalized_proposal_yaml_pair(
                tnp.before_yaml,
                tnp.after_yaml,
                fromfile=f"a/{tnp.file_path}",
                tofile=f"b/{tnp.file_path} (Tier 1)",
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
                    before_text=before_text,
                    after_text=after_text,
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

            before_text, after_text, diff_hunk = EngineServicer._normalized_proposal_yaml_pair(
                anp.before_yaml,
                anp.after_yaml,
                fromfile=f"a/{anp.file_path}",
                tofile=f"b/{anp.file_path} (AI proposed)",
            )

            proposals.append(
                Proposal(
                    id=f"ai-{idx:04d}",
                    file=anp.file_path,
                    rule_id=rule_id,
                    line_start=anp.line_start,
                    line_end=anp.line_end,
                    before_text=before_text,
                    after_text=after_text,
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

        On collision Tier-1 bytes (``after_text``/``diff_hunk``) win, and the
        rationale follows the bytes: the Tier-1 explanation leads and any AI
        rationale is kept only as clearly labeled context, so authorized bytes
        are never presented under another tier's rationale.

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
                rationale: list[str] = []
                if tier1.explanation:
                    rationale.append(f"[tier-1/deterministic] {tier1.explanation}")
                if existing.explanation:
                    rationale.append(f"[tier-2/ai] {existing.explanation}")
                existing.explanation = "\n".join(rationale)
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
                    approval_token=_current_approval_token(session),
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
        *,
        approval_token: str | None = None,
    ) -> AsyncIterator[SessionEvent]:
        """Preview in an executor and commit once on the event-loop thread.

        Args:
            session: Active session.
            approved_ids: Proposal identifiers the client accepted.
            approval_token: Echoed gate identity. None is reserved for trusted
                internal transitions; an empty string identifies legacy clients.

        Yields:
            SessionEvent: Approval results or a session-scoped error.

        Raises:
            asyncio.CancelledError: When the approving stream disconnects.
        """
        if session.approval_in_flight:
            yield SessionEvent(
                error=SessionError(
                    recoverable=True, code="approval_in_progress", message="An approval is already in progress"
                )
            )
            return
        offered = bool(session.proposals or session.review_declined_proposals)
        current_token = _current_approval_token(session) if offered else ""
        decided_ids = session.approved_ids | set(session.rejected_proposals)
        offered_ids = set(session.proposals) | set(session.review_declined_proposals)
        replay = bool(approved_ids and approved_ids <= decided_ids)
        error_code = ""
        if approval_token:
            if approval_token in session.approval_decisions:
                replay = session.approval_decisions[approval_token] == frozenset(approved_ids)
                if not replay:
                    error_code = "approval_already_decided"
            elif approval_token != current_token:
                error_code = "stale_approval_gate"
            else:
                # Proposal IDs can be reused by a later gate; its token owns
                # the decision even if those IDs occur in earlier telemetry.
                replay = False
        elif approval_token == "" and offered:
            if not approved_ids and session.approval_decisions:
                # An unscoped decline-all cannot distinguish a delayed retry
                # from a fresh decision after the first completed gate.
                error_code = "approval_token_required"
            elif approved_ids and not replay and not approved_ids <= offered_ids:
                error_code = "stale_approval_gate"
        if not error_code and not replay and offered and not approved_ids <= offered_ids:
            error_code = "invalid_approval_ids"
        if error_code:
            yield SessionEvent(
                error=SessionError(
                    code=error_code,
                    recoverable=True,
                    message="This approval does not match this proposal gate. Refresh proposals and retry.",
                )
            )
            return
        if replay or (not offered and (session.awaiting_ai_triage or session.status == 3)):
            # An exact-token retry replays the committed decision's ack in
            # full: the files its approval left unpatched must stay visible
            # to the client, not silently flatten to zero on replay. The
            # approval may have committed and then failed its post-commit
            # reassessment — finish that pending step now, before the
            # acknowledgement, without applying the approval again.
            recovered = await self._session_recover_pending_reassessment(session) if replay else None
            replay_unpatched = session.approval_unpatched.get(approval_token or "", []) if replay else []
            yield SessionEvent(
                approval_ack=ApprovalAck(
                    applied_count=0,
                    status=session.status,
                    ttl_seconds=session.ttl_seconds,
                    unpatched_count=len(replay_unpatched),
                    unpatched_files=list(replay_unpatched),
                )
            )
            if recovered is not None:
                for event in recovered:
                    yield event
                return
            async for event in self._session_replay_state(session):
                yield event
            return
        if approval_token is not None and not offered:
            yield SessionEvent(
                error=SessionError(recoverable=True, code="stale_approval_gate", message="No approval gate is active")
            )
            return
        decision_token = current_token or _current_approval_token(session)
        session.approval_in_flight = True
        session.approval_finished.clear()
        release_on_exit = True
        try:
            preview = None
            graph = session.content_graph
            if (
                isinstance(graph, ContentGraph)
                and session.graph_originals is not None
                and any(pid.startswith(("ai-", "t1-", "g2-t1-")) for pid in session.proposals)
            ):
                future = asyncio.get_event_loop().run_in_executor(
                    None, partial(_preview_graph_approvals, session, graph, session.graph_originals, set(approved_ids))
                )
                try:
                    preview = await asyncio.shield(future)
                except asyncio.CancelledError:
                    # The pure worker cannot be stopped. Keep its read guard
                    # until completion; a resumed stream can retry afterward.
                    release_on_exit = False

                    def release(done: asyncio.Future[_GraphApprovalPreview]) -> None:
                        if not done.cancelled():
                            done.exception()
                        session.approval_in_flight = False
                        session.approval_finished.set()

                    future.add_done_callback(release)
                    raise

            ready: asyncio.Future[list[SessionEvent]] = asyncio.get_running_loop().create_future()

            def consume_ready(done: asyncio.Future[list[SessionEvent]]) -> None:
                if not done.cancelled():
                    done.exception()

            ready.add_done_callback(consume_ready)

            async def finish() -> None:
                try:
                    events = [
                        event
                        async for event in self._session_finish_approval(
                            session, approved_ids, preview, decision_token=decision_token
                        )
                    ]
                except BaseException as exc:
                    ready.set_exception(exc)
                    raise
                finally:
                    session.approval_in_flight = False
                    session.approval_finished.set()
                ready.set_result(events)
                for event in events:
                    if event.HasField("result"):
                        await self._session_report_result(session, event.result)

            continuation = asyncio.create_task(finish())
            session.approval_tasks.add(continuation)

            def completed(done: asyncio.Task[None]) -> None:
                session.approval_tasks.discard(done)
                if not done.cancelled():
                    done.exception()

            continuation.add_done_callback(completed)
            # The owned task now releases the mutation guard, even if this
            # stream disconnects. Reporting follows readiness independently.
            release_on_exit = False
            events = await asyncio.shield(ready)
            for event in events:
                yield event
        except (SpliceConflictError, OSError) as exc:
            yield SessionEvent(error=SessionError(recoverable=True, code="remediation_failed", message=str(exc)))
        finally:
            if release_on_exit:
                session.approval_in_flight = False
                session.approval_finished.set()

    async def _session_finish_approval(
        self,
        session: SessionState,
        approved_ids: set[str],
        preview: _GraphApprovalPreview | None,
        *,
        decision_token: str | None = None,
    ) -> AsyncIterator[SessionEvent]:
        """Apply approvals and either complete or advance to Gate 2 (AI).

        Args:
            session: Active session.
            approved_ids: Proposal IDs the user accepted.
            preview: Pure graph approval preview, or None for legacy text proposals.
            decision_token: Gate token the decision is recorded under, recorded
                as soon as the approval commits so a failed post-commit step
                cannot lose it. None skips recording (trusted internal calls).

        Yields:
            SessionEvent: ApprovalAck, optional Gate 2 ProposalsReady, and SessionResult.
        """
        was_tier1_gate = session.awaiting_tier1_gate
        run_ai_gate = was_tier1_gate and bool(session.fix_options and session.fix_options.enable_ai)
        # Persist the prepared source transaction before consuming proposals or
        # changing the live graph. A partial write can retry the entire batch.
        if preview is not None:
            _validate_graph_approval_preview(session, session.content_graph, approved_ids, preview)
            if session.temp_dir is not None and preview.files:
                await asyncio.get_event_loop().run_in_executor(
                    None, _write_files_to_temp_dir, session.temp_dir, preview.files
                )
        applied, temp_patches = self._session_apply_approved(session, approved_ids, finalize=False, preview=preview)
        # The approval has committed its bytes and graph changes here. Record
        # the decision and its skipped files now, before file reassessment:
        # that step runs after the commit and can fail (for example when the
        # Native validator is unreachable), and an exact-token retry must
        # still find the recorded decision instead of a stale gate.
        if decision_token is not None:
            session.approval_decisions[decision_token] = frozenset(approved_ids)
            session.approval_unpatched[decision_token] = sorted(getattr(temp_patches, "unpatched", None) or [])
            if session.approval_token == decision_token:
                session.approval_token = ""
                session.approval_offer_snapshot = ()
        session.status = 2  # PROCESSING until applied bytes have been assessed
        if applied or temp_patches:
            session.pending_file_reassessment = "ai" if run_ai_gate else "complete"
            # The preview transaction has already persisted these files.
            session.pending_file_patches = [] if preview is not None else list(temp_patches or [])
            await _finish_pending_file_reassessment(session)
        if not run_ai_gate:
            session.status = 3  # COMPLETE only after file assessment succeeds
        if was_tier1_gate:
            files_touched = len(preview.files) if preview is not None else len(temp_patches or [])
            report_fixed = session.report.fixed if session.report else applied
            # Surface splice skips (never silently dropped): SpliceResult
            # carries .unpatched, but plain-list mocks/legacy paths do not —
            # getattr keeps the approval path total. Approximate-baseline
            # files ride in .unpatched alongside skips (graph_engine
            # splice_with_outcome), so ApprovalAck.unpatched_count covers
            # truncated diffs too.
            unpatched_files = sorted(getattr(temp_patches, "unpatched", None) or [])
            _apply_msg = ProgressUpdate(
                message=(
                    f"Applied {applied} approved Tier 1 proposal(s) "
                    f"({report_fixed} violation(s) fixed, {files_touched} file(s) written); "
                    "declined proposals reverted; "
                    f"{len(unpatched_files)} unpatched file(s)"
                    + (f": {', '.join(unpatched_files[:8])}" if unpatched_files else "")
                ),
                phase="graph-tier1",
                level=2,
            )
            session.progress_logs.append(_apply_msg)
            yield SessionEvent(progress=_apply_msg)
        else:
            unpatched_files = sorted(getattr(temp_patches, "unpatched", None) or [])
        yield SessionEvent(
            approval_ack=ApprovalAck(
                applied_count=applied,
                status=session.status,
                ttl_seconds=session.ttl_seconds,
                unpatched_count=len(unpatched_files),
                unpatched_files=unpatched_files,
            ),
        )

        if run_ai_gate:
            async for event in self._session_enter_ai_phase(session, report_completion=False):
                yield event
            return

        if session.status == 3:  # COMPLETE
            async for event in self._session_serialize_result(session):
                yield event

    async def _session_enter_ai_phase(
        self,
        session: SessionState,
        *,
        report_completion: bool = True,
    ) -> AsyncIterator[SessionEvent]:
        """Pause for AI escalation triage, or skip when no candidates remain.

        Emits ``AiTriageReady`` and returns when there are AI-candidate findings.
        Empty candidate set skips triage and AI (COMPLETE).

        Args:
            session: Session after Tier 1 apply (graph spliced).
            report_completion: Emit reporting after the result; approval callers
                defer reporting until their durable continuation releases its guard.

        Yields:
            SessionEvent: AiTriageReady, or SessionResult when nothing to escalate.
        """
        candidates = _collect_ai_triage_candidates(session)
        if not candidates:
            session.status = 3  # COMPLETE
            result_events = (
                self._session_build_result(session) if report_completion else self._session_serialize_result(session)
            )
            async for event in result_events:
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
        remaining.extend(dict(v) for v in session.file_violations)
        _extend_with_unattached(remaining, session)
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
        session.remaining_ai = [v for v in remaining if not _is_file_finding(v)]
        session.remaining_manual = [v for v in remaining if _is_file_finding(v)]

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
                    approval_token=_current_approval_token(session),
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
            from apme_engine.remediation.graph_engine import splice_with_outcome  # noqa: PLC0415

            try:
                splice_outcome = splice_with_outcome(graph, originals)
            except SpliceConflictError as exc:
                yield SessionEvent(error=SessionError(code="remediation_failed", message=str(exc)))
                return
            patches = splice_outcome.patches
            if splice_outcome.unpatched:
                logger.warning(
                    "Splice left %d file(s) unpatched: %s",
                    splice_outcome.unpatched_count,
                    ", ".join(splice_outcome.unpatched[:8]),
                )
            _format_spliced_patches(patches, originals, session.temp_dir)
            _write_patches_to_session(session, patches)
            if patches:
                session.pending_file_reassessment = "complete"
                session.pending_file_patches = list(patches)
                await _finish_pending_file_reassessment(session)
            session.status = 3
            async for event in self._session_build_result(session):
                yield event

    @staticmethod
    def _session_apply_approved(
        session: SessionState,
        approved_ids: set[str],
        *,
        finalize: bool = True,
        preview: _GraphApprovalPreview | None = None,
    ) -> tuple[int, list[SplicedFilePatch] | None]:
        """Apply approved proposals to session working state.

        For graph-based proposals (``id`` starts with ``"ai-"`` or ``"t1-"``),
        the ContentGraph already holds the pending changes.  Approved nodes
        are promoted; rejected nodes are reverted.  Post-approval,
        ``splice_with_outcome`` re-generates working files.

        For legacy file-based proposals, text-based find/replace is used.

        Args:
            session: Active session whose working files will be mutated.
            approved_ids: Set of proposal IDs the user accepted.
            finalize: When true, mark session COMPLETE. When false (Gate 1
                with AI still pending), leave status for the AI gate.
            preview: Optional pure graph approval preview prepared off the event loop.

        Returns:
            Applied proposal count and prepared text patches. The approval
            preview separately carries complete file bytes for persistence.
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
            applied, rejected_nodes, approved_nodes, temp_patches, landed_nodes, rejected_edit_passes = (
                _apply_graph_approvals(
                    session,
                    graph,
                    originals,
                    approved_ids,
                    preview=preview,
                )
            )
            # Files listed as unpatched that produced no patch at all hold
            # their original bytes: their findings must not be reconciled as
            # fixed (approximate-baseline files do produce a patch and stay
            # fixed, with the truncation reported via ApprovalAck).
            patched_paths = {patch.path for patch in (temp_patches or [])}
            _reconcile_after_approval(
                session,
                graph,
                rejected_nodes,
                approved_node_ids=approved_nodes,
                unspliced_files={
                    path for path in (getattr(temp_patches, "unpatched", None) or []) if path not in patched_paths
                },
                landed_nodes=landed_nodes,
                rejected_edit_passes=rejected_edit_passes,
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

    async def _session_serialize_result(
        self,
        session: SessionState,
    ) -> AsyncIterator[SessionEvent]:
        """Build and yield the final SessionResult event without side effects.

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

        session_event = SessionEvent(
            result=SessionResult(
                patches=patches,
                report=report,
                remaining_violations=remaining_violations,
                fixed_violations=list(report.fixed_violations),
                # Aggregate scan diagnostics for direct clients (issue #512);
                # same source the reporting path emits via
                # FixCompletedEvent.diagnostics. None leaves the field unset.
                diagnostics=session.scan_diagnostics,
            ),
        )
        yield session_event

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
        result: SessionResult | None = None
        async for event in self._session_serialize_result(session):
            yield event
            if event.HasField("result"):
                result = event.result
        if result is not None:
            await self._session_report_result(session, result)

    async def _session_report_result(self, session: SessionState, result: SessionResult) -> None:
        """Emit completion reporting after delivering the client's result.

        Args:
            session: Completed session supplying reporting metadata.
            result: Delivered result with final patches and findings.
        """
        # The gateway derives scan_type from operation intent (ADR-039).
        await emit_fix_completed(
            self._build_fix_event(
                session,
                list(result.remaining_violations),
                list(result.fixed_violations),
                list(result.patches),
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
            diagnostics=session.scan_diagnostics,  # issue #512: same source as SessionResult
        )

    async def _session_recover_pending_reassessment(self, session: SessionState) -> list[SessionEvent] | None:
        """Finish a pending post-approval reassessment and build its events.

        An approval that committed its bytes can still fail its post-commit
        file reassessment; the continuation stays pending on the session.
        Finishing it here must not apply any approval again — it only writes
        the already-committed patches and refreshes findings.

        Args:
            session: Session holding a pending reassessment continuation.

        Returns:
            Events for the recovered continuation, or None when no
            reassessment was pending.
        """
        recovered: list[SessionEvent] | None = None
        async with session.file_reassessment_lock:
            if session.pending_file_reassessment:
                continuation = session.pending_file_reassessment
                await _finish_pending_file_reassessment(session)
                if continuation == "ai":
                    recovered = [event async for event in self._session_enter_ai_phase(session)]
                else:
                    session.status = 3  # COMPLETE after a successful retry
                    recovered = [event async for event in self._session_build_result(session)]
        return recovered

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
        if session.approval_in_flight and (session.status != 1 or session.approval_tasks):
            await session.approval_finished.wait()
        recovered = await self._session_recover_pending_reassessment(session)
        if recovered is not None:
            for event in recovered:
                yield event
            return
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
                    approval_token=_current_approval_token(session),
                    proposals=review_proposals,
                    tier=session.current_tier,
                    status=1,
                ),
            )
        if session.status == 3:  # COMPLETE
            async for event in self._session_serialize_result(session):
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

        Probes fan out via ``asyncio.gather`` under an overall deadline so one
        slow validator cannot stall the response past caller deadlines; slow
        or failing probes are recorded as degraded per-service entries.

        Args:
            request: Health request (unused).
            context: gRPC servicer context.

        Returns:
            HealthResponse with aggregate status and downstream service health.
        """
        downstream: list[ServiceHealth] = []
        unhealthy = False

        metas: list[tuple[str, str, bool]] = []
        coros: list[Awaitable[tuple[bool, ServiceHealth | None]]] = []

        # Probe validators
        for name, env_var in VALIDATOR_ENV_VARS.items():
            addr = os.environ.get(env_var)
            required = name in REQUIRED_VALIDATORS
            if not addr:
                metas.append((name, "", required))
                coros.append(engine_health._missing_validator_health(name, env_var, required=required))
                continue
            metas.append((name, addr, required))
            coros.append(engine_health._probe_validator_health(name, addr, required=required))

        # Galaxy Proxy is required (HTTP /health) — sole collection install path.
        proxy_url = os.environ.get("APME_GALAXY_PROXY_URL", "").strip()
        metas.append(("galaxy_proxy", proxy_url, True))
        coros.append(engine_health._probe_galaxy_proxy_health(proxy_url))

        outcomes = await engine_health.collect_health_probe_outcomes(coros, metas)
        for (pname, paddr, prequired), outcome in zip(metas, outcomes, strict=True):
            if isinstance(outcome, tuple):
                bad, entry = outcome
            elif isinstance(outcome, asyncio.CancelledError):
                bad = prequired
                entry = ServiceHealth(name=pname, status="error: health probe timed out", address=paddr)
            elif isinstance(outcome, BaseException):
                bad = prequired
                entry = ServiceHealth(name=pname, status=f"error: {outcome}", address=paddr)
            else:  # pragma: no cover - defensive; probes only return tuples or raise
                bad = prequired
                entry = ServiceHealth(name=pname, status=f"error: unexpected probe result: {outcome!r}", address=paddr)
            if bad:
                unhealthy = True
            if entry is not None:
                downstream.append(entry)

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
    """
    _write_files_to_temp_dir(temp_dir, {patch.path: patch.patched.encode("utf-8") for patch in patches})


def _write_files_to_temp_dir(temp_dir: Path, files: Mapping[str, bytes]) -> None:
    """Persist an approval file batch without decoding unchanged source bytes.

    Args:
        temp_dir: Session temp directory root.
        files: Complete target bytes indexed by source path.

    Raises:
        ValueError: If a source path escapes the root or contains relative "..".
        OSError: If a write fails.
    """
    root = temp_dir.resolve()
    for source_path, content in files.items():
        rel = Path(source_path)
        if rel.is_absolute():
            path = rel.resolve()
        else:
            if ".." in rel.parts:
                raise ValueError(f"Unsafe patch path rejected: {source_path!r}")
            path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Patch path escapes temp root: {source_path!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(content)
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


class _SourceFormatter(Protocol):
    """Formatter contract shared by post-splice gates."""

    def __call__(
        self, text: str, filename: str = "<stdin>", *, source_context: TaskfileSourceContext | None = None
    ) -> object:
        """Format source with its declared provenance.

        Args:
            text: Source content to format.
            filename: Source path used for diagnostics and classification.
            source_context: Context established by the owning source inventory.

        Returns:
            Formatting result.
        """
        ...


def _format_spliced_patches(
    patches: Sequence[SplicedFilePatch],
    originals: Mapping[str, str],
    temp_dir: Path | None = None,
    *,
    formatter: _SourceFormatter | None = None,
) -> None:
    """Format every gate's patches using the same file inventory and source context.

    Args:
        patches: Assembled patches to format in place.
        originals: Original file inventory, including standalone role markers.
        temp_dir: Project root for session-relative paths.
        formatter: Optional formatter supplied by the initial remediation caller.
    """
    from apme_engine.engine.finder import role_roots_from_paths, taskfile_source_context
    from apme_engine.formatter import format_content

    format_fn = formatter or format_content

    def source_path(value: str) -> str:
        path = Path(value)
        return str(temp_dir / path) if temp_dir is not None and not path.is_absolute() else value

    roots = role_roots_from_paths([source_path(path) for path in originals])
    for patch in patches:
        context = taskfile_source_context(source_path(patch.path), str(temp_dir) if temp_dir else "", roots)
        result = format_fn(patch.patched, filename=patch.path, source_context=context)
        if getattr(result, "changed", False):
            patch.patched = getattr(result, "formatted", patch.patched)


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


@dataclass(frozen=True)
class _GraphApprovalPreview:
    """Prepared source patches without mutations to the live approval state.

    Attributes:
        graph: Live graph whose decisions were previewed.
        proposal_snapshot: Proposal bytes captured before preview.
        approved_ids: Approval choices used for the preview.
        decisions: Resolved proposal and node identifiers.
        patches: Successfully assembled and formatted text source patches.
        files: Complete target bytes for affected files, including restorations.
        landed_nodes: Nodes whose edits are present in the patched bytes.
            Nodes skipped for a missing baseline are absent even when a
            sibling in the same file landed.
    """

    graph: ContentGraph
    proposal_snapshot: tuple[tuple[str, bytes], ...]
    approved_ids: frozenset[str]
    decisions: tuple[tuple[str, str], ...]
    patches: list[SplicedFilePatch]
    files: dict[str, bytes]
    landed_nodes: frozenset[str] = frozenset()


def _proposal_snapshot(session: SessionState) -> tuple[tuple[str, bytes], ...]:
    """Capture the pending proposal set for approval conflict detection.

    Args:
        session: Session owning the pending proposals.

    Returns:
        Sorted proposal identifiers and serialized values.
    """
    return tuple(
        sorted(
            (pid, cast(Message, proposal).SerializeToString(deterministic=True))
            for pid, proposal in session.proposals.items()
        )
    )


def _current_approval_token(session: SessionState) -> str:
    """Bind an opaque, replay-stable token to the current proposal snapshot.

    Args:
        session: Session owning the currently offered proposals.

    Returns:
        Token to echo with approvals, including empty decline-all decisions.
    """
    snapshot = tuple(
        sorted(
            _proposal_snapshot(session)
            + tuple(
                (pid, cast(Message, row).SerializeToString(deterministic=True))
                for pid, row in session.review_declined_proposals.items()
            )
        )
    )
    if not session.approval_token or session.approval_offer_snapshot != snapshot:
        session.approval_token = uuid.uuid4().hex
        session.approval_offer_snapshot = snapshot
    return session.approval_token


def _apply_node_approval_decisions(
    graph: ContentGraph, decisions: tuple[tuple[str, str], ...], approved_ids: frozenset[str]
) -> tuple[set[str], set[str], dict[str, int]]:
    """Apply decisions to an exclusively owned graph or during an atomic commit.

    Args:
        graph: Graph to update.
        decisions: Resolved proposal and node identifiers.
        approved_ids: Accepted proposal identifiers.

    Returns:
        Rejected node identifiers, approved node identifiers, and for each
        rejected node the earliest pass number among the pending edits its
        rejection removes. Certification orders a declined edit against
        the pass that cleared a finding with it: a rescan at pass N runs on
        text carrying every edit recorded up to N, so a declined edit from
        a pass <= N may have supplied that clearance while one recorded
        later cannot have. Nodes rejected without pending edits are absent
        from the mapping (their rejection changes no bytes).
    """
    rejected: set[str] = set()
    approved: set[str] = set()
    rejected_edit_passes: dict[str, int] = {}

    def _reject(node_id: str) -> None:
        node = graph.get_node(node_id)
        if node is not None:
            # Only transform entries (non-empty source) are edits; scan
            # snapshots carry the baseline text, so rejecting a node whose
            # only pending entry is its scan removes no bytes.
            pending_passes = [entry.pass_number for entry in node.progression if not entry.approved and entry.source]
            if pending_passes:
                rejected_edit_passes[node_id] = min(pending_passes)
        graph.reject_node(node_id)
        rejected.add(node_id)

    for pid, node_id in decisions:
        if pid in approved_ids:
            graph.approve_node(node_id)
            approved.add(node_id)
        else:
            _reject(node_id)
    for node in graph.nodes():
        if node.node_id not in approved and any(
            not entry.approved and entry.source in ("ai", "deterministic") for entry in node.progression
        ):
            _reject(node.node_id)
    return rejected, approved, rejected_edit_passes


def _preview_graph_approvals(
    session: SessionState, graph: ContentGraph, originals: dict[str, str], approved_ids: set[str]
) -> _GraphApprovalPreview:
    """Prepare an approval transaction without changing live graph/session data.

    Args:
        session: Read-only session view while its approval guard is held.
        graph: Read-only live graph to copy.
        originals: Original source file contents.
        approved_ids: Accepted proposal identifiers.

    Returns:
        Preview to commit on the event-loop thread after validation.
    """
    from apme_engine.remediation.graph_engine import (
        AINodeProposal,
        SpliceResult,
        Tier1NodeProposal,
        splice_with_outcome,
    )

    snapshot = _proposal_snapshot(session)
    proposal_node_map: dict[str, str] = {}
    ai_proposals = [p for p in session.ai_proposals if isinstance(p, AINodeProposal)]
    tier1_proposals = [p for p in session.tier1_proposals if isinstance(p, Tier1NodeProposal)]
    for index, proposal in enumerate(ai_proposals):
        proposal_node_map[f"ai-{index:04d}"] = proposal.node_id
    for index, tier1_proposal in enumerate(tier1_proposals):
        proposal_node_map[f"t1-{index:04d}"] = tier1_proposal.node_id
        proposal_node_map[f"g2-t1-{index:04d}"] = tier1_proposal.node_id
    decisions = tuple(
        (pid, node_id)
        for pid, row in session.proposals.items()
        if (node_id := (row.path or "").strip() or proposal_node_map.get(pid))
    )
    choices = frozenset(approved_ids)
    candidate = deepcopy(graph)
    _rejected_nodes, approved_nodes, _rejected_passes = _apply_node_approval_decisions(candidate, decisions, choices)
    splice_outcome = splice_with_outcome(candidate, originals)
    if splice_outcome.unpatched:
        # Never treat an empty patch list as full success: files skipped or
        # spliced from an approximate baseline are surfaced to the caller and
        # forwarded on ApprovalAck.unpatched_count/unpatched_files.
        logger.warning(
            "Splice left %d file(s) unpatched: %s",
            splice_outcome.unpatched_count,
            ", ".join(splice_outcome.unpatched[:8]),
        )
    patches = SpliceResult(splice_outcome.patches, unpatched=splice_outcome.unpatched)
    _format_spliced_patches(patches, originals, session.temp_dir)
    # A retry may change its choices after a partial disk write. Include the
    # baseline for every affected file, even when rejecting its edits produces
    # no splice delta, so the next batch overwrites all prior attempted bytes.
    decided_nodes = {node_id for _pid, node_id in decisions}
    affected_paths = {
        node.file_path
        for node in graph.nodes()
        if node.node_id in decided_nodes
        or any(not entry.approved and entry.source in ("ai", "deterministic") for entry in node.progression)
    }
    restoration = {path: text.encode("utf-8") for path, text in originals.items() if path in affected_paths}
    restoration.update(session.pre_gate2_files)
    files = {_working_files_key(session.temp_dir, path): content for path, content in restoration.items()}
    for patch in patches:
        files[_working_files_key(session.temp_dir, patch.path)] = patch.patched.encode("utf-8")
    # An approved edit can restore a file to its scan baseline (a Gate 2
    # candidate undoing a Gate 1 change, or normalising back to the original
    # text): the cumulative splice then produces no patch delta for that
    # file even though the decision changes its bytes. The scan baseline IS
    # the committed result for such a file — without this the pre-Gate-2
    # bytes would survive on disk while the approval counted the edit as
    # applied. Declined-only files are untouched here (a rejection must not
    # undo Gate 1: they keep the pre-Gate-2 bytes above), and files skipped
    # by the splice stay surfaced via ``ApprovalAck.unpatched_files``.
    patched_paths = {patch.path for patch in patches}
    skipped_paths = set(splice_outcome.unpatched)
    approved_paths = {node.file_path for node in candidate.nodes() if node.node_id in approved_nodes and node.file_path}
    restored_paths: set[str] = set()
    for path in approved_paths:
        if path not in patched_paths and path not in skipped_paths and path in originals:
            files[_working_files_key(session.temp_dir, path)] = originals[path].encode("utf-8")
            restored_paths.add(path)
    # Restoration evidence is tracked per node against the final bytes, not
    # per file: a sibling's edit can keep a file in ``patched_paths`` while
    # this node's approved edit restores its text to the scan baseline —
    # the splice skips such a node (no delta), so contributors miss it,
    # yet the patch is built from the scan text, so the node's baseline
    # bytes are exactly what lands in the committed file. Nodes whose
    # approved edit matches their scan baseline certify like contributors
    # when their file landed bytes at all (a produced patch, or the
    # whole-file baseline write above); nodes in files the splice skipped
    # stay uncertified.
    landed_paths = patched_paths | restored_paths
    restored_nodes = frozenset(
        node.node_id
        for node in candidate.nodes()
        if node.node_id in approved_nodes
        and node.file_path
        and node.file_path in landed_paths
        and node.file_path not in skipped_paths
        and (state := node.state) is not None
        and (baseline := node.baseline_state()) is not None
        and state.content_hash == baseline.content_hash
    )
    landed_nodes = _landed_node_ids(splice_outcome) | restored_nodes
    return _GraphApprovalPreview(graph, snapshot, choices, decisions, patches, files, landed_nodes)


def _validate_graph_approval_preview(
    session: SessionState, graph: object, approved_ids: set[str], prepared: _GraphApprovalPreview
) -> None:
    """Reject stale approval input before disk persistence or live commit.

    Args:
        session: Session owning the pending proposals.
        graph: Current live graph.
        approved_ids: Approval choices to validate.
        prepared: Preview captured while the session guard was held.

    Raises:
        SpliceConflictError: When the graph, proposals, or choices changed.
    """
    if (
        prepared.graph is not graph
        or prepared.proposal_snapshot != _proposal_snapshot(session)
        or prepared.approved_ids != frozenset(approved_ids)
    ):
        raise SpliceConflictError("Approval proposals changed while preparing patches; retry the approval")


def _apply_graph_approvals(
    session: SessionState,
    graph: object,
    originals: dict[str, str],
    approved_ids: set[str],
    *,
    preview: _GraphApprovalPreview | None = None,
) -> tuple[int, set[str], set[str], list[SplicedFilePatch], frozenset[str], dict[str, int]]:
    """Commit graph approvals only after their source patches were prepared.

    Args:
        session: Session to update on the owning event-loop thread.
        graph: Live graph containing pending proposals.
        originals: Original source file contents.
        approved_ids: Accepted proposal identifiers.
        preview: Optional pure preview already prepared in an executor.

    Returns:
        Applied count, rejected/approved node identifiers, text patches,
        the nodes whose edits landed in those patches, and the earliest
        pending-edit pass per rejected node. Complete file bytes are
        carried separately in the preview.

    Raises:
        SpliceConflictError: When the pending proposal set changed before commit.
    """
    if not isinstance(graph, ContentGraph):
        if preview is not None:
            raise SpliceConflictError("Approval graph changed while preparing patches; retry the approval")
        return (_apply_text_approvals(session, approved_ids), set(), set(), [], frozenset(), {})
    prepared = preview or _preview_graph_approvals(session, graph, originals, approved_ids)
    _validate_graph_approval_preview(session, graph, approved_ids, prepared)
    rejected, approved, rejected_edit_passes = _apply_node_approval_decisions(
        graph, prepared.decisions, prepared.approved_ids
    )
    applied = 0
    for pid, _node_id in prepared.decisions:
        proposal = session.proposals.pop(pid)
        if pid in approved_ids:
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
        else:
            _record_rejected_proposal(session, pid, proposal)
    if session.pre_gate2_files:
        session.working_files = dict(session.pre_gate2_files)
    session.working_files.update(prepared.files)
    return applied, rejected, approved, prepared.patches, prepared.landed_nodes, rejected_edit_passes


def _landed_node_ids(splice_outcome: SpliceOutcome) -> frozenset[str]:
    """Union of ``SpliceOutcome.contributors`` across every patched file.

    Args:
        splice_outcome: Splice outcome whose contributing nodes to collect.

    Returns:
        Node IDs whose own edits are present in a produced patch. Callers
        that certify findings as fixed must key off these nodes, not off the
        patched file paths: a sibling's edit can produce a file's patch while
        this node's edit never landed.
    """
    return frozenset().union(*splice_outcome.contributors.values())


def _declined_ancestor_invalidates(
    graph: ContentGraph,
    node_id: str,
    rejected_nodes: frozenset[str] | set[str],
    *,
    rejected_edit_passes: Mapping[str, int] | None = None,
    fixed_in_pass: int | None = None,
) -> bool:
    """Whether a declined ancestor edit may have supplied a clearance.

    The rescan that cleared a finding ran on text carrying every edit
    recorded up to its pass, so when an ancestor along the ``CONTAINS``
    chain was rejected and its declined edit was recorded at a pass no
    later than the finding's clearing pass, the clearance may depend on
    bytes that are in that converged text but not in the committed ones.
    A declined edit recorded after the clearing pass cannot have supplied
    the clearance (it did not exist yet when the rescan ran). When the
    ordering is unknown the check is conservative and invalidates.

    Args:
        graph: Graph used to walk the ancestor chain.
        node_id: Node whose finding is being certified.
        rejected_nodes: Nodes whose pending edits were declined in this
            approval round. An ancestor in this set invalidates a
            clearance that its declined edit may have supplied.
        rejected_edit_passes: Earliest pending-edit pass per rejected
            node, from ``_apply_node_approval_decisions``. An ancestor
            absent from the mapping was rejected without pending edits
            (no bytes changed), which never invalidates.
        fixed_in_pass: Pass whose rescan cleared the finding. Combined
            with ``rejected_edit_passes`` this orders the clearance
            against the declined edit; None is conservative.

    Returns:
        True when a declined ancestor edit invalidates the clearance.
    """
    for ancestor in graph.ancestors(node_id):
        if ancestor.node_id not in rejected_nodes:
            continue
        # A rejected ancestor's edit was part of the converged text the
        # rescan cleared whenever the rescan's pass is at least the edit's
        # pass; such an edit is not in the committed bytes, so the
        # clearance cannot be attributed to the edits that did land. A
        # declined edit recorded after the clearing pass postdates the
        # rescan and cannot have supplied the clearance.
        if rejected_edit_passes is not None and ancestor.node_id not in rejected_edit_passes:
            # Rejected without pending edits: no bytes changed.
            continue
        earliest = rejected_edit_passes.get(ancestor.node_id) if rejected_edit_passes is not None else None
        if fixed_in_pass is None or earliest is None or earliest <= fixed_in_pass:
            return True
    return False


def _edit_landed_for_node(
    graph: ContentGraph,
    node_id: str,
    landed_nodes: frozenset[str] | set[str],
    rejected_nodes: frozenset[str] | set[str] = frozenset(),
    *,
    rejected_edit_passes: Mapping[str, int] | None = None,
    fixed_in_pass: int | None = None,
) -> bool:
    """Whether a node's fix reached the output bytes via its own or an inherited edit.

    Certification accepts the node's own landed edit or a landed edit on any
    ancestor along the ``CONTAINS`` chain: YAML inheritance (play-level
    ``module_defaults``, variables, ...) flows down that chain, so an
    ancestor's edit can supply the fix a rescan cleared on this node — the
    rescan runs on the converged graph text, which already includes the
    ancestor's edit. A sibling's edit in the same file is never on that
    chain and never certifies. Ancestors are only reachable through
    ``CONTAINS`` edges, so the rule stays within one file.

    An ancestor's landed edit certifies only when the node carries no edit
    of its own: a node whose own (unlanded) edit changed its text could
    have been fixed by those bytes — the ones that never reached the
    output — so an unrelated ancestor edit that landed (a play rename)
    must not certify it. The node's own text is compared against its scan
    baseline: unchanged text means the only possible fix source is
    inheritance, which the landed ancestor edit then supplies.

    The same convergence argument bounds certification from above, and it
    is ordered by pass: the rescan that cleared the finding ran on text
    carrying every edit recorded up to its pass, so when an ancestor
    along the chain was rejected and its declined edit was recorded at a
    pass no later than the finding's clearing pass, the clearance may
    depend on bytes that are in that converged text but not in the
    committed ones — the finding must stay unresolved rather than be
    reported fixed, even when the node's own edit landed. A declined
    edit recorded after the clearing pass cannot have supplied the
    clearance (it did not exist yet when the rescan ran), so findings
    cleared before that edit keep their certification. When the ordering
    is unknown the check is conservative and invalidates.

    Args:
        graph: Graph used to walk the ancestor chain.
        node_id: Node whose finding is being certified.
        landed_nodes: Nodes whose own edits are present in the produced
            patches, from ``SpliceOutcome.contributors`` (plus approved
            baseline restorations).
        rejected_nodes: Nodes whose pending edits were declined in this
            approval round. An ancestor in this set invalidates a
            clearance that its declined edit may have supplied.
        rejected_edit_passes: Earliest pending-edit pass per rejected
            node, from ``_apply_node_approval_decisions``. An ancestor
            absent from the mapping was rejected without pending edits
            (no bytes changed), which never invalidates.
        fixed_in_pass: Pass whose rescan cleared the finding. Combined
            with ``rejected_edit_passes`` this orders the clearance
            against the declined edit; None is conservative.

    Returns:
        True when this node or one of its ancestors landed in a patch.
    """
    if _declined_ancestor_invalidates(
        graph,
        node_id,
        rejected_nodes,
        rejected_edit_passes=rejected_edit_passes,
        fixed_in_pass=fixed_in_pass,
    ):
        return False
    if node_id in landed_nodes:
        return True
    node = graph.get_node(node_id)
    if node is not None:
        state = node.state
        baseline = node.baseline_state()
        if state is not None and baseline is not None and state.content_hash != baseline.content_hash:
            # This node has an edit of its own that did not land: the fix
            # may live in those unlanded bytes, so a landed ancestor edit
            # cannot be attributed to this finding.
            return False
    return any(ancestor.node_id in landed_nodes for ancestor in graph.ancestors(node_id))


def _unspliced_fixed_violations(
    fixed_violations: list[ViolationDict],
    graph: object,
    landed_nodes: frozenset[str] | set[str],
) -> tuple[list[ViolationDict], list[ViolationDict]]:
    """Split fixed findings into those whose edits reached the source and those that did not.

    A graph finding is only fixed when the bytes handed back to the caller
    carry its fix: its *own* node's edits landed, or an ancestor's edit
    landed and supplies the fix through YAML inheritance. ``landed_nodes``
    comes from ``SpliceOutcome.contributors`` and is node-level by
    construction: a sibling's edit that produced the file's patch does not
    certify this node's finding, a node skipped for a missing baseline is
    absent even when a sibling in the same file landed, and a node whose
    edit set normalises back to the baseline (or rewrites the same bytes)
    is absent as well. In all of those cases none of this node's edits
    reached the output, so reporting the finding as fixed overstates the
    remediation. An ancestor's landed edit (play-level ``module_defaults``
    supplying a value a child task needs) *does* certify: inheritance
    flows down the ``CONTAINS`` chain, so the child's rescan-cleared
    finding was fixed by bytes that are in the output.
    Approximate-baseline files are *not* split out here: their nodes do
    contribute a best-effort patch, so their findings stay fixed and the
    truncation is surfaced separately via ``SpliceOutcome.unpatched``.

    Args:
        fixed_violations: Findings the graph ledger reports as fixed.
        graph: Graph after convergence (used to resolve node -> file).
        landed_nodes: Nodes whose own edits are present in the produced
            patches, from ``SpliceOutcome.contributors``.

    Returns:
        ``(landed, unspliced)`` — findings to keep as fixed and findings to
        report as unresolved. Unspliced entries carry
        ``edit_skipped = True`` so callers can tell them apart from
        findings that never had a fix.
    """
    from apme_engine.graph.content_graph import ContentGraph  # noqa: PLC0415

    if not isinstance(graph, ContentGraph):
        return list(fixed_violations), []

    landed: list[ViolationDict] = []
    unspliced: list[ViolationDict] = []
    for violation in fixed_violations:
        node = graph.get_node(str(violation.get("path", "")))
        if node is None or not node.file_path:
            landed.append(violation)
            continue
        if not _edit_landed_for_node(graph, node.node_id, landed_nodes):
            demoted = dict(violation)
            demoted["remediation_class"] = RemediationClass.MANUAL_REVIEW
            demoted["edit_skipped"] = True
            unspliced.append(demoted)
            continue
        landed.append(violation)
    return landed, unspliced


def _reconcile_after_approval(
    session: SessionState,
    graph: object,
    rejected_node_ids: set[str],
    approved_node_ids: set[str] | None = None,
    unspliced_files: set[str] | None = None,
    landed_nodes: Iterable[str] | None = None,
    rejected_edit_passes: Mapping[str, int] | None = None,
) -> None:
    """Reconcile session accounting after AI proposals are approved/rejected.

    Promotes ``proposed`` violations on approved nodes to ``fixed``,
    transitions ``proposed`` violations on rejected nodes to ``declined``,
    then queries the graph ledger for authoritative counts.

    Findings on ``unspliced_files`` are reported as remaining instead of
    fixed: those files produced no patch, so their bytes are unchanged and
    the ledger's ``fixed`` status did not land in the result.

    Args:
        session: Active session to reconcile.
        graph: ContentGraph after approve/reject mutations.
        rejected_node_ids: Node IDs whose AI proposals were rejected.
        approved_node_ids: Node IDs whose AI proposals were accepted.
            When omitted, only rejected nodes are updated (safe default).
        unspliced_files: Files with approved edits that produced no patch.
            Findings on them stay unresolved (``edit_skipped = True``).
        landed_nodes: Nodes whose own edits are present in the patches that
            landed, from ``SpliceOutcome.contributors``. They serve two
            purposes. Findings previously reopened as ``patch_pending`` on
            them are promoted to ``fixed``: the fix is in the bytes now. A
            node in an unpatched file, or one skipped for a missing baseline
            while a sibling landed, is absent and keeps waiting for its own
            patch. The exception is a clearance reopened because a declined
            edit may have supplied it: landing the node's own edit again
            proves nothing about the declined bytes, so such records wait
            for a fresh assessment (a rescan of current text) instead of a
            retry promotion. Attributed fixed findings on nodes *outside* the set
            follow the skipped-edit path (``edit_skipped``, manual review,
            reopened in the ledger) unless an ancestor's landed edit
            supplies their fix through YAML inheritance: a sibling's edit
            patching the same file must not certify them, so file-level
            membership is never the only test. When omitted, node-level
            certification is skipped and only the file-level
            ``unspliced_files`` check applies.
        rejected_edit_passes: Earliest pending-edit pass per rejected
            node, from ``_apply_node_approval_decisions``. Orders each
            declined ancestor edit against the pass that cleared a
            finding: a declined edit recorded at or before the clearing
            pass may have supplied the clearance (the rescan ran on
            converged text carrying it) and invalidates it, while one
            recorded later postdates the clearance and cannot.

    Findings left in ``pending_review`` — typically a child finding an
    ancestor's Gate 1 edit cleared, on a node with no proposal of its
    own — are settled here through the same node-level certification
    (``landed_nodes``, ``rejected_edit_passes``): they follow the
    ancestor's approval when its edit landed, and reopen as declined
    otherwise, so no finding drops out of both report lists.
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

    if landed_nodes:
        # Ledger promotion happens before the queries below so this very report
        # counts those findings as fixed: their edits are in the output bytes.
        promoted = graph.resolve_patch_pending(set(landed_nodes))
        if promoted:
            logger.info(
                "Promoted %d reopened finding(s) whose patch landed (session=%s)",
                promoted,
                session.session_id,
            )

    # A child finding cleared by an ancestor's Gate 1 edit sits in
    # ``pending_review`` on a node that carries no proposal of its own, so
    # the per-node decision sweep never settles it and the queries below
    # count it as neither fixed nor remaining. Settle those rows with the
    # same certification the fixed list uses: the node's own landed edit
    # or an ancestor's (YAML inheritance flows down the ``CONTAINS``
    # chain) promotes the finding — it follows the ancestor's approval —
    # while anything else, including a declined ancestor edit recorded
    # at or before its clearing pass, reopens as declined so the report
    # keeps listing it instead of dropping it from both lists.
    if graph.query_violations(status="pending_review"):
        settled_landed: set[str] = set(landed_nodes) if landed_nodes is not None else set()

        def _certify_pending(record: ViolationRecord) -> bool:
            return _edit_landed_for_node(
                graph,
                record.key[0],
                settled_landed,
                rejected_node_ids,
                rejected_edit_passes=rejected_edit_passes,
                fixed_in_pass=record.fixed_in_pass,
            )

        promoted_pr, declined_pr = graph.settle_pending_review(_certify_pending)
        if promoted_pr or declined_pr:
            logger.info(
                "Settled %d inherited pending_review finding(s): %d fixed, %d declined (session=%s)",
                promoted_pr + declined_pr,
                promoted_pr,
                declined_pr,
                session.session_id,
            )

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
    remaining.extend(dict(v) for v in session.file_violations)
    _extend_with_unattached(remaining, session)

    fixed = [dict(v) for v in graph.query_violations(status="fixed")]
    for v in fixed:
        v["remediation_class"] = RemediationClass.AUTO_FIXABLE
    _enrich_violations_from_graph(fixed, graph, fixed=True)

    unspliced_file_set = unspliced_files or set()
    landed_set = set(landed_nodes) if landed_nodes is not None else None
    if unspliced_file_set or landed_set is not None:
        kept_fixed: list[ViolationDict] = []
        skipped_unspliced: list[ViolationDict] = []
        skipped_invalidated: list[ViolationDict] = []
        for violation in fixed:
            owner = graph.get_node(str(violation.get("path", "")))
            # Skipped-edit path: the file produced no patch at all, or
            # neither the node's own edit nor an ancestor's landed edit is
            # in the patches that reached the output (a sibling's edit may
            # have patched the same file — that must not certify this
            # node's finding). A declined ancestor edit invalidates a
            # finding only when its pass is at most the finding's clearing
            # pass: the rescan that cleared the finding ran on converged
            # text carrying every edit recorded up to that pass.
            clearing_pass = violation.get("fixed_in_pass")
            if (
                owner is not None
                and owner.file_path
                and (
                    owner.file_path in unspliced_file_set
                    or (
                        landed_set is not None
                        and not _edit_landed_for_node(
                            graph,
                            owner.node_id,
                            landed_set,
                            rejected_node_ids,
                            rejected_edit_passes=rejected_edit_passes,
                            fixed_in_pass=clearing_pass if isinstance(clearing_pass, int) else None,
                        )
                    )
                )
            ):
                demoted = dict(violation)
                demoted["remediation_class"] = RemediationClass.MANUAL_REVIEW
                demoted["edit_skipped"] = True
                if owner is not None and _declined_ancestor_invalidates(
                    graph,
                    owner.node_id,
                    rejected_node_ids,
                    rejected_edit_passes=rejected_edit_passes,
                    fixed_in_pass=clearing_pass if isinstance(clearing_pass, int) else None,
                ):
                    # The bytes may be fine, but the certification was
                    # withdrawn because a declined edit may have supplied
                    # the clearance: landing the node's own edit again must
                    # not repeat it, so the record waits for a fresh
                    # assessment instead of a retry promotion.
                    skipped_invalidated.append(demoted)
                else:
                    skipped_unspliced.append(demoted)
            else:
                kept_fixed.append(violation)
        skipped_fixed = skipped_unspliced + skipped_invalidated
        if skipped_fixed:
            logger.warning(
                "Reporting %d finding(s) unresolved: their edit did not reach the output bytes (%s)",
                len(skipped_fixed),
                ", ".join(sorted({str(v.get("file", "")) for v in skipped_fixed if v.get("file")})[:8]),
            )
            # The ledger is the source of truth for later report rebuilds (Gate
            # 2 queries ``fixed`` again), so a finding whose file never changed
            # must not stay certified as fixed there either. Two reopen
            # flavors: bytes that never landed may promote again once their
            # edit does, while a clearance a declined edit invalidated needs a
            # fresh assessment before it can be reported fixed again.
            reopened = graph.reopen_fixed_violations(skipped_unspliced)
            reopened += graph.reopen_fixed_violations(skipped_invalidated, invalidated_by_decline=True)
            if reopened != len(skipped_fixed):
                logger.warning(
                    "Reopened %d of %d skipped-edit finding(s) in the ledger (session=%s)",
                    reopened,
                    len(skipped_fixed),
                    session.session_id,
                )
            # Skipped edits are manual review, not auto-fixable.
            fixed = kept_fixed
            remaining.extend(skipped_fixed)

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
) -> tuple[list[str], list[str], list[str]]:
    """Validate rule IDs in configs against this Engine's known catalog.

    Performs a forward check (unknown IDs) always.  When *complete* is
    ``True`` (Gateway path), also performs a reverse check (missing IDs)
    to detect catalog drift.

    Args:
        rule_configs: Proto RuleConfig messages.
        complete: If ``True``, treat *rule_configs* as the full catalog
            and check for missing IDs (bidirectional audit).

    Returns:
        Tuple of (unknown_ids, missing_ids, divergent_ids).  *missing_ids*
        is always empty when *complete* is ``False``.  *divergent_ids* holds
        normalized IDs configured more than once with conflicting
        enabled/severity/enforced flags — identical repeats are harmless
        (warn-only) but conflicting repeats resolve order-dependently.

    IDs are compared in normalized form (see
    :func:`~apme_engine.rule_ids.normalize_rule_id`) so a
    prefixed config ID (``native:L042``) matches its bare catalog twin.
    """
    if not _known_rule_ids:
        if complete:
            # Fail closed: an empty engine catalog with a complete Gateway
            # catalog means registration hasn't happened (restart window) —
            # scanning unchecked would flap ADR-041 across restart.
            logger.error("Rule catalog is empty with complete=True — failing closed")
            return [], ["<engine-rule-catalog-empty>"], []
        logger.warning("Rule catalog is empty — bidirectional audit skipped, unknown rule IDs flow unchecked")
        return [], [], []
    if not rule_configs:
        if complete:
            # Fail closed: Gateway claims a complete catalog but sent nothing —
            # every known ID is missing.
            return [], sorted({normalize_rule_id(r) for r in _known_rule_ids}), []
        return [], [], []
    known = {normalize_rule_id(r) for r in _known_rule_ids}
    config_ids: set[str] = set()
    seen: dict[str, str] = {}
    seen_flags: dict[str, tuple[object, object, object]] = {}
    unknown: list[str] = []
    divergent: list[str] = []
    for rc in rule_configs:
        rid: str = rc.rule_id  # type: ignore[attr-defined]
        norm = normalize_rule_id(rid)
        flags = (rc.severity, rc.enabled, rc.enforced)  # type: ignore[attr-defined]
        if norm in seen and seen[norm] != rid:
            logger.warning(
                "Duplicate normalized rule ID %r (from %r and %r) — last-wins; "
                "divergent enabled/severity flags resolve order-dependently",
                norm,
                seen[norm],
                rid,
            )
        if norm in seen_flags and seen_flags[norm] != flags and norm not in divergent:
            logger.warning(
                "Divergent config flags for normalized rule ID %r — resolves order-dependently",
                norm,
            )
            divergent.append(norm)
        seen[norm] = rid
        seen_flags[norm] = flags
        config_ids.add(norm)
        if norm not in known:
            unknown.append(rid)
    missing: list[str] = []
    if complete:
        missing = sorted(known - config_ids)
    return unknown, missing, divergent
