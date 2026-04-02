"""Reporting gRPC servicer — persists fix events to SQLite.

Engine pods push ``FixCompletedEvent`` messages to this servicer via gRPC
(ADR-020 push model).  Each event is decomposed into ORM rows and committed
in a single transaction.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone

import grpc
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from apme.v1 import reporting_pb2, reporting_pb2_grpc
from apme_engine.severity_defaults import severity_from_proto, severity_to_label
from apme_gateway.db import get_session
from apme_gateway.db.models import (
    Proposal,
    Rule,
    Scan,
    ScanCollection,
    ScanGraph,
    ScanLog,
    ScanManifest,
    ScanPatch,
    ScanPythonPackage,
    Session,
    Violation,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _diagnostics_to_json(diag: object) -> str | None:
    """Serialise ScanDiagnostics to a JSON string for storage.

    Args:
        diag: Proto ScanDiagnostics message.

    Returns:
        JSON string or None when diagnostics are empty.
    """
    if diag.ByteSize() == 0:  # type: ignore[attr-defined]
        return None
    return json.dumps(
        {
            "engine_parse_ms": diag.engine_parse_ms,  # type: ignore[attr-defined]
            "engine_annotate_ms": diag.engine_annotate_ms,  # type: ignore[attr-defined]
            "engine_total_ms": diag.engine_total_ms,  # type: ignore[attr-defined]
            "files_scanned": diag.files_scanned,  # type: ignore[attr-defined]
            "graph_nodes_built": diag.graph_nodes_built,  # type: ignore[attr-defined]
            "total_violations": diag.total_violations,  # type: ignore[attr-defined]
            "fan_out_ms": diag.fan_out_ms,  # type: ignore[attr-defined]
            "total_ms": diag.total_ms,  # type: ignore[attr-defined]
        }
    )


class ReportingServicer(reporting_pb2_grpc.ReportingServicer):
    """Concrete Reporting servicer that persists events to SQLite."""

    async def ReportFixCompleted(  # noqa: N802
        self,
        request: reporting_pb2.FixCompletedEvent,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> reporting_pb2.ReportAck:
        """Persist a completed remediate (fix) event.

        Args:
            request: The remediate completion event from an engine pod.
            context: gRPC servicer context.

        Returns:
            Empty acknowledgement.
        """
        logger.info("ReportFixCompleted scan_id=%s session=%s", request.scan_id, request.session_id)
        try:
            async with get_session() as db:
                await _upsert_session(db, request.session_id, request.project_path)
                scan = Scan(
                    scan_id=request.scan_id,
                    session_id=request.session_id,
                    project_id=None,
                    project_path=request.project_path,
                    source=request.source or "cli",
                    trigger="cli",
                    created_at=_now_iso(),
                    scan_type="remediate",
                    total_violations=request.summary.total if request.summary else 0,
                    auto_fixable=request.summary.auto_fixable if request.summary else 0,
                    ai_candidate=request.summary.ai_candidate if request.summary else 0,
                    manual_review=request.summary.manual_review if request.summary else 0,
                    fixed_count=request.report.fixed if request.report else 0,
                    diagnostics_json=_diagnostics_to_json(request.diagnostics),
                )
                db.add(scan)
                _add_violations(db, request.scan_id, list(request.remaining_violations))
                _add_violations(db, request.scan_id, list(request.fixed_violations))
                _add_proposals(db, request.scan_id, list(request.proposals))
                _add_logs(db, request.scan_id, list(request.logs))
                _add_patches(db, request.scan_id, list(request.patches))
                _add_manifest(db, request.scan_id, request.manifest)
                _add_graph(db, request.scan_id, request.content_graph_json)
                await db.commit()
        except Exception:
            logger.exception("Failed to persist remediate event %s", request.scan_id)
            await context.abort(grpc.StatusCode.INTERNAL, "Persistence failure")
        return reporting_pb2.ReportAck()

    async def RegisterRules(  # noqa: N802
        self,
        request: reporting_pb2.RegisterRulesRequest,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> reporting_pb2.RegisterRulesResponse:
        """Reconcile the rule catalog from a Primary registration (ADR-041).

        Args:
            request: Full rule set from the registering Primary.
            context: gRPC servicer context.

        Returns:
            Response with reconciliation counts.
        """
        logger.info(
            "RegisterRules pod_id=%s is_authority=%s rules=%d",
            request.pod_id,
            request.is_authority,
            len(request.rules),
        )
        if not request.is_authority:
            logger.info("Ignoring registration from non-authority pod %s", request.pod_id)
            return reporting_pb2.RegisterRulesResponse(
                accepted=False,
                message="Registration rejected: pod is not the rule authority",
            )

        try:
            async with get_session() as db:
                added, removed, unchanged = await _reconcile_rules(db, request.rules)
                await db.commit()
            logger.info("Rule catalog reconciled: added=%d removed=%d unchanged=%d", added, removed, unchanged)
            return reporting_pb2.RegisterRulesResponse(
                accepted=True,
                message="Catalog reconciled",
                rules_added=added,
                rules_removed=removed,
                rules_unchanged=unchanged,
            )
        except Exception:
            logger.exception("Failed to reconcile rule catalog from pod %s", request.pod_id)
            await context.abort(grpc.StatusCode.INTERNAL, "Rule reconciliation failure")
            return reporting_pb2.RegisterRulesResponse(accepted=False, message="Internal error")


async def _reconcile_rules(
    db: AsyncSession,
    incoming: Sequence[object],
) -> tuple[int, int, int]:
    """Full reconciliation: add new, remove absent, update changed.

    Args:
        db: Active async database session.
        incoming: Proto RuleDefinition messages from the registering Primary.

    Returns:
        Tuple of (added, removed, unchanged) counts.
    """
    now = _now_iso()
    incoming_map: dict[str, object] = {r.rule_id: r for r in incoming}  # type: ignore[attr-defined]

    result = await db.execute(sa_select(Rule))
    existing_rules = {r.rule_id: r for r in result.scalars().all()}

    incoming_ids = set(incoming_map.keys())
    existing_ids = set(existing_rules.keys())

    added = 0
    for rule_id in incoming_ids - existing_ids:
        rd = incoming_map[rule_id]
        db.add(
            Rule(
                rule_id=rd.rule_id,  # type: ignore[attr-defined]
                default_severity=rd.default_severity,  # type: ignore[attr-defined]
                category=rd.category,  # type: ignore[attr-defined]
                source=rd.source,  # type: ignore[attr-defined]
                description=rd.description,  # type: ignore[attr-defined]
                scope=rd.scope,  # type: ignore[attr-defined]
                enabled=rd.enabled,  # type: ignore[attr-defined]
                registered_at=now,
            )
        )
        added += 1

    removed = 0
    for rule_id in existing_ids - incoming_ids:
        existing = existing_rules[rule_id]
        await db.delete(existing)
        removed += 1

    unchanged = 0
    for rule_id in incoming_ids & existing_ids:
        rd = incoming_map[rule_id]
        existing = existing_rules[rule_id]
        existing.default_severity = rd.default_severity  # type: ignore[attr-defined]
        existing.category = rd.category  # type: ignore[attr-defined]
        existing.source = rd.source  # type: ignore[attr-defined]
        existing.description = rd.description  # type: ignore[attr-defined]
        existing.scope = rd.scope  # type: ignore[attr-defined]
        existing.enabled = rd.enabled  # type: ignore[attr-defined]
        existing.registered_at = now
        unchanged += 1

    return added, removed, unchanged


async def _upsert_session(db: AsyncSession, session_id: str, project_path: str) -> None:
    """Create or update the session row with the latest timestamp.

    Args:
        db: Active async database session.
        session_id: Deterministic session hash.
        project_path: Project root path.
    """
    stmt = sa_select(Session).where(Session.session_id == session_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    now = _now_iso()
    if existing is None:
        db.add(Session(session_id=session_id, project_path=project_path, first_seen=now, last_seen=now))
    else:
        existing.last_seen = now


def _add_violations(db: AsyncSession, scan_id: str, violations: Sequence[object]) -> None:
    """Convert proto Violations to ORM rows and add to the session.

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        violations: Proto Violation messages.
    """
    for v in violations:
        line_val: int | None = None
        oneof = v.WhichOneof("line_oneof")  # type: ignore[attr-defined]
        if oneof == "line":
            line_val = v.line  # type: ignore[attr-defined]
        elif oneof == "line_range":
            line_val = v.line_range.start  # type: ignore[attr-defined]
        db.add(
            Violation(
                scan_id=scan_id,
                rule_id=v.rule_id,  # type: ignore[attr-defined]
                level=severity_to_label(severity_from_proto(v.severity)),  # type: ignore[attr-defined]
                message=v.message,  # type: ignore[attr-defined]
                file=v.file,  # type: ignore[attr-defined]
                line=line_val,
                path=v.path,  # type: ignore[attr-defined]
                remediation_class=v.remediation_class,  # type: ignore[attr-defined]
                scope=v.scope,  # type: ignore[attr-defined]
                validator_source=v.source,  # type: ignore[attr-defined]
                snippet=v.snippet,  # type: ignore[attr-defined]
            )
        )


def _add_proposals(db: AsyncSession, scan_id: str, proposals: Sequence[object]) -> None:
    """Convert proto ProposalOutcome to ORM rows.

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        proposals: Proto ProposalOutcome messages.
    """
    for p in proposals:
        db.add(
            Proposal(
                scan_id=scan_id,
                proposal_id=p.proposal_id,  # type: ignore[attr-defined]
                rule_id=p.rule_id,  # type: ignore[attr-defined]
                file=p.file,  # type: ignore[attr-defined]
                tier=p.tier,  # type: ignore[attr-defined]
                confidence=p.confidence,  # type: ignore[attr-defined]
                status=p.status,  # type: ignore[attr-defined]
            )
        )


def _add_logs(db: AsyncSession, scan_id: str, logs: Sequence[object]) -> None:
    """Convert proto ProgressUpdate to ORM rows.

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        logs: Proto ProgressUpdate messages.
    """
    for entry in logs:
        db.add(
            ScanLog(
                scan_id=scan_id,
                message=entry.message,  # type: ignore[attr-defined]
                phase=entry.phase,  # type: ignore[attr-defined]
                progress=entry.progress,  # type: ignore[attr-defined]
                level=entry.level,  # type: ignore[attr-defined]
            )
        )


def _add_patches(db: AsyncSession, scan_id: str, patches: Sequence[object]) -> None:
    """Convert proto FilePatch messages to ORM rows.

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        patches: Proto FilePatch messages.
    """
    for p in patches:
        diff = p.diff  # type: ignore[attr-defined]
        if diff:
            db.add(
                ScanPatch(
                    scan_id=scan_id,
                    file=p.path,  # type: ignore[attr-defined]
                    diff=diff,
                )
            )


def _add_manifest(db: AsyncSession, scan_id: str, manifest: object) -> None:
    """Persist ProjectManifest data from a scan event (ADR-040).

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        manifest: Proto ProjectManifest message (may be empty).
    """
    if manifest.ByteSize() == 0:  # type: ignore[attr-defined]
        return

    requirements = list(manifest.requirements_files)  # type: ignore[attr-defined]
    db.add(
        ScanManifest(
            scan_id=scan_id,
            ansible_core_version=manifest.ansible_core_version,  # type: ignore[attr-defined]
            requirements_files_json=json.dumps(requirements),
            dependency_tree=manifest.dependency_tree,  # type: ignore[attr-defined]
        )
    )
    seen_fqcns: set[str] = set()
    for c in manifest.collections:  # type: ignore[attr-defined]
        if c.fqcn in seen_fqcns:
            logger.debug("Skipping duplicate collection FQCN '%s' for scan '%s'", c.fqcn, scan_id)
            logger.debug("Skipping duplicate collection FQCN '%s' for scan '%s'", c.fqcn, scan_id)
            logger.debug("Skipping duplicate collection FQCN '%s' for scan '%s'", c.fqcn, scan_id)
            continue
        seen_fqcns.add(c.fqcn)
        db.add(
            ScanCollection(
                scan_id=scan_id,
                fqcn=c.fqcn,
                version=c.version,
                source=c.source or "unknown",
                license=c.license,
                supplier=c.supplier,
            )
        )
    for p in manifest.python_packages:  # type: ignore[attr-defined]
        db.add(
            ScanPythonPackage(
                scan_id=scan_id,
                name=p.name,
                version=p.version,
                license=p.license,
                supplier=p.supplier,
            )
        )


def _add_graph(db: AsyncSession, scan_id: str, content_graph_json: str) -> None:
    """Persist ContentGraph JSON from a scan event.

    Args:
        db: Active async database session.
        scan_id: Owning scan UUID.
        content_graph_json: JSON string from ``ContentGraph.to_dict()``.
    """
    if not content_graph_json:
        return

    node_count = 0
    edge_count = 0
    try:
        parsed = json.loads(content_graph_json)
        if isinstance(parsed, dict):
            node_count = len(parsed.get("nodes", []))
            edge_count = len(parsed.get("edges", []))
        else:
            logger.warning("content_graph_json for scan %s is not a JSON object, storing raw", scan_id)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Invalid content_graph_json for scan %s, storing raw", scan_id)

    db.add(
        ScanGraph(
            scan_id=scan_id,
            graph_json=content_graph_json,
            node_count=node_count,
            edge_count=edge_count,
        )
    )
