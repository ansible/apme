"""Scan lifecycle management — create, persist, query."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apme_gateway.models.database import ScanRecord, ViolationRecord
from apme_gateway.services.grpc_client import PrimaryClient


def _diagnostics_to_dict(diag) -> dict:
    """Convert a ScanDiagnostics protobuf to a plain dict."""
    validators = []
    for v in diag.validators:
        validators.append(
            {
                "validator_name": v.validator_name,
                "request_id": v.request_id,
                "total_ms": v.total_ms,
                "files_received": v.files_received,
                "violations_found": v.violations_found,
                "rule_timings": [
                    {"rule_id": rt.rule_id, "elapsed_ms": rt.elapsed_ms, "violations": rt.violations}
                    for rt in v.rule_timings
                ],
                "metadata": dict(v.metadata),
            }
        )
    return {
        "engine_parse_ms": diag.engine_parse_ms,
        "engine_annotate_ms": diag.engine_annotate_ms,
        "engine_total_ms": diag.engine_total_ms,
        "files_scanned": diag.files_scanned,
        "trees_built": diag.trees_built,
        "total_violations": diag.total_violations,
        "validators": validators,
        "fan_out_ms": diag.fan_out_ms,
        "total_ms": diag.total_ms,
    }


async def create_scan(
    session: AsyncSession,
    client: PrimaryClient,
    *,
    project_name: str,
    files: dict[str, str],
    ansible_core_version: str = "",
    collection_specs: list[str] | None = None,
) -> ScanRecord:
    """Invoke Primary.ScanStream, persist results, return the scan record."""
    scan = ScanRecord(project_name=project_name, status="running")
    scan.options = {
        "ansible_core_version": ansible_core_version,
        "collection_specs": collection_specs or [],
    }
    session.add(scan)
    await session.flush()

    try:
        resp = await client.scan(
            files,
            project_name,
            ansible_core_version=ansible_core_version,
            collection_specs=collection_specs or [],
        )
    except Exception as exc:
        scan.status = "failed"
        scan.diagnostics = {"error": str(exc)}
        await session.commit()
        return scan

    error_count = warning_count = hint_count = 0
    violations: list[ViolationRecord] = []
    for v in resp.violations:
        line_val = None
        line_end = None
        if v.HasField("line_range"):
            line_val = v.line_range.start
            line_end = v.line_range.end
        elif v.line:
            line_val = v.line

        vr = ViolationRecord(
            scan_id=scan.id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=line_val,
            line_end=line_end,
            path=v.path,
        )
        violations.append(vr)
        if v.level == "error":
            error_count += 1
        elif v.level == "warning":
            warning_count += 1
        else:
            hint_count += 1

    session.add_all(violations)
    scan.status = "completed"
    scan.total_violations = len(violations)
    scan.error_count = error_count
    scan.warning_count = warning_count
    scan.hint_count = hint_count
    if resp.diagnostics:
        scan.diagnostics = _diagnostics_to_dict(resp.diagnostics)
    await session.commit()
    return scan


async def ingest_scan(
    session: AsyncSession,
    *,
    project_name: str,
    scan_type: str = "scan",
    status: str = "completed",
    violations: list[dict],
    fixed_count: int = 0,
    diagnostics: dict | None = None,
    request_id: str | None = None,
) -> ScanRecord:
    """Persist a pre-computed scan result from the CLI."""
    if request_id:
        existing = (
            await session.execute(select(ScanRecord).where(ScanRecord.request_id == request_id))
        ).scalar_one_or_none()
        if existing:
            return existing

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=5)
    recent_dup = (
        await session.execute(
            select(ScanRecord).where(
                ScanRecord.project_name == project_name,
                ScanRecord.scan_type == scan_type,
                ScanRecord.created_at >= cutoff,
            )
        )
    ).scalar_one_or_none()
    if recent_dup:
        return recent_dup

    error_count = warning_count = hint_count = 0
    violation_records: list[ViolationRecord] = []

    scan = ScanRecord(
        project_name=project_name,
        scan_type=scan_type,
        status=status,
        request_id=request_id,
    )
    session.add(scan)
    await session.flush()

    for v in violations:
        level = v.get("level", "hint")
        vr = ViolationRecord(
            scan_id=scan.id,
            rule_id=v.get("rule_id", ""),
            level=level,
            message=v.get("message", ""),
            file=v.get("file", ""),
            line=v.get("line"),
            line_end=v.get("line_end"),
            path=v.get("path"),
        )
        violation_records.append(vr)
        if level == "error":
            error_count += 1
        elif level == "warning":
            warning_count += 1
        else:
            hint_count += 1

    session.add_all(violation_records)
    scan.total_violations = len(violation_records)
    scan.error_count = error_count
    scan.warning_count = warning_count
    scan.hint_count = hint_count
    scan.fixed_count = fixed_count
    scan.diagnostics = diagnostics
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if request_id:
            existing = (
                await session.execute(select(ScanRecord).where(ScanRecord.request_id == request_id))
            ).scalar_one_or_none()
            if existing:
                return existing
    return scan


async def list_scans(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    project: str | None = None,
) -> tuple[list[ScanRecord], int]:
    """Return paginated scan records with optional filters."""
    q = select(ScanRecord).order_by(ScanRecord.created_at.desc())
    count_q = select(func.count(ScanRecord.id))

    if status:
        q = q.where(ScanRecord.status == status)
        count_q = count_q.where(ScanRecord.status == status)
    if project:
        q = q.where(ScanRecord.project_name.ilike(f"%{project}%"))
        count_q = count_q.where(ScanRecord.project_name.ilike(f"%{project}%"))

    total = (await session.execute(count_q)).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(q)
    return list(result.scalars().all()), total


async def get_scan(session: AsyncSession, scan_id: str) -> ScanRecord | None:
    """Fetch a single scan with its violations eagerly loaded."""
    q = select(ScanRecord).where(ScanRecord.id == scan_id).options(selectinload(ScanRecord.violations))
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def delete_scan(session: AsyncSession, scan_id: str) -> bool:
    """Delete a scan and its cascade-deleted violations.  Returns True if found."""
    scan = await session.get(ScanRecord, scan_id)
    if not scan:
        return False
    await session.delete(scan)
    await session.commit()
    return True
