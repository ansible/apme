"""Adaptive operation deadline estimation and enforcement (ADR-068).

Computes work-based budgets for long-running FixSession operations and
defines stall detection independent of total budget.
"""

from __future__ import annotations

import logging
import math
import os

from apme_engine.engine.models import ViolationDict

logger = logging.getLogger("apme.deadline")


def _parse_int_env(name: str, default: int) -> int:
    """Parse an integer environment variable with a safe fallback.

    Args:
        name: Environment variable name.
        default: Value to use when unset or invalid.

    Returns:
        Parsed integer, or ``default`` on missing/invalid input.
    """
    raw = os.environ.get(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default


_DEFAULT_SCAN_BASE = _parse_int_env("APME_OPERATION_SCAN_BASE", 300)
_DEFAULT_MIN_BUDGET = _parse_int_env("APME_OPERATION_MIN_BUDGET", 600)
_DEFAULT_MAX_BUDGET = _parse_int_env("APME_SESSION_MAX_LIFETIME", 7200)
_DEFAULT_PER_CALL_TIMEOUT = _parse_int_env("APME_OPERATION_PER_CALL_TIMEOUT", 60)
_DEFAULT_STALL_SECONDS = _parse_int_env("APME_OPERATION_STALL_SECONDS", 600)
# Safe floor when operator budget bounds are invalid (ADR-068).
FALLBACK_NON_AI_OPERATION_BUDGET = 600


def parse_ai_concurrency(raw: str | None = None) -> int:
    """Parse ``APME_AI_CONCURRENCY`` with a safe default of 4 (ADR-068).

    Args:
        raw: Optional override; when omitted, reads ``APME_AI_CONCURRENCY``.

    Returns:
        Parsed concurrency clamped to at least 1.
    """
    value = raw if raw is not None else os.environ.get("APME_AI_CONCURRENCY", "4")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid APME_AI_CONCURRENCY=%r; using default 4", value)
        return 4
    if parsed < 1:
        logger.warning("APME_AI_CONCURRENCY=%s must be >= 1; using 1", parsed)
        return 1
    return parsed


_DEFAULT_AI_CONCURRENCY = parse_ai_concurrency()
# Lightweight Tier 1 estimate: seconds per violation (ADR-068).
_DEFAULT_TIER1_SECONDS_PER_VIOLATION = _parse_int_env("APME_OPERATION_TIER1_PER_VIOLATION", 2)
_DEFAULT_TIER1_MARGIN_CAP = _parse_int_env("APME_OPERATION_TIER1_MARGIN_CAP", 600)


class BudgetConfigError(ValueError):
    """Invalid operator configuration for budget estimation."""


class OperationDeadlineError(Exception):
    """Raised when an operation exceeds its budget or stalls without progress."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize with a machine-readable code and message.

        Args:
            code: Error identifier (e.g. ``operation_budget_exceeded``).
            message: Human-readable detail.
        """
        self.code = code
        super().__init__(message)


def count_ai_nodes(violations: list[ViolationDict]) -> int:
    """Count distinct graph node IDs in a violation list.

    Args:
        violations: Violations with optional ``path`` (node_id).

    Returns:
        Number of unique non-empty node paths.
    """
    return len({str(v.get("path", "")) for v in violations if str(v.get("path", ""))})


def _validate_ai_budget_inputs(
    *,
    concurrency: int,
    per_call_timeout: int,
    max_ai_attempts: int,
) -> None:
    """Fail fast on invalid AI budget configuration (ADR-068).

    Args:
        concurrency: Parallel AI calls (must be > 0).
        per_call_timeout: Per-call timeout seconds (must be > 0).
        max_ai_attempts: AI resubmission cap (must be >= 1).

    Raises:
        BudgetConfigError: When any input is out of range.
    """
    if concurrency <= 0:
        msg = f"concurrency must be > 0 (got {concurrency})"
        raise BudgetConfigError(msg)
    if per_call_timeout <= 0:
        msg = f"per_call_timeout must be > 0 (got {per_call_timeout})"
        raise BudgetConfigError(msg)
    if max_ai_attempts < 1:
        msg = f"max_ai_attempts must be >= 1 (got {max_ai_attempts})"
        raise BudgetConfigError(msg)


def _clamp_budget(
    raw: int,
    *,
    min_budget: int | None = None,
    max_budget: int | None = None,
) -> int:
    floor = min_budget if min_budget is not None else _DEFAULT_MIN_BUDGET
    ceiling = max_budget if max_budget is not None else _DEFAULT_MAX_BUDGET
    if floor > ceiling:
        msg = f"min_budget ({floor}) must be <= max_budget ({ceiling})"
        raise BudgetConfigError(msg)
    return max(floor, min(ceiling, raw))


def estimate_tier1_margin(violation_count: int) -> int:
    """Lightweight Tier 1 time estimate from violation count.

    Args:
        violation_count: Number of violations to remediate.

    Returns:
        Estimated Tier 1 margin seconds (capped).
    """
    if violation_count <= 0:
        return 0
    return min(
        _DEFAULT_TIER1_MARGIN_CAP,
        violation_count * _DEFAULT_TIER1_SECONDS_PER_VIOLATION,
    )


def estimate_non_ai_budget(
    *,
    violation_count: int = 0,
    scan_base: int | None = None,
    min_budget: int | None = None,
    max_budget: int | None = None,
) -> int:
    """Estimate budget for check / non-AI remediate phases (ADR-068).

    Args:
        violation_count: Violation count for tier1 margin estimate.
        scan_base: Override scan base seconds.
        min_budget: Override minimum budget floor.
        max_budget: Override maximum budget ceiling.

    Returns:
        Clamped non-AI operation budget in seconds.
    """
    base = scan_base if scan_base is not None else _DEFAULT_SCAN_BASE
    tier1_margin = estimate_tier1_margin(violation_count)
    margin = max(60, int((base + tier1_margin) * 0.10))
    raw = base + tier1_margin + margin
    return _clamp_budget(raw, min_budget=min_budget, max_budget=max_budget)


def estimate_ai_budget(
    *,
    ai_node_count: int,
    concurrency: int | None = None,
    per_call_timeout: int | None = None,
    max_ai_attempts: int = 2,
    min_budget: int | None = None,
    max_budget: int | None = None,
) -> int:
    """Estimate AI-phase budget at the AI gate (excludes scan_base; ADR-068).

    Args:
        ai_node_count: Distinct graph nodes requiring AI.
        concurrency: Parallel AI calls (default env).
        per_call_timeout: Per-call timeout seconds (default env).
        max_ai_attempts: AI resubmission cap.
        min_budget: Override minimum budget floor.
        max_budget: Override maximum budget ceiling.

    Returns:
        Clamped AI-only operation budget in seconds.
    """
    conc = concurrency if concurrency is not None else _DEFAULT_AI_CONCURRENCY
    per_call = per_call_timeout if per_call_timeout is not None else _DEFAULT_PER_CALL_TIMEOUT
    _validate_ai_budget_inputs(
        concurrency=conc,
        per_call_timeout=per_call,
        max_ai_attempts=max_ai_attempts,
    )
    batches = math.ceil(ai_node_count / conc) if ai_node_count > 0 else 0
    ai_budget = batches * per_call * max_ai_attempts
    margin = max(60, int(ai_budget * 0.10))
    raw = ai_budget + margin
    return _clamp_budget(raw, min_budget=min_budget, max_budget=max_budget)


def estimate_operation_budget(
    *,
    ai_node_count: int = 0,
    concurrency: int | None = None,
    per_call_timeout: int | None = None,
    max_ai_attempts: int = 2,
    violation_count: int = 0,
    scan_base: int | None = None,
    min_budget: int | None = None,
    max_budget: int | None = None,
) -> int:
    """Estimate combined budget (non-AI + AI when nodes present).

    Prefer ``estimate_non_ai_budget`` / ``estimate_ai_budget`` for phase-aware
    enforcement.

    Args:
        ai_node_count: Distinct graph nodes requiring AI.
        concurrency: Parallel AI calls (default env).
        per_call_timeout: Per-call timeout seconds (default env).
        max_ai_attempts: AI resubmission cap.
        violation_count: Violation count for tier1 margin estimate.
        scan_base: Override scan base seconds.
        min_budget: Override minimum budget floor.
        max_budget: Override maximum budget ceiling.

    Returns:
        Clamped combined operation budget in seconds.
    """
    non_ai = estimate_non_ai_budget(
        violation_count=violation_count,
        scan_base=scan_base,
        min_budget=min_budget,
        max_budget=max_budget,
    )
    if ai_node_count <= 0:
        return non_ai
    ai = estimate_ai_budget(
        ai_node_count=ai_node_count,
        concurrency=concurrency,
        per_call_timeout=per_call_timeout,
        max_ai_attempts=max_ai_attempts,
        min_budget=min_budget,
        max_budget=max_budget,
    )
    return _clamp_budget(non_ai + ai, min_budget=min_budget, max_budget=max_budget)


def operation_deadline_mono(
    *,
    operation_started_at: float,
    operation_budget_s: int,
    max_lifetime_deadline_mono: float,
) -> float:
    """Return the effective monotonic deadline for the current phase.

    Args:
        operation_started_at: Phase start in monotonic time.
        operation_budget_s: Phase budget in seconds.
        max_lifetime_deadline_mono: Absolute session cap in monotonic time.

    Returns:
        Effective deadline in monotonic time.
    """
    phase_deadline = operation_started_at + operation_budget_s
    if max_lifetime_deadline_mono <= 0:
        return phase_deadline
    return min(phase_deadline, max_lifetime_deadline_mono)


def stall_window(operation_budget_s: int) -> int:
    """Return seconds without progress before declaring a stall.

    Args:
        operation_budget_s: Total operation budget.

    Returns:
        Stall threshold in seconds (default 10 min cap; scales down for short budgets).
    """
    return min(_DEFAULT_STALL_SECONDS, operation_budget_s)


def is_task_linked_progress(phase: str) -> bool:
    """Return whether a progress phase counts as task-linked work.

    Heartbeats keep the stream alive and refresh idle TTL but must not
    reset the stall clock (ADR-068).

    Args:
        phase: ``ProgressUpdate.phase`` value.

    Returns:
        True when the event reflects substantive operation progress.
    """
    return phase not in ("heartbeat", "")


def check_operation_deadline(
    *,
    operation_budget_s: int,
    operation_started_at: float,
    last_progress_at: float,
    now: float,
    max_lifetime_deadline_mono: float = 0.0,
) -> OperationDeadlineError | None:
    """Return an error when budget, stall, or session lifetime limits are exceeded.

    Args:
        operation_budget_s: Total allowed wall-clock seconds (0 = disabled).
        operation_started_at: ``time.monotonic()`` at operation start.
        last_progress_at: ``time.monotonic()`` at last progress event.
        now: Current ``time.monotonic()`` value.
        max_lifetime_deadline_mono: Absolute session cap in monotonic time.

    Returns:
        ``OperationDeadlineError`` when exceeded, else ``None``.
    """
    if operation_budget_s <= 0 or operation_started_at <= 0:
        return None

    deadline = operation_deadline_mono(
        operation_started_at=operation_started_at,
        operation_budget_s=operation_budget_s,
        max_lifetime_deadline_mono=max_lifetime_deadline_mono,
    )
    if now > deadline:
        if max_lifetime_deadline_mono > 0 and now >= max_lifetime_deadline_mono:
            return OperationDeadlineError(
                code="session_lifetime_exceeded",
                message="Session maximum lifetime exceeded",
            )
        elapsed = now - operation_started_at
        return OperationDeadlineError(
            code="operation_budget_exceeded",
            message=(f"Operation exceeded budget of {operation_budget_s}s (elapsed {int(elapsed)}s)"),
        )

    idle = now - last_progress_at if last_progress_at > 0 else (now - operation_started_at)
    limit = stall_window(operation_budget_s)
    if idle > limit:
        return OperationDeadlineError(
            code="operation_stalled",
            message=f"No progress for {int(idle)}s (stall limit {limit}s)",
        )

    return None
