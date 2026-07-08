"""REST + SSE endpoints for project operations (ADR-052).

Provides ``POST``, ``GET``, ``POST /approve``, ``POST /cancel``,
``POST /submit``, and ``GET /events`` under
``/api/v1/projects/{project_id}/operation``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apme_engine.graph.severity import severity_from_proto, severity_to_label
from apme_gateway.api.schemas import SubmitRequest, SubmitResponse
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import PatchedFile, Scan
from apme_gateway.operation_registry import _now_iso, get_operation_registry
from apme_gateway.operation_types import (
    TERMINAL_STATUSES,
    OperationResult,
    OperationState,
    OperationStatus,
    ProgressEntry,
    Proposal,
)

logger = logging.getLogger(__name__)

operation_router = APIRouter(prefix="/api/v1/projects/{project_id}/operation")


# ── Request / Response schemas ────────────────────────────────────────


class OperateRequest(BaseModel):  # type: ignore[misc]
    """Body for ``POST /operate``.

    Attributes:
        action: ``check`` or ``remediate``.
        options: Additional operation options.
    """

    action: str = Field(..., pattern="^(check|remediate)$")
    options: dict[str, Any] = Field(default_factory=dict)


class OperateResponse(BaseModel):  # type: ignore[misc]
    """Response for ``POST /operate``.

    Attributes:
        operation_id: The new operation's unique identifier.
    """

    operation_id: str


class ApproveRequest(BaseModel):  # type: ignore[misc]
    """Body for ``POST /approve``.

    Attributes:
        approved_ids: List of proposal IDs the user accepted.
    """

    approved_ids: list[str] = Field(default_factory=list)


# ── REST endpoints ────────────────────────────────────────────────────


@operation_router.post("", status_code=201)  # type: ignore[untyped-decorator]
async def initiate_operation(project_id: str, body: OperateRequest) -> OperateResponse:
    """Initiate a new check or remediate operation for a project.

    Rejects with 409 if the project already has an active operation.

    Args:
        project_id: Target project UUID.
        body: Action and options payload.

    Returns:
        The new operation identifier.

    Raises:
        HTTPException: 404 if project not found, 409 if operation active.
    """
    from apme_gateway._galaxy_inject import load_galaxy_server_defs
    from apme_gateway.config import load_config

    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    registry = get_operation_registry()
    operation_id = uuid.uuid4().hex
    scan_id = uuid.uuid4().hex
    scan_type = body.action

    try:
        state = registry.create(
            operation_id=operation_id,
            project_id=proj.id,
            scan_id=scan_id,
            scan_type=scan_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    cfg = load_config()
    galaxy_servers = await load_galaxy_server_defs()

    task = asyncio.create_task(
        _drive_operation(
            operation_id=operation_id,
            project_id=proj.id,
            repo_url=proj.repo_url,
            branch=proj.branch,
            primary_address=cfg.primary_address,
            remediate=scan_type == "remediate",
            options=body.options,
            scan_id=scan_id,
            galaxy_servers=galaxy_servers,
            scm_token=proj.scm_token or cfg.scm_token,
        )
    )
    state.grpc_task = task
    registry.start_reaper()

    return OperateResponse(operation_id=operation_id)


@operation_router.get("")  # type: ignore[untyped-decorator]
async def get_operation_state(project_id: str) -> dict[str, Any]:
    """Return the current operation state snapshot for a project.

    Args:
        project_id: Target project UUID.

    Returns:
        Full serialised ``OperationState``.

    Raises:
        HTTPException: 404 if no operation exists for this project.
    """
    registry = get_operation_registry()
    state = registry.get_by_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No operation for this project")
    return state.to_snapshot()


@operation_router.post("/approve")  # type: ignore[untyped-decorator]
async def approve_proposals(project_id: str, body: ApproveRequest) -> dict[str, str]:
    """Submit approval decisions for AI proposals.

    Resolves the operation's ``approval_future`` so the background gRPC
    task can send the approval to Primary.

    Args:
        project_id: Target project UUID.
        body: Approved proposal IDs.

    Returns:
        Confirmation message.

    Raises:
        HTTPException: 404 if no operation, 409 if not in awaiting_approval.
    """
    registry = get_operation_registry()
    state = registry.get_by_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No operation for this project")
    if state.status != OperationStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Operation is in '{state.status.value}', not 'awaiting_approval'",
        )
    if state.approval_future is None or state.approval_future.done():
        raise HTTPException(status_code=409, detail="Approval already submitted")

    state.approval_future.set_result(body.approved_ids)
    return {"status": "approved"}


@operation_router.post("/cancel")  # type: ignore[untyped-decorator]
async def cancel_operation(project_id: str) -> dict[str, str]:
    """Cancel an in-flight operation.

    Args:
        project_id: Target project UUID.

    Returns:
        Confirmation message.

    Raises:
        HTTPException: 404 if no operation, 409 if already terminal.
    """
    registry = get_operation_registry()
    state = registry.get_by_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No operation for this project")
    if state.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Operation already in terminal state '{state.status.value}'",
        )
    if state.grpc_task and not state.grpc_task.done():
        state.grpc_task.cancel()
    if state.approval_future and not state.approval_future.done():
        state.approval_future.set_result([])
    registry.transition(state.operation_id, OperationStatus.CANCELLED)
    return {"status": "cancelled"}


@operation_router.post("/submit")  # type: ignore[untyped-decorator]
async def submit_operation(
    project_id: str,
    body: SubmitRequest | None = None,
) -> SubmitResponse:
    """Push patched files to a branch and optionally open a PR (ADR-050).

    This is the unified SCM submit endpoint that replaces the former
    ``/activity/{id}/pull-request`` and ``/operation/create-pr`` endpoints.

    Two modes:

    - **Live operation** (no ``activity_id``): uses the in-memory
      ``OperationRegistry`` for the project — requires a completed
      remediation operation.
    - **Historical activity** (``activity_id`` provided): loads patched
      files from the database — works for any past remediation that still
      has stored patches.

    Args:
        project_id: Target project UUID.
        body: Optional submit options (branch name, create_pr flag, etc.).

    Returns:
        Branch, commit SHA, optional PR URL, and provider.

    Raises:
        HTTPException: 404/409/422/502 depending on state.
    """
    from apme_gateway.config import load_config
    from apme_gateway.scm import detect_provider, get_provider

    if body is None:
        body = SubmitRequest()

    cfg = load_config()

    if body.activity_id:
        scan_id, state = body.activity_id, None
    else:
        scan_id, state = _resolve_live_operation(project_id)

    async with get_session() as db:
        scan = await q.get_scan(db, scan_id)
        if scan is None:
            if state:
                registry = get_operation_registry()
                registry.transition(state.operation_id, OperationStatus.COMPLETED)
            raise HTTPException(status_code=404, detail="Activity not found")

        if body.activity_id and scan.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail="Activity does not belong to this project",
            )

        if body.activity_id and scan.scan_type != "remediate":
            raise HTTPException(
                status_code=409,
                detail="Submit requires a remediate activity",
            )

        if scan.pr_url:
            if state:
                get_operation_registry().set_pr_url(state.operation_id, scan.pr_url)
            raise HTTPException(
                status_code=409,
                detail=f"PR already created for this activity: {scan.pr_url}",
            )

        project = await q.get_project(db, project_id)
        if project is None:
            if state:
                get_operation_registry().transition(
                    state.operation_id,
                    OperationStatus.COMPLETED,
                )
            raise HTTPException(status_code=404, detail="Project not found")

        patched = await q.get_patched_files(db, scan_id)
        if not patched:
            if state:
                get_operation_registry().transition(
                    state.operation_id,
                    OperationStatus.COMPLETED,
                )
            raise HTTPException(status_code=404, detail="No patched files found")

    if state:
        get_operation_registry().transition(
            state.operation_id,
            OperationStatus.SUBMITTING_PR,
        )

    inline_token = body.scm_token.strip() if body.scm_token else None
    token = inline_token or project.scm_token or cfg.scm_token
    if not token:
        if state:
            get_operation_registry().transition(
                state.operation_id,
                OperationStatus.COMPLETED,
            )
        raise HTTPException(status_code=422, detail="No SCM token configured")

    provider_type = project.scm_provider or detect_provider(project.repo_url)
    if not provider_type:
        if state:
            get_operation_registry().transition(
                state.operation_id,
                OperationStatus.COMPLETED,
            )
        raise HTTPException(
            status_code=422,
            detail=f"Cannot detect SCM provider from URL: {project.repo_url}",
        )

    api_base = cfg.github_api_url if provider_type == "github" else None
    try:
        provider = get_provider(provider_type, api_base_url=api_base)
    except ValueError as exc:
        if state:
            get_operation_registry().transition(
                state.operation_id,
                OperationStatus.COMPLETED,
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    short_id = scan_id[:8]
    branch_name = body.branch_name or f"apme/remediate-{short_id}"
    commit_title = body.title or f"fix: APME remediation — {scan.fixed_count} findings resolved"

    try:
        await provider.create_branch(project.repo_url, project.branch, branch_name, token)
        files = {pf.path: pf.content for pf in patched}
        commit_sha = await provider.push_files(
            project.repo_url,
            branch_name,
            files,
            commit_title,
            token,
        )

        pr_url: str | None = None
        if body.create_pr:
            pr_body = body.body or _build_pr_body(scan, patched)
            pr_result = await provider.create_pull_request(
                project.repo_url,
                project.branch,
                branch_name,
                commit_title,
                pr_body,
                token,
            )
            pr_url = pr_result.pr_url
    except Exception as exc:
        logger.exception("SCM provider error for project %s", project_id)
        if state:
            get_operation_registry().transition(
                state.operation_id,
                OperationStatus.COMPLETED,
                error=str(exc),
            )
        raise HTTPException(status_code=502, detail="SCM provider error") from exc

    if pr_url:
        async with get_session() as db:
            await q.set_scan_pr_url(db, scan_id, pr_url)
        if state:
            get_operation_registry().set_pr_url(state.operation_id, pr_url)
    elif state:
        get_operation_registry().transition(
            state.operation_id,
            OperationStatus.COMPLETED,
        )

    return SubmitResponse(
        branch_name=branch_name,
        commit_sha=commit_sha,
        pr_url=pr_url,
        provider=provider_type,
    )


def _resolve_live_operation(project_id: str) -> tuple[str, OperationState]:
    """Look up the live operation for a project and validate it.

    Args:
        project_id: Target project UUID.

    Returns:
        Tuple of (scan_id, operation_state).

    Raises:
        HTTPException: 404 if no operation, 409 if wrong state.
    """
    registry = get_operation_registry()
    state: OperationState | None = registry.get_by_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No active operation for this project")
    if state.status != OperationStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot submit: operation is '{state.status.value}', not 'completed'",
        )
    if state.scan_type != "remediate":
        raise HTTPException(status_code=409, detail="Submit requires a remediate operation")
    if not state.result or not state.result.patches:
        raise HTTPException(status_code=409, detail="No patches available for submission")
    return state.scan_id, state


# ── SSE endpoint ──────────────────────────────────────────────────────


@operation_router.get("/events")  # type: ignore[untyped-decorator]
async def operation_events(project_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time operation state.

    On connect, sends a ``snapshot`` event with the full current state.
    Then streams delta events (``status_changed``, ``progress``,
    ``proposals``, ``result``, ``pr_created``) until the operation
    reaches a terminal state or the client disconnects.

    Args:
        project_id: Target project UUID.
        request: The incoming HTTP request (for disconnect detection).

    Returns:
        SSE streaming response.

    Raises:
        HTTPException: 404 if no operation exists for this project.
    """
    registry = get_operation_registry()
    state = registry.get_by_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No operation for this project")

    queue = registry.subscribe(state.operation_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Operation not found")

    async def _event_stream() -> Any:
        try:
            snapshot = state.to_snapshot()
            yield _sse_format("snapshot", snapshot)

            if state.status in TERMINAL_STATUSES:
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                if msg.get("_close"):
                    break

                event_type = msg.get("event", "message")
                data = msg.get("data", {})
                yield _sse_format(event_type, data)

                if data.get("status") in {s.value for s in TERMINAL_STATUSES}:
                    break
        finally:
            registry.unsubscribe(state.operation_id, queue)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_format(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE message.

    Args:
        event: SSE event type.
        data: JSON-serialisable payload.

    Returns:
        Formatted SSE string with ``event:`` and ``data:`` lines.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ── PR body builder ───────────────────────────────────────────────────


def _build_pr_body(scan: Scan, patched_files: Sequence[PatchedFile]) -> str:
    """Generate a Markdown PR body from scan data (ADR-050).

    Args:
        scan: The Scan ORM row.
        patched_files: PatchedFile rows for this activity.

    Returns:
        Markdown string.
    """
    lines: list[str] = [
        "## APME Automated Remediation",
        "",
        f"**Findings resolved:** {scan.fixed_count}",
        f"**Total violations (before):** {scan.total_violations}",
        f"**Scan type:** {scan.scan_type}",
        "",
        "### Files modified",
        "",
    ]
    for pf in patched_files:
        lines.append(f"- `{pf.path}`")
    lines.extend(
        [
            "",
            "---",
            "*This PR was auto-generated by [APME](https://github.com/ansible/apme).*",
        ]
    )
    return "\n".join(lines)


# ── Operation driver (replaces WebSocket tunnel logic) ────────────────

_SCAN_PERSIST_WAIT_S = 60.0
_SCAN_PERSIST_POLL_S = 0.25


async def finalize_operation_scan(
    *,
    project_id: str,
    scan_id: str,
    scan_type: str,
    clone_commit: str,
    captured_patches: list[dict[str, str]],
    ai_proposed_count: int,
    ai_declined_count: int,
    ai_accepted_count: int,
) -> None:
    """Link a completed operation to its persisted scan row and store patches.

    The engine commits the scan via ``ReportFixCompleted`` asynchronously. If
    the gateway tries to insert ``scan_patches`` before that row exists, SQLite
    raises a foreign-key error. Poll until the scan row appears or timeout.

    Args:
        project_id: Owning project UUID.
        scan_id: Engine scan identifier.
        scan_type: ``check`` or ``remediate``.
        clone_commit: Cloned repo HEAD SHA (may be empty).
        captured_patches: Per-file diffs from the session result event.
        ai_proposed_count: AI proposals offered during the operation.
        ai_declined_count: AI proposals declined by the engine.
        ai_accepted_count: AI proposals approved by the user.

    Raises:
        TimeoutError: When the reporting servicer never persisted the scan row.
    """
    deadline = time.monotonic() + _SCAN_PERSIST_WAIT_S

    while time.monotonic() < deadline:
        async with get_session() as db:
            scan = await q.get_scan(db, scan_id)
            if scan is not None:
                if clone_commit:
                    await q.update_project_commit(db, project_id, clone_commit)
                await q.link_scan_to_project(
                    db,
                    scan_id,
                    project_id,
                    trigger="ui",
                    scan_type=scan_type,
                    source="gateway",
                )
                await q.update_ai_counts(
                    db,
                    scan_id,
                    ai_proposed=ai_proposed_count,
                    ai_declined=ai_declined_count,
                    ai_accepted=ai_accepted_count,
                )
                if captured_patches and not scan.patches:
                    await q.store_patches(db, scan_id, captured_patches)
                await q.update_project_health(db, project_id)
                return

        await asyncio.sleep(_SCAN_PERSIST_POLL_S)

    raise TimeoutError(
        f"Scan {scan_id} was not persisted within {_SCAN_PERSIST_WAIT_S:.0f}s (ReportFixCompleted may have failed)"
    )


async def _drive_operation(
    *,
    operation_id: str,
    project_id: str,
    repo_url: str,
    branch: str,
    primary_address: str,
    remediate: bool,
    options: dict[str, Any],
    scan_id: str,
    galaxy_servers: Any = None,
    scm_token: str | None = None,
) -> None:
    """Background task that clones the repo and drives Primary's FixSession.

    Updates the ``OperationRegistry`` state throughout. Runs independently
    of any browser connection.

    Args:
        operation_id: Registry operation identifier.
        project_id: Owning project UUID.
        repo_url: SCM clone URL.
        branch: Branch to clone.
        primary_address: ``host:port`` for Primary gRPC.
        remediate: Whether this is a remediation.
        options: Client-supplied options.
        scan_id: Engine scan identifier.
        galaxy_servers: Galaxy server defs.
        scm_token: Optional SCM token for private repository access.
    """
    from apme_gateway.scan.driver import fetch_remote_head, run_project_operation

    registry = get_operation_registry()

    try:
        await fetch_remote_head(repo_url, branch, scm_token=scm_token)

        registry.transition(operation_id, OperationStatus.CLONING)

        ai_proposed_count = 0
        ai_declined_count = 0
        ai_accepted_count = 0
        captured_patches: list[dict[str, str]] = []

        async def _progress_cb(event: object) -> None:
            """Translate gRPC SessionEvent into registry updates.

            Args:
                event: gRPC SessionEvent protobuf.
            """
            nonlocal ai_proposed_count, ai_declined_count, ai_accepted_count

            kind = None
            with contextlib.suppress(Exception):
                kind = event.WhichOneof("event")  # type: ignore[attr-defined]

            if kind == "progress":
                prog = event.progress  # type: ignore[attr-defined]
                entry = ProgressEntry(
                    phase=prog.phase or "processing",
                    message=prog.message or "",
                    timestamp=_now_iso(),
                    progress=prog.progress if prog.progress is not None else None,
                    level=prog.level if prog.level is not None else None,
                )
                if registry.get(operation_id) and registry.get(operation_id).status == OperationStatus.CLONING:  # type: ignore[union-attr]
                    registry.transition(operation_id, OperationStatus.SCANNING)
                registry.add_progress(operation_id, entry)

            elif kind == "proposals":
                props = event.proposals  # type: ignore[attr-defined]
                items = [
                    Proposal(
                        id=p.id,
                        rule_id=p.rule_id,
                        file=p.file,
                        tier=p.tier,
                        confidence=p.confidence,
                        explanation=p.explanation,
                        diff_hunk=p.diff_hunk,
                        status=p.status or "proposed",
                        suggestion=p.suggestion,
                        line_start=p.line_start,
                    )
                    for p in props.proposals
                ]
                ai_proposed_count = sum(1 for i in items if i.status != "declined")
                ai_declined_count = sum(1 for i in items if i.status == "declined")
                registry.set_proposals(operation_id, items)

            elif kind == "approval_ack":
                ack = event.approval_ack  # type: ignore[attr-defined]
                ai_accepted_count = getattr(ack, "applied_count", 0)
                registry.transition(operation_id, OperationStatus.APPLYING)

            elif kind == "result":
                res = event.result  # type: ignore[attr-defined]
                report = getattr(res, "report", None)
                remaining = getattr(res, "remaining_violations", [])
                fixed_viols = getattr(res, "fixed_violations", [])
                fixed = report.fixed if report else 0
                total = len(remaining) + fixed

                def _extract_line(v: object) -> int | None:
                    if v.HasField("line"):  # type: ignore[attr-defined]
                        return v.line  # type: ignore[attr-defined, no-any-return]
                    if v.HasField("line_range"):  # type: ignore[attr-defined]
                        return v.line_range.start  # type: ignore[attr-defined, no-any-return]
                    return None

                fixed_violations_json = [
                    {
                        "rule_id": v.rule_id,
                        "severity": severity_to_label(severity_from_proto(v.severity)),
                        "message": v.message,
                        "file": v.file,
                        "line": _extract_line(v),
                        "path": v.path,
                    }
                    for v in fixed_viols
                ]

                result_patches = getattr(res, "patches", [])
                patches_json = [{"file": p.path, "diff": p.diff} for p in result_patches if p.diff]
                captured_patches.extend(patches_json)

                remediated = fixed if remediate else 0
                remaining_count = len(remaining)

                op_result = OperationResult(
                    total_violations=total,
                    fixable=fixed,
                    ai_proposed=ai_proposed_count,
                    ai_declined=ai_declined_count,
                    ai_accepted=ai_accepted_count,
                    manual_review=remaining_count if remediate else (report.remaining_manual if report else 0),
                    remediated_count=remediated,
                    fixed_violations=fixed_violations_json,
                    patches=patches_json,
                )
                registry.set_result(operation_id, op_result)

        raw_specs = options.get("collection_specs", [])
        specs = [str(s) for s in raw_specs] if isinstance(raw_specs, list) else []

        approval_queue: asyncio.Queue[list[str]] | None = None
        bridge_task: asyncio.Task[None] | None = None

        if remediate:
            approval_queue = asyncio.Queue()

            async def _approval_bridge() -> None:
                """Bridge registry approval_future to the driver's approval_queue."""
                while True:
                    op = registry.get(operation_id)
                    if op is None or op.status in TERMINAL_STATUSES:
                        break
                    if op.status == OperationStatus.AWAITING_APPROVAL and op.approval_future is not None:
                        try:
                            ids = await op.approval_future
                            if approval_queue is not None:
                                await approval_queue.put(ids)
                            op.approval_future = None
                        except asyncio.CancelledError:
                            break
                    else:
                        await asyncio.sleep(0.1)

            bridge_task = asyncio.create_task(_approval_bridge())

        _, result, clone_commit = await run_project_operation(
            project_id=project_id,
            repo_url=repo_url,
            branch=branch,
            primary_address=primary_address,
            remediate=remediate,
            ansible_version=str(options.get("ansible_version", "")),
            collection_specs=specs,
            enable_ai=bool(options.get("enable_ai", False)),
            ai_model=str(options.get("ai_model", "")),
            progress_callback=_progress_cb,
            approval_queue=approval_queue,
            scan_id=scan_id,
            galaxy_servers=galaxy_servers or None,
            scm_token=scm_token,
        )

        if bridge_task is not None:
            bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge_task

        op = registry.get(operation_id)
        if op is not None:
            op.clone_commit = clone_commit

        scan_type_str = "remediate" if remediate else "check"
        await finalize_operation_scan(
            project_id=project_id,
            scan_id=scan_id,
            scan_type=scan_type_str,
            clone_commit=clone_commit,
            captured_patches=captured_patches,
            ai_proposed_count=ai_proposed_count,
            ai_declined_count=ai_declined_count,
            ai_accepted_count=ai_accepted_count,
        )

        op = registry.get(operation_id)
        if op is not None and op.status not in TERMINAL_STATUSES:
            registry.transition(operation_id, OperationStatus.COMPLETED)

    except asyncio.CancelledError:
        registry.transition(operation_id, OperationStatus.CANCELLED)
    except Exception as exc:
        logger.exception("Operation %s failed", operation_id[:12])
        registry.transition(operation_id, OperationStatus.FAILED, error=str(exc))
