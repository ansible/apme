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
from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from apme.v1 import reporting_pb2, reporting_pb2_grpc
from apme_engine.graph.severity import severity_from_proto, severity_to_label
from apme_gateway.db import get_session
from apme_gateway.db.models import (
    PatchedFile,
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
from apme_gateway.proposals.flush import replace_scan_proposals
from apme_gateway.proposals.grouping import (
    group_violations,
    merge_outcomes,
    review_status_for_proposal,
    violation_accepts_review_status,
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
                existing = (
                    await db.execute(sa_select(Scan).where(Scan.scan_id == request.scan_id))
                ).scalar_one_or_none()
                if existing is None:
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
                else:
                    # Live ProposalsReady may have created a stub scan (ADR-062 Phase 2).
                    scan = existing
                    scan.session_id = request.session_id
                    scan.project_path = request.project_path
                    if not scan.source or scan.source == "gateway":
                        scan.source = request.source or scan.source or "cli"
                    scan.total_violations = request.summary.total if request.summary else scan.total_violations
                    scan.auto_fixable = request.summary.auto_fixable if request.summary else scan.auto_fixable
                    scan.ai_candidate = request.summary.ai_candidate if request.summary else scan.ai_candidate
                    scan.manual_review = request.summary.manual_review if request.summary else scan.manual_review
                    scan.fixed_count = request.report.fixed if request.report else scan.fixed_count
                    scan.diagnostics_json = _diagnostics_to_json(request.diagnostics)
                    # Idempotent replay: clear append-only children before re-add.
                    # Keep Proposal rows until replace_scan_proposals bridges them.
                    await _clear_scan_children_for_replay(db, request.scan_id)
                await db.flush()
                _add_violations(db, request.scan_id, list(request.remaining_violations))
                _add_violations(db, request.scan_id, list(request.fixed_violations))
                await db.flush()
                await _persist_grouped_proposals(
                    db,
                    scan_id=request.scan_id,
                    outcomes=list(request.proposals),
                )
                _add_logs(db, request.scan_id, list(request.logs))
                _add_patches(db, request.scan_id, list(request.patches))
                _add_manifest(db, request.scan_id, request.manifest)
                _add_graph(db, request.scan_id, request.content_graph_json)
                await db.commit()

                await _generate_scan_notifications(db, scan, request)
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


async def _clear_scan_children_for_replay(db: AsyncSession, scan_id: str) -> None:
    """Delete append-only scan children so ReportFixCompleted can re-insert.

    Preserves ``Proposal`` rows so the live stub → archival bridge in
    ``replace_scan_proposals`` still sees prior gate-commit state.

    Args:
        db: Active async session.
        scan_id: Scan whose child rows to clear.
    """
    for model in (
        Violation,
        ScanLog,
        ScanPatch,
        PatchedFile,
        ScanManifest,
        ScanCollection,
        ScanPythonPackage,
        ScanGraph,
    ):
        await db.execute(sa_delete(model).where(model.scan_id == scan_id))


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
                remediation_resolution=v.remediation_resolution,  # type: ignore[attr-defined]
                scope=v.scope,  # type: ignore[attr-defined]
                validator_source=v.source,  # type: ignore[attr-defined]
                original_yaml=v.original_yaml,  # type: ignore[attr-defined]
                fixed_yaml=v.fixed_yaml,  # type: ignore[attr-defined]
                co_fixes=",".join(v.co_fixes),  # type: ignore[attr-defined]
                node_line_start=v.node_line_start,  # type: ignore[attr-defined]
                ai_reason=v.metadata.get("ai_reason", ""),  # type: ignore[attr-defined]
                ai_suggestion=v.metadata.get("ai_suggestion", ""),  # type: ignore[attr-defined]
                review_status=None,
            )
        )


async def _persist_grouped_proposals(
    db: AsyncSession,
    *,
    scan_id: str,
    outcomes: Sequence[object],
) -> None:
    """Build ADR-062 working-set proposals from persisted violations.

    When the engine sends ProposalOutcome rows without matching violations
    (legacy thin outcome path), fall back to outcome-only proposals so
    historical activity still has a working set.

    Args:
        db: Active async session (violations already flushed).
        scan_id: Owning scan id.
        outcomes: Engine ProposalOutcome messages to overlay.
    """
    from apme_gateway.proposals.grouping import GroupedProposal  # noqa: PLC0415

    result = await db.execute(sa_select(Violation).where(Violation.scan_id == scan_id))
    violations = list(result.scalars().all())
    grouped = group_violations(violations, include_diff=True)
    merged = merge_outcomes(grouped, outcomes)

    if not merged and outcomes:
        fallback: list[GroupedProposal] = []
        for raw in outcomes:
            status = str(getattr(raw, "status", "") or "pending")
            if status == "rejected":
                status = "declined"
            tier = int(getattr(raw, "tier", 0) or 0)
            source = "ai" if tier >= 2 else "outcome"
            gate = "ai" if tier >= 2 else ""
            rule_id = str(getattr(raw, "rule_id", "") or "")
            fallback.append(
                GroupedProposal(
                    proposal_id=str(getattr(raw, "proposal_id", "") or ""),
                    rule_id=rule_id,
                    rule_ids=(rule_id,) if rule_id else (),
                    violation_ids=(),
                    file=str(getattr(raw, "file", "") or ""),
                    path="",
                    line_start=0,
                    tier=tier,
                    source=source,
                    gate=gate,
                    status=status,
                    confidence=float(getattr(raw, "confidence", 0.0) or 0.0),
                    coupled=False,
                )
            )
        merged = fallback

    await replace_scan_proposals(db, scan_id=scan_id, proposals=list(merged))

    # Stamp review_status from persisted rows so gate-commit decisions bridged
    # across replace_scan_proposals (and non-interactive Tier 1 promotions
    # written by replace) are applied once violation_ids exist.
    from apme_gateway.db.models import Proposal  # noqa: PLC0415
    from apme_gateway.proposals.grouping import (  # noqa: PLC0415
        parse_json_list,
        stamp_rule_allowlist,
    )

    by_id = {v.id: v for v in violations}
    persisted = list((await db.execute(sa_select(Proposal).where(Proposal.scan_id == scan_id))).scalars().all())
    for prop in persisted:
        review = review_status_for_proposal(prop.source, prop.status)
        if not review:
            continue
        v_ids = parse_json_list(prop.violation_ids_json)
        int_ids = [int(v) for v in v_ids if str(v).isdigit() or isinstance(v, int)]
        if not int_ids:
            continue
        stamp_rules = stamp_rule_allowlist(
            stamp_rule_ids_json=prop.stamp_rule_ids_json,
            rule_ids_json=prop.rule_ids_json,
        )
        for vid in int_ids:
            violation = by_id.get(vid)
            if violation is None or violation.review_status is not None:
                continue
            if stamp_rules:
                v_rule = str(getattr(violation, "rule_id", "") or "")
                if v_rule not in stamp_rules:
                    continue
            if not violation_accepts_review_status(prop.source, violation, decision=prop.status):
                continue
            violation.review_status = review


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

    Also stores full patched file content in ``patched_files`` for async
    PR creation (ADR-050).

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
        patched_bytes: bytes = p.patched  # type: ignore[attr-defined]
        if patched_bytes:
            db.add(
                PatchedFile(
                    scan_id=scan_id,
                    path=p.path,  # type: ignore[attr-defined]
                    content=patched_bytes,
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
    seen_pkg_names: set[str] = set()
    for p in manifest.python_packages:  # type: ignore[attr-defined]
        if p.name in seen_pkg_names:
            logger.debug("Skipping duplicate python package '%s' for scan '%s'", p.name, scan_id)
            continue
        seen_pkg_names.add(p.name)
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


async def _generate_scan_notifications(
    db: AsyncSession,
    scan: Scan,
    request: reporting_pb2.FixCompletedEvent,
) -> None:
    """Create notifications from a persisted scan event (best-effort).

    Builds lightweight Violation-like objects from the proto data for the
    notification generator to inspect, then delegates to
    :func:`apme_gateway.notifications.generate_notifications`.

    Args:
        db: Active async database session.
        scan: The committed Scan ORM row.
        request: Original gRPC event (for violation proto access).
    """
    try:
        from apme_gateway.notifications import (  # noqa: PLC0415
            broadcast_notifications,
            generate_notifications,
        )

        all_protos = list(request.remaining_violations) + list(request.fixed_violations)
        stub_violations = [
            Violation(
                scan_id=scan.scan_id,
                rule_id=v.rule_id,
                level="",
                message="",
                file=v.file,
            )
            for v in all_protos
        ]
        payloads = await generate_notifications(db, scan, stub_violations)
        await db.commit()
        broadcast_notifications(payloads)
    except Exception:
        await db.rollback()
        logger.warning("Notification generation failed for scan %s", scan.scan_id, exc_info=True)
