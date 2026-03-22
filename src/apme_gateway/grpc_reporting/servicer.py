"""Reporting gRPC servicer — persists scan/fix events to SQLite.

Engine pods push ``ScanCompletedEvent`` and ``FixCompletedEvent`` messages
to this servicer via gRPC (ADR-020 push model).  Each event is decomposed
into ORM rows and committed in a single transaction.
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
from apme_gateway.db import get_session
from apme_gateway.db.models import Proposal, Scan, ScanLog, Session, Violation

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
            "trees_built": diag.trees_built,  # type: ignore[attr-defined]
            "total_violations": diag.total_violations,  # type: ignore[attr-defined]
            "fan_out_ms": diag.fan_out_ms,  # type: ignore[attr-defined]
            "total_ms": diag.total_ms,  # type: ignore[attr-defined]
        }
    )


class ReportingServicer(reporting_pb2_grpc.ReportingServicer):
    """Concrete Reporting servicer that persists events to SQLite."""

    async def ReportScanCompleted(  # noqa: N802
        self,
        request: reporting_pb2.ScanCompletedEvent,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> reporting_pb2.ReportAck:
        """Persist a completed scan event.

        Args:
            request: The scan completion event from an engine pod.
            context: gRPC servicer context.

        Returns:
            Empty acknowledgement.
        """
        logger.info("ReportScanCompleted scan_id=%s session=%s", request.scan_id, request.session_id)
        try:
            async with get_session() as db:
                await _upsert_session(db, request.session_id, request.project_path)
                scan = Scan(
                    scan_id=request.scan_id,
                    session_id=request.session_id,
                    project_path=request.project_path,
                    source=request.source or "cli",
                    created_at=_now_iso(),
                    scan_type="scan",
                    total_violations=request.summary.total if request.summary else 0,
                    auto_fixable=request.summary.auto_fixable if request.summary else 0,
                    ai_candidate=request.summary.ai_candidate if request.summary else 0,
                    manual_review=request.summary.manual_review if request.summary else 0,
                    diagnostics_json=_diagnostics_to_json(request.diagnostics),
                )
                db.add(scan)
                _add_violations(db, request.scan_id, list(request.violations))
                _add_logs(db, request.scan_id, list(request.logs))
                await db.commit()
        except Exception:
            logger.exception("Failed to persist scan event %s", request.scan_id)
            await context.abort(grpc.StatusCode.INTERNAL, "Persistence failure")
        return reporting_pb2.ReportAck()

    async def ReportFixCompleted(  # noqa: N802
        self,
        request: reporting_pb2.FixCompletedEvent,
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> reporting_pb2.ReportAck:
        """Persist a completed fix event.

        Args:
            request: The fix completion event from an engine pod.
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
                    project_path=request.project_path,
                    source=request.source or "cli",
                    created_at=_now_iso(),
                    scan_type="fix",
                    total_violations=request.summary.total if request.summary else 0,
                    auto_fixable=request.summary.auto_fixable if request.summary else 0,
                    ai_candidate=request.summary.ai_candidate if request.summary else 0,
                    manual_review=request.summary.manual_review if request.summary else 0,
                    diagnostics_json=_diagnostics_to_json(request.diagnostics),
                )
                db.add(scan)
                _add_violations(db, request.scan_id, list(request.remaining_violations))
                _add_proposals(db, request.scan_id, list(request.proposals))
                _add_logs(db, request.scan_id, list(request.logs))
                await db.commit()
        except Exception:
            logger.exception("Failed to persist fix event %s", request.scan_id)
            await context.abort(grpc.StatusCode.INTERNAL, "Persistence failure")
        return reporting_pb2.ReportAck()


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
                level=v.level,  # type: ignore[attr-defined]
                message=v.message,  # type: ignore[attr-defined]
                file=v.file,  # type: ignore[attr-defined]
                line=line_val,
                path=v.path,  # type: ignore[attr-defined]
                remediation_class=v.remediation_class,  # type: ignore[attr-defined]
                scope=v.scope,  # type: ignore[attr-defined]
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
