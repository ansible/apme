"""Atomic operate endpoint (extracted from operation_router, Group B #20).

Pure move of the ``POST /api/v1/projects/{project_id}/operate`` router
plus ``_auto_drive_atomic_gates``, ``_wait_for_terminal_snapshot``, and
``atomic_operate``. ``operation_router.py`` re-exports these names so
existing imports (app wiring, tests patching
``operation_router._drive_operation``) keep working.

``_drive_operation`` and ``submit_operation`` stay in
``operation_router`` and are accessed lazily via module attribute (not
top-level import) so test patches on the ``operation_router`` module are
honored and no import cycle forms.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from apme_gateway.api._request import _RequestOpt  # noqa: F401 -- single definition (#30)
from apme_gateway.api.schemas import AtomicOperateRequest, SubmitRequest
from apme_gateway.api.submit_idempotency import (
    _coded_detail,
    _effective_submit_token,
    _is_valid_submit_token,
)
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.operation_registry import get_operation_registry
from apme_gateway.operation_types import TERMINAL_STATUSES, OperationStatus

logger = logging.getLogger(__name__)


# ── Atomic operate endpoint (agent operability, additive) ─────────────
# POST /api/v1/projects/{project_id}/operate runs check/remediate with
# server-side gate automation and returns the terminal snapshot. The
# stepped flow (POST /operation + /approve + /begin-remediate +
# /escalate-ai + /submit + SSE) is unchanged.

operate_router = APIRouter(prefix="/api/v1/projects/{project_id}")

_ATOMIC_OPERATE_POLL_S = 0.5
_ATOMIC_OPERATE_TIMEOUT_S = 1800.0


def _resolve_future_quietly[FutureT](fut: asyncio.Future[FutureT], value: FutureT) -> bool:
    """Set an asyncio future result, tolerating a lost set-result race.

    Gate futures are resolved from multiple paths (auto-drive, operator
    POST, timeout retirement). A ``done()`` check followed by
    ``set_result`` can still race — the loser gets ``InvalidStateError``.
    That is benign (the DB commit already happened; exactly one winner
    woke the driver), so it is swallowed instead of logged as an error.

    Args:
        fut: Future to resolve.
        value: Result value to set.

    Returns:
        True when this call resolved the future.
    """
    try:
        if fut.done():
            return False
        fut.set_result(value)
    except asyncio.InvalidStateError:
        # Lost the race with a concurrent resolver (e.g. the gate was
        # DB-committed and retired by the timeout path first). The driver
        # was already woken; nothing left to do.
        logger.debug("Gate future already resolved; skipping duplicate set_result")
        return False
    return True


async def _auto_drive_atomic_gates(
    *,
    operation_id: str,
    auto_approve_tier1: bool,
    auto_approve_ai: bool,
) -> None:
    """Resolve approval gates server-side for one atomic operation.

    Polls the registry until the operation reaches a terminal state:
    - ASSESSED with a pending begin future is auto-begun (atomic never pauses).
    - AWAITING_AI_TRIAGE with a pending escalate future is auto-escalated:
      allow-all when auto_approve_ai, else skip-AI (empty targets).
    - AWAITING_APPROVAL gates are auto-resolved: approve all offered ids
      when the gate tier matches the auto-approve flags, else decline-all
      via the same gate-commit path as POST /approve.

    Args:
        operation_id: Registry operation identifier.
        auto_approve_tier1: Auto-approve Tier 1 (tier < 2) proposals.
        auto_approve_ai: Auto-escalate AI and auto-approve AI (tier >= 2) proposals.
    """
    from apme_gateway.proposals.draft import commit_gate_decisions  # noqa: PLC0415

    registry = get_operation_registry()
    while True:
        op = registry.get(operation_id)
        if op is None or op.status in TERMINAL_STATUSES:
            return
        try:
            if op.status == OperationStatus.ASSESSED and op.begin_remediate_future is not None:
                begin_fut = op.begin_remediate_future
                if not begin_fut.done():
                    op.scan_type = "remediate"
                    _resolve_future_quietly(begin_fut, None)
            elif op.status == OperationStatus.AWAITING_AI_TRIAGE and op.escalate_ai_future is not None:
                escalate_fut = op.escalate_ai_future
                if not escalate_fut.done():
                    if auto_approve_ai:
                        candidates = list(op.ai_triage_candidates or [])
                        # Group by path while retaining each candidate's
                        # rule IDs so escalation stays scoped (an empty
                        # rule_ids would widen to the whole path).
                        grouped: dict[str, list[str]] = {}
                        for c in candidates:
                            path = str(c.get("path") or "")
                            if not path:
                                continue
                            rule_id = str(c.get("rule_id") or "")
                            bucket = grouped.setdefault(path, [])
                            if rule_id and rule_id not in bucket:
                                bucket.append(rule_id)
                        targets: list[dict[str, Any]] = [
                            {"path": path, "rule_ids": list(rule_ids)} for path, rule_ids in grouped.items()
                        ]
                        _resolve_future_quietly(escalate_fut, targets)
                    else:
                        _resolve_future_quietly(escalate_fut, [])
                    registry.transition(operation_id, OperationStatus.APPLYING)
            elif op.status == OperationStatus.AWAITING_APPROVAL and op.approval_gate is not None:
                gate = op.approval_gate
                if not gate.future.done():
                    proposals = list(op.proposals or [])
                    tier2 = [p for p in proposals if p.tier >= 2]
                    tier1 = [p for p in proposals if p.tier < 2]
                    approved: list[str] = []
                    if auto_approve_tier1:
                        approved.extend(p.id for p in tier1)
                    if auto_approve_ai:
                        approved.extend(p.id for p in tier2)
                    async with op.approval_gate_lock:
                        if op.approval_gate is not gate or gate.future.done():
                            pass
                        else:
                            offered = [p.id for p in proposals]
                            async with get_session() as db:
                                await commit_gate_decisions(
                                    db,
                                    scan_id=op.scan_id,
                                    project_id=op.project_id,
                                    approved_engine_ids=approved,
                                    offered_engine_ids=offered,
                                )
                                await db.commit()
                            # The DB commit above is durable; a raced
                            # duplicate resolver (timeout path) may have
                            # completed the future first — tolerated quietly.
                            _resolve_future_quietly(gate.future, approved)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Atomic auto-drive failed for operation %s", operation_id[:12])
        await asyncio.sleep(_ATOMIC_OPERATE_POLL_S)


async def _wait_for_terminal_snapshot(
    *,
    operation_id: str,
    timeout_s: float = _ATOMIC_OPERATE_TIMEOUT_S,
) -> dict[str, Any]:
    """Poll the registry until the operation is terminal, then snapshot it.

    Args:
        operation_id: Registry operation identifier.
        timeout_s: Maximum seconds to wait.

    Returns:
        Terminal ``OperationState.to_snapshot()`` dict.

    Raises:
        TimeoutError: When the operation is not terminal in time.
    """
    import time as _time

    registry = get_operation_registry()
    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        op = registry.get(operation_id)
        if op is not None and op.status in TERMINAL_STATUSES:
            return op.to_snapshot()
        await asyncio.sleep(_ATOMIC_OPERATE_POLL_S)
    op = registry.get(operation_id)
    if op is not None and op.status in TERMINAL_STATUSES:
        return op.to_snapshot()
    msg = f"Operation {operation_id} did not reach a terminal state in time"
    raise TimeoutError(msg)


@operate_router.post("/operate", status_code=200)  # type: ignore[untyped-decorator]
async def atomic_operate(
    project_id: str,
    body: AtomicOperateRequest | None = None,
    request: _RequestOpt = None,
) -> dict[str, Any]:
    """Run check/remediate atomically with server-side gates (additive).

    Drives begin-remediate, AI escalation, and approval gates server-side
    from ``options.{auto_approve_tier1,auto_approve_ai}`` and optionally
    submits (branch/PR) from ``options.submit``. Blocks until the
    operation reaches a terminal state and returns that snapshot. The
    stepped ``/operation`` flow is unchanged. A missing body defaults to
    ``{"action": "check"}`` (read-only).

    The terminal wait is capped at ``_ATOMIC_OPERATE_TIMEOUT_S`` (1800s);
    exceeding it surfaces HTTP 504 and cancels the drive task so no
    orphaned Engine stream survives the request. The embedded submit
    propagates idempotency: the caller's ``Idempotency-Key`` header (or
    ``options.submit.submit_token``) is forwarded as the submit token, so
    retrying the atomic call replays the stored submit result instead of
    pushing twice. Replay keys on ``(project_id, token)`` alone when the
    stored entry already carries a completed submit result — a fresh
    ``scan_id`` per atomic attempt does not force a 409 when
    branch/activity bindings match. Submit inputs (idempotency token
    entropy, branch-name shape) are validated up front with 400/422
    before any operation is created.

    Args:
        project_id: Target project UUID.
        body: Atomic action + options payload (defaults to check).
        request: Incoming request (for ``Idempotency-Key`` header forwarding
            to the embedded submit).

    Returns:
        Terminal operation snapshot (includes ``result`` and optional
        ``pr_url`` when ``options.submit`` was requested; additive
        ``submit_error`` when the embedded submit failed).

    Raises:
        HTTPException: 404 if project not found; 400 on weak idempotency
            token; 422 on invalid submit branch name; 409 on active
            operation or draft working set without abandon opt-in; 504 on
            timeout.
        asyncio.CancelledError: Re-raised on client disconnect (never
            swallowed by submit-error handling).
    """
    from apme_gateway._galaxy_inject import load_galaxy_server_defs
    from apme_gateway.config import load_config

    if body is None:
        req = AtomicOperateRequest()
    elif isinstance(body, AtomicOperateRequest):
        req = body
    else:
        req = AtomicOperateRequest.model_validate(body)
    action = req.action
    opts = req.options
    auto_tier1 = bool(opts.auto_approve_tier1)
    auto_ai = bool(opts.auto_approve_ai)
    enable_ai = bool(opts.enable_ai or auto_ai)

    # Boundary validation BEFORE registry.create / long wait so bad submit
    # inputs fail fast with 400/422 instead of after the full operation.
    # Mirrors POST /submit rules: token entropy via _is_valid_submit_token,
    # branch-name shape via the shared SubmitRequest validator.
    _early_submit: SubmitRequest | None = None
    if opts.submit is not None:
        from pydantic import ValidationError as _AtomicValidationError

        _early_token = _effective_submit_token(request, opts.submit.submit_token)
        if _early_token is not None and not _is_valid_submit_token(_early_token):
            raise HTTPException(
                status_code=400,
                detail=_coded_detail(
                    "invalid_idempotency_token",
                    "Idempotency token must be uuid-hex with at least 32 hex chars (128-bit).",
                ),
            )
        try:
            _early_submit = SubmitRequest(
                branch_name=opts.submit.branch_name,
                create_pr=bool(opts.submit.create_pr),
                submit_token=_early_token,
            )
        except _AtomicValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    registry = get_operation_registry()
    scan_type = action
    # Shared initiation preamble with the stepped flow (#19): locking,
    # 409 conflict codes, and draft semantics live in one helper.
    from apme_gateway.api.operation_router import (  # noqa: PLC0415
        _claim_project_operation as _claim_atomic_operation,
    )

    operation_id, scan_id, state = await _claim_atomic_operation(proj.id, action, req.abandon_working_set)

    cfg = load_config()
    galaxy_servers = await load_galaxy_server_defs()
    drive_options: dict[str, Any] = {
        "ansible_version": opts.ansible_version,
        "collection_specs": list(opts.collection_specs),
        "enable_ai": enable_ai,
        "ai_model": opts.ai_model,
        "interactive": False,
        "assess_pause": False,
    }
    # Lazy module-attribute access so tests patching
    # operation_router._drive_operation affect the atomic path (no cycle:
    # operation_router imports this module at top level; this module only
    # touches operation_router lazily at call time).
    from apme_gateway.api import operation_router as _op_router_mod

    task = asyncio.create_task(
        _op_router_mod._drive_operation(
            operation_id=operation_id,
            project_id=proj.id,
            repo_url=proj.repo_url,
            branch=proj.branch,
            engine_address=cfg.engine_address,
            remediate=scan_type == "remediate",
            options=drive_options,
            scan_id=scan_id,
            galaxy_servers=galaxy_servers,
            scm_token=proj.scm_token or cfg.scm_token,
            scm_provider=proj.scm_provider,
        )
    )
    state.grpc_task = task
    registry.start_reaper()
    auto_task = asyncio.create_task(
        _auto_drive_atomic_gates(
            operation_id=operation_id,
            auto_approve_tier1=auto_tier1,
            auto_approve_ai=auto_ai,
        )
    )
    try:
        try:
            snapshot = await _wait_for_terminal_snapshot(operation_id=operation_id)
        finally:
            # No orphans on ANY exit (success, timeout, client
            # disconnect/CancelledError, or unexpected error): retire the
            # auto-drive and cancel a still-pending Engine drive.
            # Cancelling a done task is a no-op.
            if not auto_task.done():
                auto_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await auto_task
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except TimeoutError as exc:
        # Retire the stranded operation (#9): without a terminal
        # transition the project stays blocked behind a 409
        # "already has an active operation" while the cancelled drive
        # task will never complete it. FAILED releases the project
        # for an explicit retry instead of stranding it.
        try:
            registry.transition(
                operation_id,
                OperationStatus.FAILED,
                error=str(exc),
            )
        except Exception:
            logger.warning(
                "Failed to retire timed-out atomic operation %s",
                operation_id,
                exc_info=True,
            )
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    submit_opts = opts.submit
    if submit_opts is not None and snapshot.get("status") == OperationStatus.COMPLETED.value:
        # Reuse the boundary-validated submit body (token + branch shape
        # already checked before the operation started); re-resolve the
        # header token in case it changed mid-flight is unnecessary —
        # prefer the early value for consistency.
        submit_body = _early_submit or SubmitRequest(
            branch_name=submit_opts.branch_name,
            create_pr=bool(submit_opts.create_pr),
            submit_token=_effective_submit_token(request, submit_opts.submit_token),
        )
        try:
            await _op_router_mod.submit_operation(proj.id, submit_body, request)
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            # Propagate submit failures into the snapshot (additive
            # ``submit_error``) instead of logger.warning-only so agents
            # can branch without a second fetch. Terminal status is kept.
            detail = exc.detail
            if isinstance(detail, dict):
                submit_error = str(detail.get("message") or detail.get("code") or exc.status_code)
            else:
                submit_error = str(detail)
            logger.warning(
                "Atomic operate submit failed for project %s: %s",
                proj.id[:12],
                submit_error,
            )
            op = registry.get(operation_id)
            if op is not None:
                op.submit_error = submit_error
                snapshot = op.to_snapshot()
            else:
                snapshot["submit_error"] = submit_error
        except Exception as exc:  # noqa: BLE001 -- preserve snapshot on non-HTTP faults
            # Non-HTTP submit faults (DB errors, provider errors,
            # RuntimeError from session handling) must not discard the
            # completed operate snapshot into a bare 500 (#20).
            submit_error = f"internal submit error: {type(exc).__name__}"
            logger.warning(
                "Atomic operate submit failed for project %s: %s",
                proj.id[:12],
                submit_error,
                exc_info=True,
            )
            op = registry.get(operation_id)
            if op is not None:
                op.submit_error = submit_error
                snapshot = op.to_snapshot()
            else:
                snapshot["submit_error"] = submit_error
        else:
            op = registry.get(operation_id)
            if op is not None:
                snapshot = op.to_snapshot()
    return snapshot
