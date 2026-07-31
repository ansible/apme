"""Tests for adaptive operation deadlines (ADR-068)."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from apme_engine.daemon.deadline import (
    BudgetConfigError,
    OperationDeadlineError,
    _parse_int_env,
    check_operation_deadline,
    count_ai_nodes,
    estimate_ai_budget,
    estimate_non_ai_budget,
    estimate_operation_budget,
    estimate_tier1_margin,
    is_task_linked_progress,
    operation_deadline_mono,
    parse_ai_concurrency,
    stall_window,
)
from apme_engine.daemon.session import SessionState
from apme_engine.engine.models import ViolationDict


def test_count_ai_nodes_deduplicates_paths() -> None:
    """Distinct node paths are counted once."""
    violations: list[ViolationDict] = [
        {"path": "play-1/task-1", "rule_id": "L001"},
        {"path": "play-1/task-1", "rule_id": "L002"},
        {"path": "play-1/task-2", "rule_id": "L003"},
        {"path": "", "rule_id": "L004"},
    ]
    assert count_ai_nodes(violations) == 2


def test_parse_int_env_invalid_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric budget env vars fall back to the provided default.

    Args:
        monkeypatch: Pytest fixture for env overrides.
    """
    monkeypatch.setenv("APME_OPERATION_SCAN_BASE", "not-a-number")
    assert _parse_int_env("APME_OPERATION_SCAN_BASE", 300) == 300


def test_parse_ai_concurrency_invalid_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric APME_AI_CONCURRENCY falls back to 4.

    Args:
        monkeypatch: Pytest fixture for env overrides.
    """
    monkeypatch.setenv("APME_AI_CONCURRENCY", "not-a-number")
    assert parse_ai_concurrency() == 4


def test_parse_ai_concurrency_clamps_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero or negative concurrency is clamped to 1.

    Args:
        monkeypatch: Pytest fixture for env overrides.
    """
    monkeypatch.setenv("APME_AI_CONCURRENCY", "0")
    assert parse_ai_concurrency() == 1


def test_estimate_tier1_margin_scales_with_violations() -> None:
    """Tier 1 margin grows with violation count up to the cap."""
    assert estimate_tier1_margin(0) == 0
    assert estimate_tier1_margin(10) == 20
    assert estimate_tier1_margin(1000) == 600


def test_estimate_non_ai_budget_includes_tier1_margin() -> None:
    """Non-AI budget includes scan base and tier1 margin."""
    base_only = estimate_non_ai_budget(violation_count=0)
    with_violations = estimate_non_ai_budget(violation_count=300)
    assert with_violations > base_only
    assert base_only >= 600


def test_estimate_ai_budget_excludes_scan_base() -> None:
    """AI-only budget is smaller than combined when nodes are few."""
    ai_only = estimate_ai_budget(ai_node_count=5, concurrency=4, max_ai_attempts=1)
    combined = estimate_operation_budget(ai_node_count=5, concurrency=4, max_ai_attempts=1)
    assert ai_only < combined
    assert ai_only >= 600


def test_estimate_operation_budget_scales_with_ai_nodes() -> None:
    """More AI nodes produce a larger budget."""
    small = estimate_operation_budget(ai_node_count=5, concurrency=4, max_ai_attempts=1)
    large = estimate_operation_budget(ai_node_count=645, concurrency=4, max_ai_attempts=2)
    assert large > small
    assert small >= 600
    assert large <= 7200


def test_estimate_operation_budget_respects_floor_without_ai() -> None:
    """Non-AI operations still get the minimum budget floor."""
    assert estimate_operation_budget() == 600


def test_estimate_ai_budget_rejects_zero_concurrency() -> None:
    """Concurrency must be > 0."""
    with pytest.raises(BudgetConfigError, match="concurrency"):
        estimate_ai_budget(ai_node_count=1, concurrency=0)


def test_estimate_ai_budget_rejects_zero_per_call_timeout() -> None:
    """per_call_timeout must be > 0."""
    with pytest.raises(BudgetConfigError, match="per_call_timeout"):
        estimate_ai_budget(ai_node_count=1, per_call_timeout=0)


def test_estimate_ai_budget_rejects_zero_max_attempts() -> None:
    """max_ai_attempts must be >= 1."""
    with pytest.raises(BudgetConfigError, match="max_ai_attempts"):
        estimate_ai_budget(ai_node_count=1, max_ai_attempts=0)


def test_operation_deadline_mono_respects_lifetime_cap() -> None:
    """Phase deadline is capped by session lifetime."""
    started = 100.0
    lifetime_cap = 500.0
    assert (
        operation_deadline_mono(
            operation_started_at=started,
            operation_budget_s=600,
            max_lifetime_deadline_mono=lifetime_cap,
        )
        == lifetime_cap
    )


def test_stall_window_caps_at_default() -> None:
    """Stall window never exceeds 600s by default."""
    assert stall_window(10_000) == 600
    assert stall_window(1200) == 600
    assert stall_window(600) == 600
    assert stall_window(300) == 300


def test_is_task_linked_progress_excludes_heartbeat() -> None:
    """Heartbeats must not reset the stall clock."""
    assert is_task_linked_progress("graph-ai") is True
    assert is_task_linked_progress("heartbeat") is False
    assert is_task_linked_progress("") is False


def test_check_operation_deadline_budget_exceeded() -> None:
    """Elapsed time beyond budget returns operation_budget_exceeded."""
    now = 1000.0
    err = check_operation_deadline(
        operation_budget_s=600,
        operation_started_at=now - 700,
        last_progress_at=now - 10,
        now=now,
    )
    assert err is not None
    assert err.code == "operation_budget_exceeded"
    assert isinstance(err, OperationDeadlineError)


def test_check_operation_deadline_lifetime_cap() -> None:
    """Session lifetime cap returns session_lifetime_exceeded."""
    now = 8000.0
    err = check_operation_deadline(
        operation_budget_s=3600,
        operation_started_at=now - 100,
        last_progress_at=now - 10,
        now=now,
        max_lifetime_deadline_mono=now - 1,
    )
    assert err is not None
    assert err.code == "session_lifetime_exceeded"
    assert "lifetime" in str(err).lower()


def test_check_operation_deadline_stalled() -> None:
    """No progress within stall window returns operation_stalled."""
    now = 2000.0
    err = check_operation_deadline(
        operation_budget_s=3600,
        operation_started_at=now - 100,
        last_progress_at=now - 700,
        now=now,
    )
    assert err is not None
    assert err.code == "operation_stalled"


def test_check_operation_deadline_disabled_when_unset() -> None:
    """Zero budget disables enforcement."""
    assert (
        check_operation_deadline(
            operation_budget_s=0,
            operation_started_at=0.0,
            last_progress_at=0.0,
            now=time.monotonic(),
        )
        is None
    )


def test_session_begin_operation_phase_increments_generation() -> None:
    """Each phase anchor bumps operation_generation (ADR-068)."""
    session = SessionState(session_id="test")
    session.init_lifetime_deadline()
    session.begin_operation_phase(600)
    assert session.operation_generation == 1
    session.begin_operation_phase(300)
    assert session.operation_generation == 2


def test_clamp_budget_rejects_inverted_bounds() -> None:
    """Inverted min/max budget bounds raise BudgetConfigError."""
    with pytest.raises(BudgetConfigError, match="min_budget"):
        estimate_non_ai_budget(min_budget=9000, max_budget=600)


def test_session_reanchor_lifetime_deadline() -> None:
    """Resume re-anchors lifetime cap from remaining wall time without extending it."""
    session = SessionState(session_id="test")
    session.init_lifetime_deadline()
    first_cap = session.max_lifetime_deadline_mono
    session.created_at -= timedelta(seconds=100)
    session.reanchor_lifetime_deadline()
    assert session.max_lifetime_deadline_mono <= first_cap + 1
    assert session.max_lifetime_deadline_mono >= first_cap - 105
