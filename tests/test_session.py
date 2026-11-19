"""Tests for session management and FixSession bidirectional stream (ADR-028).

Part 1: SessionState and SessionStore unit tests (no gRPC, no server).
Part 2: FixSession helper method tests (async generators, no server).
Part 3: FixSession RPC integration tests (full servicer with mocked pipeline).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.engine_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FilePatch,
    FixOptions,
    FixReport,
    Proposal,
    ProposalsReady,
    ResumeRequest,
    ScanChunk,
    SessionCommand,
    SessionEvent,
    SessionResult,
    Tier1Summary,
)
from apme_engine.daemon import session as daemon_session_module
from apme_engine.daemon.session import (
    _DEFAULT_TTL,
    _MAX_LIFETIME,
    _MAX_SESSIONS,
    ResourceExhaustedError,
    SessionState,
    SessionStore,
)
from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.graph_engine import FilePatch as SplicedFilePatch
from apme_engine.remediation.graph_engine import SpliceOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AsyncCommandStream:
    """Async iterator backed by a queue for feeding commands to FixSession."""

    def __init__(self) -> None:
        """Initialize empty command queue."""
        self._queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()

    def send(self, cmd: SessionCommand) -> None:
        """Enqueue a command for the stream.

        Args:
            cmd: Command to enqueue.
        """
        self._queue.put_nowait(cmd)

    def close(self) -> None:
        """Signal end of stream."""
        self._queue.put_nowait(None)

    def __aiter__(self) -> AsyncCommandStream:
        """Return self as async iterator.

        Returns:
            Self.
        """
        return self

    async def __anext__(self) -> SessionCommand:
        """Return next command or raise StopAsyncIteration.

        Returns:
            Next SessionCommand from the queue.

        Raises:
            StopAsyncIteration: When the queue receives a None sentinel.
        """
        cmd = await self._queue.get()
        if cmd is None:
            raise StopAsyncIteration
        return cmd


class FakeGrpcContext:
    """Minimal stub for grpc.aio.ServicerContext in tests."""

    def __init__(self) -> None:
        """Initialize with no abort state."""
        self._code: object = None
        self._details: str | None = None
        self.aborted: bool = False

    async def abort(self, code: object, details: str) -> None:
        """Record abort and raise to exit the servicer under test.

        Args:
            code: gRPC status code.
            details: Error details string.

        Raises:
            _AbortSignal: Always, to unwind the test call stack.
        """
        self._code = code
        self._details = details
        self.aborted = True
        raise _AbortSignal(code, details)

    def set_code(self, code: object) -> None:
        """Set the recorded status code.

        Args:
            code: gRPC status code.
        """
        self._code = code

    def set_details(self, details: str) -> None:
        """Set the recorded error details.

        Args:
            details: Error details string.
        """
        self._details = details

    def peer(self) -> str:
        """Return a fake peer address.

        Returns:
            Fake peer identifier string.
        """
        return "ipv4:127.0.0.1:50051"


class _AbortSignal(Exception):
    """Raised by FakeGrpcContext.abort to break out of the servicer.

    Args:
        code: gRPC status code.
        details: Error details string.
    """

    def __init__(self, code: object, details: str) -> None:
        super().__init__(f"{code}: {details}")
        self.code = code
        self.details = details


async def test_pipeline_explicit_native_skip_does_not_call_native(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicitly skipped Native is neither required nor dispatched by a scan.

    Args:
        monkeypatch: Replace validator endpoints and local scan dependencies.
        tmp_path: Temporary project and session venv path.
    """
    from unittest.mock import MagicMock

    from apme.v1.common_pb2 import File
    from apme_engine.daemon import engine_server

    for env_var in engine_server.VALIDATOR_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("OPA_GRPC_ADDRESS", "opa")
    monkeypatch.setenv("ANSIBLE_GRPC_ADDRESS", "ansible")
    context = SimpleNamespace(
        hierarchy_payload={"hierarchy": [], "collection_set": []},
        scandata=None,
        engine_diagnostics=SimpleNamespace(
            parse_ms=0.0, annotate_ms=0.0, total_ms=0.0, files_scanned=1, graph_nodes_built=0
        ),
    )
    manager = MagicMock()
    manager.get.return_value = None
    manager.peek_warm.return_value = SimpleNamespace(
        venv_root=tmp_path, failed_collections=[], installed_collections=[]
    )
    monkeypatch.setattr(engine_server, "run_scan", MagicMock(return_value=context))
    monkeypatch.setattr(engine_server.EngineServicer, "_get_venv_manager", MagicMock(return_value=manager))
    dispatch = AsyncMock(return_value=engine_server._ValidatorResult())
    monkeypatch.setattr(engine_server, "_call_validator", dispatch)
    findings, *_ = await engine_server.EngineServicer()._execute_scan_pipeline(
        tmp_path,
        [File(path="molecule/default/molecule.yml", content=b"---")],
        "skip-native",
        skip_validators=frozenset({"native"}),
    )
    assert not findings
    assert {call.args[0] for call in dispatch.await_args_list} == {"opa", "ansible"}


# ---------------------------------------------------------------------------
# Part 1: SessionState unit tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _disable_create_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable session creation rate-limit for legacy burst tests (N17).

    Session creation throttling is opt-in; tests create bursts without
    configuring APME_SESSION_CREATE_INTERVAL_S. Rate-limit behavior is
    covered in test_session_admission_idle.py.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(daemon_session_module, "_CREATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(daemon_session_module, "_MIN_CREATE_INTERVAL_S", 0.0)


class TestSessionState:
    """Unit tests for SessionState dataclass."""

    def test_initial_state(self) -> None:
        """Fresh session has expected defaults."""
        state = SessionState(session_id="abc123")
        assert state.session_id == "abc123"
        assert state.current_tier == 1
        assert state.status == 2  # PROCESSING
        assert state.idempotency_ok is True
        assert state.original_files == {}
        assert state.working_files == {}
        assert state.proposals == {}
        assert state.report is None

    def test_ttl_positive_on_fresh_session(self) -> None:
        """New session TTL is positive and within the default idle window."""
        state = SessionState(session_id="abc")
        assert 0 < state.ttl_seconds <= _DEFAULT_TTL

    def test_not_expired_when_fresh(self) -> None:
        """Fresh session is not expired."""
        state = SessionState(session_id="abc")
        assert state.expired is False

    def test_not_expiring_soon_when_fresh(self) -> None:
        """Fresh session is not in the expiring-soon window."""
        state = SessionState(session_id="abc")
        assert state.expiring_soon is False

    def test_expired_after_idle_timeout(self) -> None:
        """Session expires after idle TTL elapses."""
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(UTC) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert state.expired is True

    def test_expired_after_max_lifetime(self) -> None:
        """Session expires after max lifetime is exceeded."""
        state = SessionState(session_id="abc")
        state.created_at = datetime.now(UTC) - timedelta(
            seconds=_MAX_LIFETIME + 1,
        )
        assert state.expired is True

    def test_expiring_soon_within_warning_window(self) -> None:
        """Low remaining TTL marks session as expiring soon."""
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(UTC) - timedelta(
            seconds=_DEFAULT_TTL - 200,
        )
        assert state.expiring_soon is True

    def test_touch_resets_idle_timer(self) -> None:
        """touch() refreshes idle activity and increases remaining TTL."""
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(UTC) - timedelta(seconds=600)
        old_ttl = state.ttl_seconds
        state.touch()
        assert state.ttl_seconds > old_ttl

    def test_lifetime_seconds_near_zero_on_create(self) -> None:
        """lifetime_seconds is near zero immediately after creation."""
        state = SessionState(session_id="abc")
        assert state.lifetime_seconds < 5

    def test_operation_budget_remaining_respects_lifetime_cap(self) -> None:
        """Remaining budget is capped by the session lifetime deadline (ADR-068)."""
        state = SessionState(session_id="abc")
        now = time.monotonic()
        state.operation_budget_s = 600
        state.operation_started_at = now
        state.max_lifetime_deadline_mono = now + 120
        remaining = state.operation_budget_remaining()
        assert 115 <= remaining <= 120

    def test_reset_partial_run_state_clears_per_run_artifacts(self, tmp_path: Path) -> None:
        """Failed runs do not leak progress_logs or gate state into retries.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        state = SessionState(session_id="retry-1")
        state.original_files = {"a.yml": b"orig\n"}
        state.working_files = {"a.yml": b"mutated\n"}
        state.progress_logs.append(
            ProgressUpdate(message="formatting", phase="format", level=2),
        )
        state.report = FixReport()
        state.ai_proposals = [object()]
        state.tier1_proposals = [object()]
        state.remaining_ai = [ViolationDict(rule_id="L001", message="x", path="a.yml")]
        state.remaining_manual = [ViolationDict(rule_id="L002", message="y", path="a.yml")]
        state.dep_health_violations = [ViolationDict(rule_id="R001", message="z", path="a.yml")]
        state.awaiting_tier1_gate = True
        state.awaiting_assess = True
        state.awaiting_ai_triage = True
        state.content_graph = object()
        state.graph_engine = object()
        state.review_declined_proposals["p1"] = Proposal(id="p1")
        state.proposals["p2"] = Proposal(id="p2")
        state.status = 3  # COMPLETE
        temp = tmp_path / "session_temp"
        temp.mkdir()
        state.temp_dir = temp
        cfg_dir = tmp_path / "galaxy"
        cfg_dir.mkdir()
        cfg = cfg_dir / "ansible.cfg"
        cfg.write_text("[galaxy]\n")
        state.galaxy_cfg_path = cfg

        state.reset_partial_run_state()

        assert state.working_files == state.original_files
        assert state.format_diffs == []
        assert state.tier1_patches == []
        assert state.proposals == {}
        assert state.review_declined_proposals == {}
        assert state.progress_logs == []
        assert state.report is None
        assert state.ai_proposals == []
        assert state.tier1_proposals == []
        assert state.remaining_ai == []
        assert state.remaining_manual == []
        assert state.dep_health_violations == []
        assert state.awaiting_tier1_gate is False
        assert state.awaiting_assess is False
        assert state.awaiting_ai_triage is False
        assert state.content_graph is None
        assert state.graph_engine is None
        assert state.status == 2  # PROCESSING
        assert not temp.exists()
        assert state.temp_dir is None
        assert cfg.exists()
        assert state.galaxy_cfg_path == cfg

    def test_cleanup_removes_temp_dir(self, tmp_path: Path) -> None:
        """cleanup() deletes temp_dir contents and clears the field.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        state = SessionState(session_id="abc")
        temp = tmp_path / "session_temp"
        temp.mkdir()
        (temp / "file.yml").write_text("---\n")
        state.temp_dir = temp

        state.cleanup()
        assert not temp.exists()
        assert state.temp_dir is None

    def test_cleanup_noop_without_temp_dir(self) -> None:
        """cleanup() does nothing when temp_dir is unset."""
        state = SessionState(session_id="abc")
        state.cleanup()
        assert state.temp_dir is None


# ---------------------------------------------------------------------------
# Part 1b: SessionStore unit tests
# ---------------------------------------------------------------------------


class TestSessionStore:
    """Unit tests for SessionStore CRUD and capacity limits."""

    def test_create_returns_unique_session(self) -> None:
        """create() yields distinct session IDs and increments count."""
        store = SessionStore()
        s1 = store.create()
        s2 = store.create()
        assert s1.session_id != s2.session_id
        assert store.count == 2

    def test_get_returns_existing_session(self) -> None:
        """get() returns the same object for a known session ID."""
        store = SessionStore()
        s = store.create()
        assert store.get(s.session_id) is s

    def test_get_returns_none_for_unknown_id(self) -> None:
        """get() returns None for an unknown session ID."""
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_get_auto_removes_expired_session(self) -> None:
        """get() drops expired sessions and returns None."""
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(UTC) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert store.get(s.session_id) is None
        assert store.count == 0

    def test_touch_refreshes_activity(self) -> None:
        """touch() updates last activity so TTL recovers."""
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(UTC) - timedelta(seconds=100)
        store.touch(s.session_id)
        assert s.ttl_seconds > _DEFAULT_TTL - 5

    def test_remove_returns_true(self) -> None:
        """remove() returns True and clears the session from the store."""
        store = SessionStore()
        s = store.create()
        assert store.remove(s.session_id) is True
        assert store.count == 0

    def test_remove_unknown_returns_false(self) -> None:
        """remove() returns False for an unknown session ID."""
        store = SessionStore()
        assert store.remove("nope") is False

    def test_max_sessions_raises(self) -> None:
        """create() raises ResourceExhaustedError at the session cap."""
        store = SessionStore()
        for _ in range(_MAX_SESSIONS):
            store.create()
        with pytest.raises(ResourceExhaustedError, match="Maximum"):
            store.create()

    def test_remove_frees_slot_for_new_session(self) -> None:
        """Removing a session allows create() under the max again."""
        store = SessionStore()
        sessions = [store.create() for _ in range(_MAX_SESSIONS)]
        store.remove(sessions[0].session_id)
        store.create()
        assert store.count == _MAX_SESSIONS

    def test_remove_cleans_up_temp_dir(self, tmp_path: Path) -> None:
        """remove() runs cleanup and deletes the session temp directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        store = SessionStore()
        s = store.create()
        temp = tmp_path / "sess_tmp"
        temp.mkdir()
        s.temp_dir = temp
        store.remove(s.session_id)
        assert not temp.exists()


class TestSessionStoreReaper:
    """Unit tests for SessionStore background reaper behavior."""

    async def test_reaper_collects_expired_sessions(self) -> None:
        """Manual sweep removes expired sessions from the store."""
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(UTC) - timedelta(
            seconds=_DEFAULT_TTL + 10,
        )

        expired = [sid for sid, st in store._sessions.items() if st.expired]
        for sid in expired:
            store._remove(sid)

        assert store.count == 0

    async def test_reaper_preserves_active_sessions(self) -> None:
        """Sweep keeps non-expired sessions in the store."""
        store = SessionStore()
        store.create().touch()

        expired = [sid for sid, st in store._sessions.items() if st.expired]
        for sid in expired:
            store._remove(sid)

        assert store.count == 1

    async def test_start_and_stop_reaper(self) -> None:
        """start_reaper and stop_reaper manage the background task lifecycle."""
        store = SessionStore()
        store.start_reaper()
        assert store._reaper_task is not None
        assert not store._reaper_task.done()

        store.stop_reaper()
        await asyncio.sleep(0.05)
        assert store._reaper_task is None


# ---------------------------------------------------------------------------
# Part 2: FixSession helper method tests
# ---------------------------------------------------------------------------


class TestSessionApplyApproved:
    """Unit tests for _session_apply_approved."""

    def test_full_approval_sets_complete(self) -> None:
        """Approving all proposals marks session complete and clears proposals."""
        from apme_engine.daemon.engine_server import EngineServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "t2-0000": Proposal(
                id="t2-0000",
                file="t.yml",
                rule_id="L001",
                before_text="old",
                after_text="new",
            ),
        }
        session.status = 1
        session.working_files = {"t.yml": b"old content"}

        applied, _ = EngineServicer._session_apply_approved(session, {"t2-0000"})
        assert applied == 1
        assert session.status == 3  # COMPLETE
        assert session.proposals == {}

    def test_partial_approval_completes_session(self) -> None:
        """Partial approval completes and stashes unapproved proposals for telemetry."""
        from apme_engine.daemon.engine_server import EngineServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "p1": Proposal(id="p1", file="a.yml", rule_id="L001", before_text="old1", after_text="new1"),
            "p2": Proposal(id="p2", file="b.yml", rule_id="L002", before_text="old2", after_text="new2"),
        }
        session.status = 1
        session.working_files = {"a.yml": b"old1", "b.yml": b"old2"}

        applied, _ = EngineServicer._session_apply_approved(session, {"p1"})
        assert applied == 1
        assert session.status == 3  # COMPLETE after approval processing
        assert session.proposals == {}
        assert "p2" in session.rejected_proposals

    def test_approval_modifies_working_files(self) -> None:
        """Approved proposal replaces before_text with after_text in working_files."""
        from apme_engine.daemon.engine_server import EngineServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "p1": Proposal(
                id="p1",
                file="test.yml",
                rule_id="L001",
                before_text="hello",
                after_text="goodbye",
            ),
        }
        session.status = 1
        session.working_files = {"test.yml": b"hello world"}

        EngineServicer._session_apply_approved(session, {"p1"})
        assert session.working_files["test.yml"] == b"goodbye world"


class TestSessionBuildResult:
    """Unit tests for _session_build_result async generator."""

    async def test_includes_only_changed_files(self) -> None:
        """Result patches list only files whose content changed."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="test")
        session.original_files = {"a.yml": b"orig-a", "b.yml": b"same"}
        session.working_files = {"a.yml": b"patched-a", "b.yml": b"same"}
        session.report = FixReport(passes=1, fixed=1)

        events = [e async for e in servicer._session_build_result(session)]
        assert len(events) == 1
        patches = events[0].result.patches
        assert len(patches) == 1
        assert patches[0].path == "a.yml"

    async def test_diff_is_unified_format(self) -> None:
        """Patch diff uses unified diff markers and line changes."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="test")
        session.original_files = {"f.yml": b"line1\nline2\n"}
        session.working_files = {"f.yml": b"line1\nchanged\n"}
        session.report = FixReport()

        events = [e async for e in servicer._session_build_result(session)]
        diff = events[0].result.patches[0].diff
        assert "---" in diff and "+++" in diff
        assert "-line2" in diff and "+changed" in diff


class TestWorkingFilesKey:
    """Normalize absolute splice paths to relative working_files keys."""

    def test_absolute_under_temp_becomes_relative(self, tmp_path: Path) -> None:
        """Absolute paths beneath temp_dir map to relative keys.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import _working_files_key

        abs_path = str(tmp_path / "playbooks" / "main.yml")
        assert _working_files_key(tmp_path, abs_path) == "playbooks/main.yml"

    def test_relative_path_unchanged(self, tmp_path: Path) -> None:
        """Already-relative keys stay relative.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import _working_files_key

        assert _working_files_key(tmp_path, "roles/x.yml") == "roles/x.yml"

    def test_no_temp_dir_returns_path(self) -> None:
        """Without a temp root the raw path is preserved."""
        from apme_engine.daemon.engine_server import _working_files_key

        assert _working_files_key(None, "/abs/file.yml") == "/abs/file.yml"


class TestWritePatchesToTempDir:
    """Path-safety and fail-loud behavior for interactive temp patch writes."""

    def test_writes_relative_path_under_temp_dir(self, tmp_path: Path) -> None:
        """Relative patch paths land under the session temp root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import _write_patches_to_temp_dir
        from apme_engine.remediation.graph_engine import FilePatch

        patch = FilePatch(path="playbooks/main.yml", original="a\n", patched="b\n", diff="", rule_ids=[])
        _write_patches_to_temp_dir(tmp_path, [patch])
        assert (tmp_path / "playbooks" / "main.yml").read_text(encoding="utf-8") == "b\n"

    def test_rejects_path_escape(self, tmp_path: Path) -> None:
        """``..`` segments that escape temp_dir raise ValueError.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import _write_patches_to_temp_dir
        from apme_engine.remediation.graph_engine import FilePatch

        patch = FilePatch(path="../escape.yml", original="a\n", patched="b\n", diff="", rule_ids=[])
        with pytest.raises(ValueError, match="Unsafe patch path|escapes temp root"):
            _write_patches_to_temp_dir(tmp_path, [patch])

    def test_absolute_under_temp_ok(self, tmp_path: Path) -> None:
        """Absolute paths still under temp_dir are accepted.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import _write_patches_to_temp_dir
        from apme_engine.remediation.graph_engine import FilePatch

        target = tmp_path / "roles" / "x.yml"
        patch = FilePatch(path=str(target), original="a\n", patched="fixed\n", diff="", rule_ids=[])
        _write_patches_to_temp_dir(tmp_path, [patch])
        assert target.read_text(encoding="utf-8") == "fixed\n"


class TestSessionApprovalGates:
    """Unit tests for multi-gate approval sequencing and proposal isolation."""

    def test_gate1_approval_preserves_unpatched_without_pre_gate2_snapshot(self) -> None:
        """The Gate 1 return value retains splice metadata without restoration.

        Gate 1 has no pre-Gate-2 snapshot, so this exercises the path that
        previously returned the plain patch list and lost ``unpatched``.
        """
        from apme_engine.daemon.engine_server import _apply_graph_approvals
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import SpliceResult

        graph = ContentGraph()
        session = SessionState(session_id="gate1-unpatched")
        outcome = SpliceOutcome(patches=[], unpatched=["legacy.yml"])

        with patch("apme_engine.remediation.graph_engine.splice_with_outcome", return_value=outcome):
            _applied, _rejected, _approved, patches = _apply_graph_approvals(session, graph, {}, set())

        assert isinstance(patches, SpliceResult)
        assert patches.unpatched == ["legacy.yml"]

    @pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
    def _mock_native_for_upstream_transaction_tests(
        self, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Isolate approval transaction tests from the Native RPC.

        Args:
            request: Current test identity.
            monkeypatch: Installs the isolated RPC mock.
        """
        isolated = {
            "test_legacy_ids_may_include_offered_declined_rows",
            "test_reporting_does_not_hold_close_or_disconnect",
            "test_failed_approval_persistence_is_retryable",
            "test_disconnect_during_persistence_finishes_ai_transition",
            "test_gates_format_the_same_role_include_patch",
            "test_resumed_approvals_do_not_race_preview",
            "test_conflicting_approval_emits_session_error",
        }
        if request.node.name.split("[", 1)[0] in isolated:
            from apme_engine.daemon import engine_server

            monkeypatch.setattr(engine_server, "_refresh_session_file_violations", AsyncMock())

    async def test_restarted_processing_rotates_pending_gate_token(self) -> None:
        """A failed processing run cannot reuse its pending gate identity on retry."""
        from apme_engine.daemon import engine_server as server

        session = SessionState(session_id="retry-processing", status=1)
        proposal = Proposal(id="p1", file="tasks.yml")
        session.proposals = {"p1": proposal}
        old_token = server._current_approval_token(session)
        session.reset_partial_run_state()
        session.proposals = {"p1": proposal}
        session.status = 1
        new_token = server._current_approval_token(session)
        assert new_token != old_token
        servicer = server.EngineServicer()
        events = [
            event
            async for event in servicer._session_handle_approval(
                session,
                set(),
                approval_token=old_token,
            )
        ]
        assert len(events) == 1
        assert events[0].error.code == "stale_approval_gate"
        assert set(session.proposals) == {"p1"}
        assert not session.approval_decisions

    @pytest.mark.parametrize(
        "stale_mode", ["token", "legacy", "different", "unknown", "current_foreign", "current_mixed", "current_unknown"]
    )  # type: ignore[untyped-decorator]
    async def test_empty_gate1_retry_cannot_decide_gate2(self, stale_mode: str) -> None:
        """Delayed Gate 1 decisions cannot consume any Gate 2 proposal.

        Args:
            stale_mode: Exact token retry, unscoped legacy retry, changed decision, or unknown token.
        """
        from apme.v1.engine_pb2 import AiEscalateRequest, AiEscalateTarget
        from apme_engine.daemon import engine_server as server

        servicer = server.EngineServicer()
        store = servicer._get_session_store()
        session = store.create()
        session.status = 1
        session.current_tier = 1
        session.awaiting_tier1_gate = True
        session.fix_options = FixOptions(enable_ai=True)
        session.proposals = {"t1-0000": Proposal(id="t1-0000", file="tasks.yml", tier=1)}
        stream = AsyncCommandStream()
        stream.send(SessionCommand(resume=ResumeRequest(session_id=session.session_id)))
        first_token = ""
        second_token = ""
        retry_sent = False
        stale_checked = False
        expected_error = {
            "legacy": "approval_token_required",
            "different": "approval_already_decided",
            "unknown": "stale_approval_gate",
            "current_foreign": "invalid_approval_ids",
            "current_mixed": "invalid_approval_ids",
            "current_unknown": "invalid_approval_ids",
        }

        async def ai_gate(_state: SessionState) -> AsyncIterator[SessionEvent]:
            _state.proposals = {"ai-0000": Proposal(id="ai-0000", file="tasks.yml", tier=2)}
            _state.current_tier = 2
            _state.status = 1
            yield SessionEvent(
                proposals=ProposalsReady(
                    proposals=list(_state.proposals.values()),
                    tier=2,
                    status=1,
                    approval_token=server._current_approval_token(_state),
                )
            )

        def check_and_decline_gate2() -> None:
            nonlocal stale_checked
            assert set(session.proposals) == {"ai-0000"}
            assert session.status == 1
            assert "ai-0000" not in session.rejected_proposals
            assert second_token not in session.approval_decisions
            stale_checked = True
            stream.send(SessionCommand(approve=ApprovalRequest(approval_token=second_token)))

        events = []
        try:
            with (
                patch.object(
                    server,
                    "_collect_ai_triage_candidates",
                    return_value=[
                        {
                            "rule_id": "L999",
                            "path": "tasks.yml/tasks[0]",
                            "file": "tasks.yml",
                        }
                    ],
                ),
                patch.object(servicer, "_session_run_ai_gate", side_effect=ai_gate),
            ):
                async for event in servicer.FixSession(stream, FakeGrpcContext()):  # type: ignore[arg-type]
                    events.append(event)
                    if event.HasField("proposals") and event.proposals.tier == 1:
                        first_token = event.proposals.approval_token
                        assert first_token
                        replay = [item async for item in servicer._session_replay_state(session)]
                        assert replay[-1].proposals.approval_token == first_token
                        stream.send(SessionCommand(approve=ApprovalRequest(approval_token=first_token)))
                    elif event.HasField("ai_triage"):
                        stream.send(
                            SessionCommand(
                                ai_escalate=AiEscalateRequest(
                                    targets=[AiEscalateTarget(path="tasks.yml/tasks[0]")],
                                )
                            )
                        )
                    elif event.HasField("proposals"):
                        if not retry_sent:
                            second_token = event.proposals.approval_token
                            assert second_token and second_token != first_token
                            retry_sent = True
                            stream.send(
                                SessionCommand(
                                    approve=ApprovalRequest(
                                        approval_token=""
                                        if stale_mode == "legacy"
                                        else (
                                            "unknown-token"
                                            if stale_mode == "unknown"
                                            else (second_token if stale_mode.startswith("current_") else first_token)
                                        ),
                                        approved_ids=(
                                            ["ai-0000"]
                                            if stale_mode == "different"
                                            else ["t1-0000"]
                                            if stale_mode == "current_foreign"
                                            else ["ai-0000", "t1-0000"]
                                            if stale_mode == "current_mixed"
                                            else ["unknown"]
                                            if stale_mode == "current_unknown"
                                            else []
                                        ),
                                    )
                                )
                            )
                        else:
                            assert stale_mode == "token"
                            assert event.proposals.approval_token == second_token
                            check_and_decline_gate2()
                    elif event.HasField("error"):
                        assert event.error.code == expected_error[stale_mode]
                        assert event.error.recoverable
                        check_and_decline_gate2()
                    elif event.HasField("result"):
                        assert stale_checked
                        stream.send(SessionCommand(close=CloseRequest()))
            assert events[-1].HasField("closed")
            assert session.approval_decisions[first_token] == frozenset()
            assert session.approval_decisions[second_token] == frozenset()
            assert set(session.rejected_proposals) == {"t1-0000", "ai-0000"}
        finally:
            store.remove(session.session_id)

    async def test_legacy_ids_may_include_offered_declined_rows(self) -> None:
        """A legacy nonempty request can include every offered row without rejecting a valid fix."""
        from apme_engine.daemon import engine_server as server

        session = SessionState(session_id="legacy-offer", status=1)
        session.proposals = {
            "ai-0000": Proposal(
                id="ai-0000",
                file="tasks.yml",
                before_text="before",
                after_text="after",
            )
        }
        session.review_declined_proposals = {
            "ai-declined-0001": Proposal(
                id="ai-declined-0001",
                file="tasks.yml",
                status="declined",
            )
        }
        session.working_files = {"tasks.yml": b"before"}
        session.approval_decisions = {"old-token": frozenset()}
        events = [
            event
            async for event in server.EngineServicer()._session_handle_approval(
                session,
                {"ai-0000", "ai-declined-0001"},
                approval_token="",
            )
        ]
        assert not any(event.HasField("error") for event in events)
        assert events[0].approval_ack.applied_count == 1
        assert session.working_files["tasks.yml"] == b"after"
        assert session.status == 3

    @pytest.mark.parametrize("disconnect", [False, True])  # type: ignore[untyped-decorator]
    async def test_reporting_does_not_hold_close_or_disconnect(self, disconnect: bool) -> None:
        """Reporting stays owned without delaying the result-to-close RPC handshake.

        Args:
            disconnect: Whether the client disconnects instead of closing normally.
        """
        from apme_engine.daemon import engine_server as server

        servicer = server.EngineServicer()
        store = servicer._get_session_store()
        session = store.create()
        session.status = 1
        session.upload_sealed = True
        session.proposals = {"p1": Proposal(id="p1", file="tasks.yml")}
        session.original_files = {"tasks.yml": b"before"}
        session.working_files = {"tasks.yml": b"after"}
        reporting = asyncio.Event()
        release = asyncio.Event()
        result_seen = asyncio.Event()
        stream = AsyncCommandStream()
        stream.send(SessionCommand(resume=ResumeRequest(session_id=session.session_id)))

        async def slow_report(_session: SessionState, _result: SessionResult) -> None:
            reporting.set()
            await release.wait()

        async def consume() -> list[SessionEvent]:
            events = []
            async for event in servicer.FixSession(stream, FakeGrpcContext()):  # type: ignore[arg-type]
                events.append(event)
                if event.HasField("proposals"):
                    stream.send(SessionCommand(approve=ApprovalRequest(approved_ids=["p1"])))
                elif event.HasField("result"):
                    assert event.result.patches[0].patched == b"after"
                    result_seen.set()
                    if not disconnect:
                        stream.send(SessionCommand(close=CloseRequest()))
            return events

        with patch.object(servicer, "_session_report_result", side_effect=slow_report):
            client = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(reporting.wait(), 5)
                await asyncio.wait_for(result_seen.wait(), 5)
                assert not session.approval_in_flight
                assert session.approval_tasks
                if disconnect:
                    client.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await client
                    assert store.get(session.session_id) is session
                else:
                    events = await asyncio.wait_for(client, 5)
                    assert events[-1].HasField("closed")
                    assert store.get(session.session_id) is None
                # Close/cancellation must leave completion reporting running.
                assert session.approval_tasks
            finally:
                pending = list(session.approval_tasks)
                release.set()
                await asyncio.wait_for(asyncio.gather(*pending), 5)
                if not client.done():
                    client.cancel()
                await asyncio.gather(client, return_exceptions=True)
                store.remove(session.session_id)
        assert not session.approval_tasks

    @pytest.mark.parametrize("enable_ai", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("accept", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("retry_accept", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("restore_snapshot", [False, True])  # type: ignore[untyped-decorator]
    async def test_failed_approval_persistence_is_retryable(
        self, tmp_path: Path, enable_ai: bool, accept: bool, retry_accept: bool, restore_snapshot: bool
    ) -> None:
        """Retry the full disk batch without losing proposals or doubling decisions.

        Args:
            tmp_path: Session working directory.
            enable_ai: Whether approval should continue to AI triage.
            accept: Whether to accept or reject on the failed first attempt.
            retry_accept: Approval choices on retry, which may differ from the first attempt.
            restore_snapshot: Whether Gate 2 snapshot bytes also need restoration.
        """
        from apme.v1.common_pb2 import Violation
        from apme.v1.engine_pb2 import AiTriageReady
        from apme_engine.daemon import engine_server as server
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        original = "- name: Task\n  debug: {}\n"
        graph = ContentGraph()
        session = SessionState(session_id="retry-write", status=1)
        session.temp_dir = tmp_path
        session.content_graph = graph
        session.graph_originals = {}
        for index in range(2):
            path = f"tasks{index}.yml"
            node = ContentNode(
                identity=NodeIdentity(path=f"{path}/tasks[0]", node_type=NodeType.TASK),
                file_path=path,
                line_start=1,
                line_end=2,
                yaml_lines=original,
            )
            graph.add_node(node)
            node.record_state(0, "scanned")
            node.update_from_yaml(original.replace("debug:", "ansible.builtin.debug:"))
            node.record_state(1, "transformed", source="deterministic")
            session.graph_originals[path] = original
            session.working_files[path] = original.encode()
            session.proposals[f"t1-{index:04d}"] = Proposal(id=f"t1-{index:04d}", path=node.node_id, file=path, tier=1)
            (tmp_path / path).write_text(original)
        # Gate 2 also needs to restore bytes not represented by changed nodes.
        if restore_snapshot:
            session.pre_gate2_files = dict(session.working_files)
            session.pre_gate2_files["restore.yml"] = b"original: value\n"
            session.pre_gate2_files["template.j2"] = b"caf\xe9\n"
            (tmp_path / "restore.yml").write_text("pending: value\n")
            (tmp_path / "template.j2").write_bytes(b"caf\xe9\n")
        session.awaiting_tier1_gate = True
        session.fix_options = FixOptions(enable_ai=enable_ai)
        pending = dict(session.proposals)
        progressions = {node.node_id: list(node.progression) for node in graph.nodes()}
        working = dict(session.working_files)
        choices = set(pending) if accept else set()
        write_fn = server._write_files_to_temp_dir
        attempts = 0

        def flaky_write(root: Path, files: dict[str, bytes]) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                write_fn(root, dict(list(files.items())[:1]))
                raise OSError("simulated disk failure after first file")
            write_fn(root, files)

        async def enter_ai(
            _self: server.EngineServicer, state: SessionState, *, report_completion: bool = True
        ) -> AsyncIterator[SessionEvent]:
            state.awaiting_ai_triage = True
            state.status = 4
            state.ai_triage_candidates = [Violation(rule_id="L999", file="tasks0.yml")]
            yield SessionEvent(ai_triage=AiTriageReady(status=4))

        servicer = server.EngineServicer()
        gate_token = server._current_approval_token(session)
        with (
            patch.object(server, "_write_files_to_temp_dir", side_effect=flaky_write),
            patch.object(server.EngineServicer, "_session_enter_ai_phase", enter_ai),
        ):
            failed = [event async for event in servicer._session_handle_approval(session, choices)]
            assert [event.WhichOneof("event") for event in failed] == ["error"]
            assert failed[0].error.code == "remediation_failed"
            assert session.status == 1
            assert session.awaiting_tier1_gate
            assert session.proposals == pending
            assert server._current_approval_token(session) == gate_token
            assert not session.approval_decisions
            assert not session.approved_ids
            assert not session.approved_proposals
            assert not session.rejected_proposals
            assert session.working_files == working
            assert {node.node_id: node.progression for node in graph.nodes()} == progressions
            assert not session.approval_in_flight
            replay = [event async for event in servicer._session_replay_state(session)]
            assert set(p.id for event in replay for p in event.proposals.proposals) == set(pending)
            choices = set(pending) if retry_accept else set()
            retried = [event async for event in servicer._session_handle_approval(session, choices)]
            assert not any(event.HasField("error") for event in retried)
            assert next(event for event in retried if event.HasField("approval_ack")).approval_ack.applied_count == (
                2 if retry_accept else 0
            )
            assert any(event.HasField("ai_triage" if enable_ai else "result") for event in retried)
            duplicate = [event async for event in servicer._session_handle_approval(session, choices)]
            assert duplicate[0].approval_ack.applied_count == 0
        assert attempts == 2
        assert len(session.approved_proposals) == (2 if retry_accept else 0)
        assert len(session.rejected_proposals) == (0 if retry_accept else 2)
        assert session.status == (4 if enable_ai else 3)
        assert not session.proposals
        if restore_snapshot:
            assert (tmp_path / "restore.yml").read_text() == "original: value\n"
            assert (tmp_path / "template.j2").read_bytes() == b"caf\xe9\n"
            assert session.working_files["template.j2"] == b"caf\xe9\n"
        for path in ("tasks0.yml", "tasks1.yml"):
            expected = original.replace("debug:", "ansible.builtin.debug:") if retry_accept else original
            assert session.working_files[path].decode().removeprefix("---\n") == expected
            assert (tmp_path / path).read_bytes() == session.working_files[path]

    async def test_disconnect_during_persistence_finishes_ai_transition(self, tmp_path: Path) -> None:
        """A disconnected approval persists before committing and exposes durable triage on resume.

        Args:
            tmp_path: Session working directory.
        """
        import threading

        from apme.v1.common_pb2 import Violation
        from apme.v1.engine_pb2 import AiTriageReady
        from apme_engine.daemon import engine_server as server
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        original = "- name: Task\n  debug: {}\n"
        graph = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path="tasks.yml/tasks[0]", node_type=NodeType.TASK),
            file_path="tasks.yml",
            line_start=1,
            line_end=2,
            yaml_lines=original,
        )
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(original.replace("debug:", "ansible.builtin.debug:"))
        node.record_state(1, "transformed", source="deterministic")
        session = SessionState(session_id="post-commit", status=1)
        session.temp_dir = tmp_path
        session.content_graph = graph
        session.graph_originals = {"tasks.yml": original}
        session.working_files = {"tasks.yml": original.encode()}
        session.proposals = {"t1-0000": Proposal(id="t1-0000", path=node.node_id, file="tasks.yml", tier=1)}
        session.awaiting_tier1_gate = True
        session.fix_options = FixOptions(enable_ai=True)
        servicer = server.EngineServicer()
        loop = asyncio.get_running_loop()
        writing = asyncio.Event()
        release_write = threading.Event()
        write_fn = server._write_files_to_temp_dir

        def slow_write(root: Path, files: dict[str, bytes]) -> None:
            loop.call_soon_threadsafe(writing.set)
            assert release_write.wait(5), "write was never released"
            write_fn(root, files)

        async def enter_ai(
            _self: server.EngineServicer, state: SessionState, *, report_completion: bool = True
        ) -> AsyncIterator[SessionEvent]:
            state.awaiting_ai_triage = True
            state.status = 4
            state.ai_triage_candidates = [Violation(rule_id="L999", file="tasks.yml", path=node.node_id)]
            yield SessionEvent(ai_triage=AiTriageReady(status=4))

        async def approve() -> list[SessionEvent]:
            return [event async for event in servicer._session_handle_approval(session, {"t1-0000"})]

        async def replay() -> list[SessionEvent]:
            return [event async for event in servicer._session_replay_state(session)]

        with (
            patch.object(server, "_write_files_to_temp_dir", side_effect=slow_write) as writer,
            patch.object(server.EngineServicer, "_session_enter_ai_phase", enter_ai),
        ):
            first = asyncio.create_task(approve())
            resumed: asyncio.Task[list[SessionEvent]] | None = None
            try:
                await asyncio.wait_for(writing.wait(), 5)
                assert session.proposals
                assert not session.approved_ids
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert session.approval_in_flight
                resumed = asyncio.create_task(replay())
                await asyncio.sleep(0)
                assert not resumed.done()
                busy = await approve()
                assert busy[0].error.code == "approval_in_progress"
                release_write.set()
                events = await asyncio.wait_for(resumed, 5)
                assert any(event.HasField("ai_triage") for event in events)
                assert session.awaiting_ai_triage and session.status == 4
                duplicate = await approve()
                assert duplicate[0].approval_ack.applied_count == 0
                assert any(event.HasField("ai_triage") for event in duplicate)
                assert session.awaiting_ai_triage and session.status == 4
                assert writer.call_count == 1
            finally:
                release_write.set()
                if not first.done():
                    first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                if resumed is not None and not resumed.done():
                    await asyncio.wait_for(resumed, 5)
        assert not session.approval_in_flight
        assert len(session.approved_proposals) == 1
        assert b"ansible.builtin.debug" in (tmp_path / "tasks.yml").read_bytes()

    @pytest.mark.parametrize("gate", ["initial", "approval", "ai"])  # type: ignore[untyped-decorator]
    async def test_gates_format_the_same_role_include_patch(self, tmp_path: Path, gate: str) -> None:
        """All gates expand identical role-include patches with identical context.

        Args:
            tmp_path: Session working directory.
            gate: Remediation gate receiving the flow-style source patch.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon import engine_server as server
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.graph_engine import FilePatch as SourcePatch
        from apme_engine.remediation.graph_engine import GraphFixReport
        from apme_engine.remediation.transforms import build_default_registry

        class DummyEngine:
            """Supply a completed engine run so this test isolates gate assembly."""

            def __init__(self, *args: object, **kwargs: object) -> None:
                """Accept the production engine constructor's inputs.

                Args:
                    *args: Positional engine options.
                    **kwargs: Named engine options.
                """
                self._registry = build_default_registry()
                self._max_ai_attempts = 2
                self._ai_provider = None
                self.remediate = AsyncMock(return_value=GraphFixReport(passes=1, fixed=1, nodes_modified=1))

        relative = "roles/web/includes/setup.yml"
        original = "- name: Original\n  debug: {}\n"
        flow = "[{name: first task, custom_module: {}}, {name: second task, custom_module: {}}]\n"
        expected = "---\n- name: first task\n  custom_module: {}\n\n- name: second task\n  custom_module: {}\n"
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text(original)
        graph = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path=f"{relative}/tasks[0]", node_type=NodeType.TASK),
            file_path=relative,
            yaml_lines=original,
            line_start=1,
            line_end=2,
        )
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "transformed", source="deterministic")
        session = SessionState(session_id=f"format-{gate}", status=1)
        session.temp_dir = tmp_path
        session.content_graph = graph
        session.graph_engine = DummyEngine()
        session.graph_originals = {relative: original}
        session.working_files = {relative: original.encode()}
        session.original_files = dict(session.working_files)
        session.fix_options = FixOptions(enable_ai=gate == "ai")
        servicer = server.EngineServicer()

        async def result_event(_self: server.EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult())

        async def scan(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine", DummyEngine),
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(
                    patches=[SourcePatch(path=relative, original=original, patched=flow, diff="")],
                    unpatched=[],
                ),
            ),
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch.object(server.EngineServicer, "_session_build_result", result_event),
        ):
            if gate == "initial":
                from apme_engine.formatter import format_content

                events = [
                    event
                    async for event in servicer._session_graph_remediate(
                        session=session,
                        scan_id="gate-format",
                        registry=MagicMock(),
                        scan_fn=scan,
                        captured_graph=[graph],
                        yaml_paths=[str(path)],
                        temp_dir=tmp_path,
                        max_passes=1,
                        progress_queue=asyncio.Queue(),
                        progress_callback=lambda *args: None,
                        _heartbeat=_noop_heartbeat,
                        format_content=format_content,
                        format_diffs=[],
                    )
                ]
            elif gate == "approval":
                session.proposals = {"t1-0000": Proposal(id="t1-0000", path=node.node_id, file=relative, tier=1)}
                events = [event async for event in servicer._session_handle_approval(session, {"t1-0000"})]
            else:
                events = [event async for event in servicer._session_run_ai_gate(session)]
        assert not any(event.HasField("error") for event in events)
        assert session.working_files[relative].decode() == expected
        assert path.read_text() == expected

    @pytest.mark.parametrize("cancel_first", [False, True])  # type: ignore[untyped-decorator]
    async def test_resumed_approvals_do_not_race_preview(self, tmp_path: Path, cancel_first: bool) -> None:
        """Concurrent approval and replay see stable state, including after disconnect.

        Args:
            tmp_path: Session working directory.
            cancel_first: Whether the first approving stream disconnects during preview.
        """
        import threading

        from apme_engine.daemon import engine_server as server
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        original = "- name: Task\n  debug: {}\n"
        graph = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path="tasks.yml/tasks[0]", node_type=NodeType.TASK),
            file_path="tasks.yml",
            line_start=1,
            line_end=2,
            yaml_lines=original,
        )
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(original.replace("debug:", "ansible.builtin.debug:"))
        node.record_state(1, "transformed", source="deterministic")
        session = SessionState(session_id="resumed-approval", status=1)
        session.temp_dir = tmp_path
        session.content_graph = graph
        session.graph_originals = {"tasks.yml": original}
        session.working_files = {"tasks.yml": original.encode()}
        session.proposals = {"t1-0000": Proposal(id="t1-0000", path=node.node_id, file="tasks.yml", tier=1)}
        pending = dict(session.proposals)
        servicer = server.EngineServicer()
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        released = threading.Event()
        preview_fn = server._preview_graph_approvals
        apply_fn = server._apply_node_approval_decisions
        mutation_threads: list[int] = []

        def slow_preview(
            s: SessionState, g: ContentGraph, originals: dict[str, str], choices: set[str]
        ) -> server._GraphApprovalPreview:
            loop.call_soon_threadsafe(entered.set)
            assert released.wait(5), "preview was never released"
            return preview_fn(s, g, originals, choices)

        def record_mutations(
            g: ContentGraph, decisions: tuple[tuple[str, str], ...], choices: frozenset[str]
        ) -> tuple[set[str], set[str]]:
            mutation_threads.append(threading.get_ident())
            return apply_fn(g, decisions, choices)

        async def consume() -> list[SessionEvent]:
            return [event async for event in servicer._session_handle_approval(session, {"t1-0000"})]

        async def result_event(_self: server.EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult())

        async def preview_finished() -> None:
            while session.approval_in_flight:
                await asyncio.sleep(0.001)

        with (
            patch.object(server, "_preview_graph_approvals", side_effect=slow_preview),
            patch.object(server, "_apply_node_approval_decisions", side_effect=record_mutations),
            patch.object(server.EngineServicer, "_session_build_result", result_event),
        ):
            first = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert session.approval_in_flight
                replay = [event async for event in servicer._session_replay_state(session)]
                assert [proposal.id for event in replay for proposal in event.proposals.proposals] == ["t1-0000"]
                busy = await consume()
                assert [event.WhichOneof("event") for event in busy] == ["error"]
                assert busy[0].error.code == "approval_in_progress"
                assert session.proposals == pending
                assert not session.approved_proposals
                assert session.working_files == {"tasks.yml": original.encode()}
                if cancel_first:
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    assert session.approval_in_flight
                released.set()
                if cancel_first:
                    await asyncio.wait_for(preview_finished(), 5)
                    assert session.proposals == pending
                    assert not session.approved_proposals
                    events = await consume()
                else:
                    events = await asyncio.wait_for(first, 5)
                assert next(event for event in events if event.HasField("approval_ack")).approval_ack.applied_count == 1
            finally:
                released.set()
                if not first.done():
                    first.cancel()
                await asyncio.gather(first, return_exceptions=True)
        assert not session.approval_in_flight
        assert len(session.approved_proposals) == 1
        assert not session.proposals
        assert mutation_threads[-1] == threading.get_ident()
        assert all(thread != threading.get_ident() for thread in mutation_threads[:-1])
        assert b"ansible.builtin.debug" in session.working_files["tasks.yml"]

    async def test_conflicting_approval_emits_session_error(self, tmp_path: Path) -> None:
        """A real splice conflict remains a session error and cannot write partial patches.

        Args:
            tmp_path: Temporary session working directory.
        """
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        original = "- name: Parent\n  block:\n    - debug: {}\n"
        graph = ContentGraph()
        session = SessionState(session_id="conflict")
        session.temp_dir = tmp_path
        session.status = 1
        session.working_files = {"tasks.yml": original.encode()}
        session.pre_gate2_files = {"tasks.yml": b"must not replace working files\n"}
        session.graph_originals = {"tasks.yml": original}
        session.content_graph = graph
        for index, (before, after, start, node_type) in enumerate(
            (
                (original, original.replace("debug: {}", "ansible.builtin.command: echo hello"), 1, NodeType.BLOCK),
                ("    - debug: {}\n", "    - ansible.builtin.debug: {}\n", 3, NodeType.TASK),
            )
        ):
            node = ContentNode(
                identity=NodeIdentity(path=f"tasks.yml/tasks[{index}]", node_type=node_type),
                file_path="tasks.yml",
                line_start=start,
                line_end=3,
                yaml_lines=before,
            )
            graph.add_node(node)
            node.record_state(0, "scanned")
            node.update_from_yaml(after)
            node.record_state(1, "transformed", source="deterministic")
            pid = f"t1-{index:04d}"
            session.proposals[pid] = Proposal(id=pid, path=node.node_id, file="tasks.yml", tier=1)
        servicer = EngineServicer()
        pending = dict(session.proposals)
        progressions = {node.node_id: list(node.progression) for node in graph.nodes()}
        events = [event async for event in servicer._session_handle_approval(session, set(session.proposals))]
        assert [event.WhichOneof("event") for event in events] == ["error"]
        assert events[0].error.code == "remediation_failed"
        assert "Conflicting remediation edits" in events[0].error.message
        assert session.working_files == {"tasks.yml": original.encode()}
        assert not (tmp_path / "tasks.yml").exists()
        assert session.status != 3
        assert session.proposals == pending
        assert not session.approved_ids
        assert not session.approved_proposals
        assert not session.rejected_proposals
        assert {node.node_id: node.progression for node in graph.nodes()} == progressions

        async def result_event(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult())

        with patch.object(EngineServicer, "_session_build_result", result_event):
            retry = [event async for event in servicer._session_handle_approval(session, {"t1-0001"})]
        assert [event.WhichOneof("event") for event in retry] == ["approval_ack", "result"]
        assert retry[0].approval_ack.applied_count == 1
        expected = "---\n" + original.replace("- debug:", "- ansible.builtin.debug:")
        assert session.working_files["tasks.yml"] == expected.encode()
        assert (tmp_path / "tasks.yml").read_text() == expected

    def test_partial_graph_tier1_approval_reverts_declined_nodes(self) -> None:
        """Approving 1 of 2 Tier 1 proposals keeps only that node's edits.

        Interactive Gate 1 stages transforms on many nodes; partial approval
        must reject the rest so splice does not write declined fixes.
        """
        from dataclasses import replace

        from apme.v1.engine_pb2 import Proposal
        from apme_engine.daemon.engine_server import _apply_graph_approvals
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.graph_engine import Tier1NodeProposal

        original = "- name: t\n  apt:\n    name: x\n"
        fixed = "- name: t\n  ansible.builtin.apt:\n    name: x\n"

        def _node(path: str, file_path: str) -> ContentNode:
            node = ContentNode(
                identity=NodeIdentity(path=path, node_type=NodeType.TASK),
                file_path=file_path,
                line_start=1,
                line_end=3,
                module="apt",
                yaml_lines=original,
            )
            node.record_state(0, "scanned")
            node.update_from_yaml(fixed)
            node.record_state(1, "transformed", source="deterministic")
            # Mark the scan baseline approved so reject keeps the first snapshot
            # when no later progression entry is approved (source="" scan row).
            node.progression[0] = replace(node.progression[0], approved=True)
            return node

        graph = ContentGraph()
        n0 = _node("a.yml/tasks[0]", "a.yml")
        n1 = _node("b.yml/tasks[0]", "b.yml")
        graph.add_node(n0)
        graph.add_node(n1)

        session = SessionState(session_id="partial-t1")
        session.content_graph = graph
        session.graph_originals = {"a.yml": original, "b.yml": original}
        session.tier1_proposals = [
            Tier1NodeProposal(
                node_id=n0.node_id,
                file_path="a.yml",
                before_yaml=original,
                after_yaml=fixed,
                rule_ids=["M001"],
            ),
            Tier1NodeProposal(
                node_id=n1.node_id,
                file_path="b.yml",
                before_yaml=original,
                after_yaml=fixed,
                rule_ids=["M001"],
            ),
        ]
        session.proposals = {
            "t1-0000": Proposal(
                id="t1-0000",
                file="a.yml",
                rule_id="M001",
                path=n0.node_id,
                source="deterministic",
                tier=1,
            ),
            "t1-0001": Proposal(
                id="t1-0001",
                file="b.yml",
                rule_id="M001",
                path=n1.node_id,
                source="deterministic",
                tier=1,
            ),
        }

        applied, rejected, _approved, patches = _apply_graph_approvals(
            session,
            graph,
            session.graph_originals,
            {"t1-0000"},
        )
        assert applied == 1
        assert n1.node_id in rejected
        assert n0.yaml_lines == fixed
        assert n1.yaml_lines == original
        assert len(patches) == 1
        assert patches[0].path == "a.yml"
        assert "ansible.builtin.apt" in patches[0].patched
        assert session.working_files["b.yml"] == original.encode()
        assert all(s.approved for s in n0.progression)
        assert len(n1.progression) == 1

    async def test_tier1_approval_without_ai_finalizes(self) -> None:
        """Tier 1 approval finalizes immediately when AI gate is disabled."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="gate-no-ai")
        session.awaiting_tier1_gate = True
        session.fix_options = FixOptions(enable_ai=False)
        session.status = 1

        async def _fake_build_result(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult())

        with patch.object(EngineServicer, "_session_build_result", _fake_build_result):
            events = [e async for e in servicer._session_handle_approval(session, set())]

        event_types = [e.WhichOneof("event") for e in events]
        assert event_types == ["progress", "approval_ack", "result"]
        assert "Applied 0 approved Tier 1 proposal" in (events[0].progress.message or "")
        assert session.status == 3  # COMPLETE

    async def test_approval_reassesses_applied_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Approval reports findings from the new bytes, not the initial snapshot.

        Args:
            monkeypatch: Configure a synthetic Native endpoint.
        """
        from apme_engine.daemon.engine_server import EngineServicer, _ValidatorResult

        monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "native")
        session = SessionState(session_id="approval-rescan", status=1)
        session.working_files = {"molecule/default/molecule.yml": b"old: value\n"}
        old: ViolationDict = {"rule_id": "L115", "path": "$.old", "message": "old"}
        new: ViolationDict = {
            "rule_id": "L116",
            "file": "molecule/default/molecule.yml",
            "line": 1,
            "path": "$.new",
            "message": "new",
            "remediation_class": "manual-review",
        }
        session.file_violations = [old]
        session.remaining_manual = [old]
        session.report = FixReport(remaining_manual=1)

        def apply(*_args: object, **_kwargs: object) -> tuple[int, None]:
            session.working_files["molecule/default/molecule.yml"] = b"new: value\n"
            session.status = 3
            return 1, None

        async def build_result(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult(report=_session.report))

        dispatch = AsyncMock(return_value=_ValidatorResult(violations=[new]))
        with (
            patch.object(EngineServicer, "_session_apply_approved", side_effect=apply),
            patch.object(EngineServicer, "_session_build_result", build_result),
            patch("apme_engine.daemon.engine_server._call_validator", dispatch),
        ):
            events = [event async for event in EngineServicer()._session_handle_approval(session, {"proposal"})]

        awaited = dispatch.await_args
        assert awaited is not None
        assert awaited.args[1].files[0].content == b"new: value\n"
        report = events[-1].result.report
        assert report is not None
        assert [v.message for v in report.remaining_violations] == ["new"]
        assert session.report is not None and session.report.remaining_manual == 1

    async def test_approval_requires_native_for_reassessment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing required Native cannot produce an approval result with stale findings.

        Args:
            monkeypatch: Remove the Native endpoint from the environment.
        """
        from apme_engine.daemon.engine_server import EngineServicer, RequiredValidatorDependencyError

        monkeypatch.delenv("NATIVE_GRPC_ADDRESS", raising=False)
        session = SessionState(session_id="missing-native", working_files={"molecule/default/molecule.yml": b"---"})
        with (
            patch.object(EngineServicer, "_session_apply_approved", return_value=(1, None)),
            pytest.raises(RequiredValidatorDependencyError, match="native not configured"),
        ):
            _ = [event async for event in EngineServicer()._session_handle_approval(session, {"proposal"})]
        assert session.status != 3
        assert session.pending_file_reassessment == "complete"

    @pytest.mark.parametrize("run_ai", [False, True])  # type: ignore[untyped-decorator]
    async def test_approval_reassessment_retries_on_resume(self, run_ai: bool) -> None:
        """Resume retries a failed assessment before completing or entering AI.

        Args:
            run_ai: Whether approval should continue to the AI gate.
        """
        from apme_engine.daemon.engine_server import EngineServicer, RequiredValidatorDependencyError

        session = SessionState(session_id="retry-file-assessment", status=1)
        session.awaiting_tier1_gate = run_ai
        session.fix_options = FixOptions(enable_ai=run_ai)
        session.report = FixReport()
        session.file_violations = [{"rule_id": "L115", "path": "$.old", "message": "old"}]
        attempts = 0

        async def reassess(current: SessionState) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RequiredValidatorDependencyError("unavailable")
            current.file_violations = [{"rule_id": "L116", "path": "$.new", "message": "new"}]

        refresh = AsyncMock(side_effect=reassess)
        emit = AsyncMock()

        async def enter_ai(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(progress=ProgressUpdate(message="AI gate"))

        servicer = EngineServicer()
        with (
            patch.object(EngineServicer, "_session_apply_approved", return_value=(1, None)),
            patch("apme_engine.daemon.engine_server._refresh_session_file_violations", refresh),
            patch("apme_engine.daemon.engine_server.emit_fix_completed", emit),
            patch.object(EngineServicer, "_session_enter_ai_phase", enter_ai),
        ):
            with pytest.raises(RequiredValidatorDependencyError):
                _ = [event async for event in servicer._session_handle_approval(session, {"proposal"})]
            assert session.status != 3
            assert session.pending_file_reassessment == ("ai" if run_ai else "complete")
            events = [event async for event in servicer._session_replay_state(session)]

        assert refresh.await_count == 2
        assert not session.pending_file_reassessment
        if run_ai:
            assert events[-1].progress.message == "AI gate"
            assert session.status != 3
        else:
            assert events[-1].WhichOneof("event") == "result"
            assert session.status == 3
            assert [v.message for v in events[-1].result.remaining_violations] == ["new"]
            emit.assert_awaited_once()

    @pytest.mark.parametrize("run_ai", [False, True])  # type: ignore[untyped-decorator]
    async def test_approval_retries_disk_writes_before_reassessment(self, tmp_path: Path, run_ai: bool) -> None:
        """Repeated write failures keep applied patches available for resume.

        Args:
            tmp_path: Session directory for the applied playbook.
            run_ai: Whether the continuation should enter the AI gate.
        """
        from apme_engine.daemon.engine_server import EngineServicer, _write_patches_to_temp_dir
        from apme_engine.remediation.graph_engine import FilePatch as SplicedFilePatch

        session = SessionState(session_id="retry-file-write", status=1, temp_dir=tmp_path)
        session.awaiting_tier1_gate = run_ai
        session.fix_options = FixOptions(enable_ai=run_ai)
        session.report = FixReport()
        patch_file = SplicedFilePatch(path="play.yml", original="old", patched="new", diff="")
        session.working_files = {"play.yml": b"new"}
        writes = 0

        def write(root: Path, patches: list[SplicedFilePatch]) -> None:
            nonlocal writes
            writes += 1
            if writes <= 2:
                raise OSError("temporary write failure")
            _write_patches_to_temp_dir(root, patches)

        async def reassess(_session: SessionState) -> None:
            assert (tmp_path / "play.yml").read_text(encoding="utf-8") == "new"
            _session.file_violations = [{"rule_id": "L116", "path": "$.new", "message": "new"}]

        async def enter_ai(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(progress=ProgressUpdate(message="AI gate"))

        refresh = AsyncMock(side_effect=reassess)
        servicer = EngineServicer()
        with (
            patch.object(EngineServicer, "_session_apply_approved", return_value=(1, [patch_file])) as apply,
            patch("apme_engine.daemon.engine_server._write_patches_to_temp_dir", side_effect=write),
            patch("apme_engine.daemon.engine_server._refresh_session_file_violations", refresh),
            patch("apme_engine.daemon.engine_server.emit_fix_completed", new_callable=AsyncMock),
            patch.object(EngineServicer, "_session_enter_ai_phase", enter_ai),
        ):
            with pytest.raises(OSError, match="temporary write failure"):
                _ = [event async for event in servicer._session_finish_approval(session, {"proposal"}, None)]
            assert session.pending_file_reassessment == ("ai" if run_ai else "complete")
            assert session.pending_file_patches == [patch_file]
            assert session.status != 3
            refresh.assert_not_awaited()
            with pytest.raises(OSError, match="temporary write failure"):
                _ = [event async for event in servicer._session_replay_state(session)]
            assert session.pending_file_patches == [patch_file]
            refresh.assert_not_awaited()
            events = [event async for event in servicer._session_replay_state(session)]

        assert writes == 3
        apply.assert_called_once()
        refresh.assert_awaited_once_with(session)
        assert not session.pending_file_patches and not session.pending_file_reassessment
        if run_ai:
            assert events[-1].progress.message == "AI gate"
        else:
            assert events[-1].WhichOneof("event") == "result"
            assert [v.message for v in events[-1].result.remaining_violations] == ["new"]

    @pytest.mark.parametrize("splice_conflict", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("fail_write", [False, True])  # type: ignore[untyped-decorator]
    async def test_ai_gate_filters_stale_t1_and_formats_splice(
        self, tmp_path: Path, fail_write: bool, splice_conflict: bool
    ) -> None:
        """Gate 2 drops stale t1-* pending IDs; no-proposal path may sync files.

        Args:
            tmp_path: Pytest temporary directory fixture.
            fail_write: Fail the first write and resume the pending work.
            splice_conflict: Whether source assembly reports conflicting edits.
        """
        from apme_engine.daemon.engine_server import EngineServicer, _write_patches_to_temp_dir
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import FilePatch as SplicedFilePatch
        from apme_engine.remediation.graph_engine import SpliceConflictError

        class _DummyGraphEngine:
            def __init__(self) -> None:
                from apme_engine.remediation.transforms import build_default_registry

                self._registry = build_default_registry()
                self._ai_provider = None
                self._max_ai_attempts = 2
                self.remediate = AsyncMock(
                    return_value=SimpleNamespace(
                        passes=1,
                        fixed=0,
                        remaining_violations=[],
                        fixed_violations=[],
                        oscillation_detected=False,
                        ai_proposals=[],
                        tier1_proposals=[],
                    )
                )

        servicer = EngineServicer()
        session = SessionState(session_id="gate-filter")
        session.content_graph = ContentGraph()
        session.graph_engine = _DummyGraphEngine()
        session.working_files = {"play.yml": b"- name: test\n  debug:\n    msg: hi\n"}
        session.proposals = {
            "t1-0000": Proposal(
                id="t1-0000",
                file="play.yml",
                rule_id="L001",
                tier=1,
                source="deterministic",
            )
        }
        session.fix_options = FixOptions(enable_ai=True)
        session.graph_originals = {"play.yml": "- name: test\n  debug:\n    msg: hi\n"}
        session.temp_dir = tmp_path

        file_finding: ViolationDict = {
            "rule_id": "L115",
            "file": "molecule/default/molecule.yml",
            "line": 1,
            "path": "$.provisioner",
            "scope": "collection",
            "remediation_class": "manual-review",
        }
        session.file_violations = [file_finding]
        session.remaining_manual = [dict(file_finding)]

        async def _fake_build_result(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            async for event in _self._session_serialize_result(_session):
                yield event

        writes = 0

        def write(root: Path, patches: list[SplicedFilePatch]) -> None:
            nonlocal writes
            writes += 1
            if fail_write and writes == 1:
                raise OSError("temporary write failure")
            _write_patches_to_temp_dir(root, patches)

        with (
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine", _DummyGraphEngine),
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(
                    patches=[
                        SplicedFilePatch(
                            path="play.yml",
                            original="",
                            patched="- name: test\n  debug:\n    msg: hi\n",
                            diff="",
                        )
                    ],
                    unpatched=[],
                ),
                side_effect=SpliceConflictError("conflicting gate edits") if splice_conflict else None,
            ),
            patch(
                "apme_engine.formatter.format_content",
                return_value=SimpleNamespace(changed=True, formatted="- name: test\n  debug:\n    msg: hi there\n"),
            ),
            patch.object(EngineServicer, "_session_build_result", _fake_build_result),
            patch("apme_engine.daemon.engine_server._write_patches_to_temp_dir", side_effect=write),
            patch(
                "apme_engine.daemon.engine_server._refresh_session_file_violations", new_callable=AsyncMock
            ) as refresh,
        ):
            events: list[SessionEvent] = []
            if fail_write and not splice_conflict:
                with pytest.raises(OSError, match="temporary write failure"):
                    async for event in servicer._session_run_ai_gate(session):
                        events.append(event)
                assert session.pending_file_reassessment == "complete"
                assert len(session.pending_file_patches) == 1
                refresh.assert_not_awaited()
                events.extend([event async for event in servicer._session_replay_state(session)])
            else:
                events = [e async for e in servicer._session_run_ai_gate(session)]

        event_types = [e.WhichOneof("event") for e in events]
        if splice_conflict:
            assert event_types == ["progress", "error"]
            assert events[-1].error.code == "remediation_failed"
            assert events[-1].error.message == "conflicting gate edits"
            assert session.working_files["play.yml"] == session.pre_gate2_files["play.yml"]
            assert not (tmp_path / "play.yml").exists()
            assert session.status != 3
            return
        refresh.assert_awaited_once_with(session)
        assert writes == (2 if fail_write else 1)
        assert event_types == ["progress", "result"]
        assert session.remaining_ai == []
        assert session.remaining_manual == [file_finding]
        assert session.report is not None
        assert session.report.remaining_manual == 1
        assert len(session.report.remaining_violations) == 1
        assert len(events[-1].result.remaining_violations) == 1
        assert session.proposals == {}
        assert "t1-0000" in session.rejected_proposals
        assert session.pre_gate2_files["play.yml"] == b"- name: test\n  debug:\n    msg: hi\n"
        # No AI proposals → complete path may format-sync working_files.
        assert session.working_files["play.yml"] == b"- name: test\n  debug:\n    msg: hi there\n"
        assert (tmp_path / "play.yml").read_text(encoding="utf-8") == "- name: test\n  debug:\n    msg: hi there\n"
        # Gate 2 must not re-run Gate 1 Tier 1; keep AI pending until approval.
        session.graph_engine.remediate.assert_awaited()
        await_args = session.graph_engine.remediate.await_args
        assert await_args is not None
        _args, kwargs = await_args
        assert kwargs.get("skip_tier1") is True
        assert kwargs.get("skip_ai") is False
        assert kwargs.get("interactive") is True

    async def test_ai_gate_does_not_write_working_files_while_awaiting(self, tmp_path: Path) -> None:
        """Pending Gate 2 proposals must not mutate working_files before approve.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import AINodeProposal

        ai_prop = AINodeProposal(
            node_id="play.yml/plays[0]/tasks[0]",
            file_path="play.yml",
            before_yaml="- name: test\n  debug:\n    msg: hi\n",
            after_yaml="- name: test\n  debug:\n    msg: GUTTED\n",
            rule_ids=["L050"],
            explanation="rename",
            confidence=0.9,
            line_start=1,
            line_end=3,
        )

        class _DummyGraphEngine:
            def __init__(self) -> None:
                from apme_engine.remediation.transforms import build_default_registry

                self._registry = build_default_registry()
                self._ai_provider = None
                self._max_ai_attempts = 2
                self.remediate = AsyncMock(
                    return_value=SimpleNamespace(
                        passes=1,
                        fixed=0,
                        remaining_violations=[],
                        fixed_violations=[],
                        oscillation_detected=False,
                        ai_proposals=[ai_prop],
                        tier1_proposals=[],
                    )
                )

        servicer = EngineServicer()
        session = SessionState(session_id="gate-no-leak")
        session.content_graph = ContentGraph()
        session.graph_engine = _DummyGraphEngine()
        baseline = b"- name: test\n  debug:\n    msg: hi\n"
        session.working_files = {"play.yml": baseline}
        session.fix_options = FixOptions(enable_ai=True)
        session.graph_originals = {"play.yml": baseline.decode()}
        session.temp_dir = tmp_path
        (tmp_path / "play.yml").write_bytes(baseline)

        with (
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine", _DummyGraphEngine),
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(
                    patches=[
                        SplicedFilePatch(
                            path="play.yml",
                            original="",
                            patched="- name: test\n  debug:\n    msg: GUTTED\n",
                            diff="",
                        )
                    ],
                    unpatched=[],
                ),
            ),
        ):
            events = [e async for e in servicer._session_run_ai_gate(session)]

        assert [e.WhichOneof("event") for e in events] == ["progress", "proposals"]
        assert session.status == 1  # AWAITING_APPROVAL
        assert session.working_files["play.yml"] == baseline
        assert (tmp_path / "play.yml").read_bytes() == baseline
        assert session.pre_gate2_files["play.yml"] == baseline

    async def test_ai_gate_pauses_on_declined_only_proposals(self, tmp_path: Path) -> None:
        """Gate 2 emits ProposalsReady when AI produces only declined rows.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph

        remaining = {
            "rule_id": "L043",
            "file": "play.yml",
            "path": "play.yml/plays[0]/tasks[0]",
            "message": "Fix Jinja2 spacing.",
            "severity": "medium",
            "line": 1,
            "scope": "task",
        }

        class _DummyGraphEngine:
            def __init__(self) -> None:
                from apme_engine.remediation.transforms import build_default_registry

                self._registry = build_default_registry()
                self._ai_provider = None
                self._max_ai_attempts = 2
                self.remediate = AsyncMock(
                    return_value=SimpleNamespace(
                        passes=1,
                        fixed=0,
                        remaining_violations=[remaining],
                        fixed_violations=[],
                        oscillation_detected=False,
                        ai_proposals=[],
                        tier1_proposals=[],
                    )
                )

        servicer = EngineServicer()
        session = SessionState(session_id="gate-declined-only")
        session.content_graph = ContentGraph()
        session.graph_engine = _DummyGraphEngine()
        baseline = b"- name: test\n  command: echo hi\n"
        session.working_files = {"play.yml": baseline}
        session.fix_options = FixOptions(enable_ai=True)
        session.graph_originals = {"play.yml": baseline.decode()}
        session.temp_dir = tmp_path
        (tmp_path / "play.yml").write_bytes(baseline)

        with patch("apme_engine.remediation.graph_engine.GraphRemediationEngine", _DummyGraphEngine):
            events = [e async for e in servicer._session_run_ai_gate(session)]

        assert [e.WhichOneof("event") for e in events] == ["progress", "proposals"]
        assert session.status == 1  # AWAITING_APPROVAL
        assert session.proposals == {}
        assert list(session.review_declined_proposals) == ["ai-declined-0000"]
        emitted = events[1].proposals.proposals
        assert len(emitted) == 1
        assert emitted[0].id == "ai-declined-0000"
        assert emitted[0].status == "declined"
        assert emitted[0].tier == 2
        assert session.working_files["play.yml"] == baseline
        assert (tmp_path / "play.yml").read_bytes() == baseline

    async def test_ai_gate_pauses_on_post_ai_tier1_cleanup(self, tmp_path: Path) -> None:
        """Post-AI deterministic cleanup is staged as g2-t1-* Gate 2 review.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import Tier1NodeProposal

        t1_prop = Tier1NodeProposal(
            node_id="play.yml/plays[0]/tasks[0]",
            file_path="play.yml",
            before_yaml="- name: test\n  command: echo hi\n",
            after_yaml="- name: test\n  ansible.builtin.command:\n    cmd: echo hi\n",
            rule_ids=["L001"],
            violation_summaries=["[L001]: FQCN"],
            line_start=1,
            line_end=2,
            node_type="task",
        )

        class _DummyGraphEngine:
            def __init__(self) -> None:
                from apme_engine.remediation.transforms import build_default_registry

                self._registry = build_default_registry()
                self._ai_provider = None
                self._max_ai_attempts = 2
                self.remediate = AsyncMock(
                    return_value=SimpleNamespace(
                        passes=1,
                        fixed=0,
                        remaining_violations=[],
                        fixed_violations=[],
                        oscillation_detected=False,
                        ai_proposals=[],
                        tier1_proposals=[t1_prop],
                    )
                )

        servicer = EngineServicer()
        session = SessionState(session_id="gate-g2-t1")
        session.content_graph = ContentGraph()
        session.graph_engine = _DummyGraphEngine()
        baseline = b"- name: test\n  command: echo hi\n"
        session.working_files = {"play.yml": baseline}
        session.fix_options = FixOptions(enable_ai=True)
        session.graph_originals = {"play.yml": baseline.decode()}
        session.temp_dir = tmp_path
        (tmp_path / "play.yml").write_bytes(baseline)

        with patch("apme_engine.remediation.graph_engine.GraphRemediationEngine", _DummyGraphEngine):
            events = [e async for e in servicer._session_run_ai_gate(session)]

        assert [e.WhichOneof("event") for e in events] == ["progress", "proposals"]
        assert session.status == 1
        assert list(session.proposals) == ["g2-t1-0000"]
        emitted = events[1].proposals.proposals
        assert emitted[0].id == "g2-t1-0000"
        assert emitted[0].tier == 2
        assert emitted[0].source == "deterministic"
        assert session.working_files["play.yml"] == baseline
        assert (tmp_path / "play.yml").read_bytes() == baseline

    def test_consolidate_gate2_proposals_merges_same_node(self) -> None:
        """AI and post-AI Tier 1 rows for one node become a single proposal."""
        from apme_engine.daemon.engine_server import EngineServicer

        ai_row = Proposal(
            id="ai-0000",
            file="play.yml",
            path="play.yml/plays[0]/tasks[0]",
            rule_id="L050",
            after_text="ai after",
            explanation="AI rewrite",
            tier=2,
            source="ai",
        )
        t1_same = Proposal(
            id="g2-t1-0000",
            file="play.yml",
            path="play.yml/plays[0]/tasks[0]",
            rule_id="L001",
            after_text="t1 after",
            diff_hunk="@@ t1 @@",
            explanation="FQCN cleanup",
            tier=2,
            source="deterministic",
        )
        t1_other = Proposal(
            id="g2-t1-0001",
            file="other.yml",
            path="other.yml/plays[0]/tasks[0]",
            rule_id="M001",
            after_text="other after",
            tier=2,
            source="deterministic",
        )

        merged = EngineServicer._consolidate_gate2_proposals([ai_row], [t1_same, t1_other])
        assert [p.id for p in merged] == ["ai-0000", "g2-t1-0001"]
        assert merged[0].after_text == "t1 after"
        assert merged[0].diff_hunk == "@@ t1 @@"
        assert set(merged[0].rule_id.split(",")) == {"L001", "L050"}
        assert "FQCN cleanup" in merged[0].explanation
        assert merged[1].id == "g2-t1-0001"

    async def test_declined_only_gate2_approval_completes(self) -> None:
        """Empty approve on declined-only Gate 2 marks the session complete."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="declined-approve")
        session.status = 1
        session.awaiting_tier1_gate = False
        session.review_declined_proposals = {
            "ai-declined-0000": Proposal(
                id="ai-declined-0000",
                file="play.yml",
                rule_id="L005",
                tier=2,
                status="declined",
                source="ai",
            )
        }

        async def _fake_build_result(_self: EngineServicer, _session: SessionState) -> AsyncIterator[SessionEvent]:
            yield SessionEvent(result=SessionResult())

        with patch.object(EngineServicer, "_session_build_result", _fake_build_result):
            events = [e async for e in servicer._session_handle_approval(session, set())]

        assert [e.WhichOneof("event") for e in events] == ["approval_ack", "result"]
        assert session.status == 3  # COMPLETE
        assert session.review_declined_proposals == {}
        assert events[0].approval_ack.applied_count == 0

    async def test_decline_all_restores_pre_gate2_working_files(self, tmp_path: Path) -> None:
        """Declining every Gate 2 proposal restores the pre-AI working tree.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph

        baseline = b"- hosts: all\n  tasks:\n    - debug: msg=keep\n"
        leaked = b"- hosts: all\n  become: true\n  tasks: []\n"
        binary = b"caf\xe9\n"
        template = "roles/web/templates/message.j2"
        session = SessionState(session_id="decline-restore", status=1)
        session.content_graph = ContentGraph()
        session.graph_originals = {"play.yml": baseline.decode()}
        session.original_files = {"play.yml": baseline, template: binary}
        session.pre_gate2_files = dict(session.original_files)
        session.working_files = {"play.yml": leaked, template: binary}
        session.temp_dir = tmp_path
        (tmp_path / "play.yml").write_bytes(leaked)
        (tmp_path / template).parent.mkdir(parents=True)
        (tmp_path / template).write_bytes(binary)
        session.proposals = {
            "ai-0000": Proposal(
                id="ai-0000",
                file="play.yml",
                path="play.yml/plays[0]",
                rule_id="L050",
                tier=2,
                source="ai",
            )
        }
        events = [event async for event in EngineServicer()._session_handle_approval(session, set())]
        assert [event.WhichOneof("event") for event in events] == ["approval_ack", "result"]
        assert events[0].approval_ack.applied_count == 0
        assert not events[1].result.patches
        assert session.status == 3
        assert session.working_files == session.pre_gate2_files
        assert not session.proposals
        for path, content in session.pre_gate2_files.items():
            assert (tmp_path / path).read_bytes() == content

    def test_build_fix_event_includes_rejected_telemetry_store(self) -> None:
        """FixCompletedEvent includes rejected outcomes from telemetry snapshots."""
        from apme_engine.daemon.engine_server import EngineServicer

        session = SessionState(session_id="fix-event")
        session.rejected_proposals = {
            "t1-0000": {
                "proposal_id": "t1-0000",
                "rule_id": "L001",
                "file": "play.yml",
                "tier": 1,
                "confidence": 0.0,
            }
        }

        event = EngineServicer._build_fix_event(session, [])
        assert len(event.proposals) == 1
        assert event.proposals[0].proposal_id == "t1-0000"
        assert event.proposals[0].status == "rejected"


class TestGalaxyProxyConfigActivation:
    """Tests for session-scoped Galaxy proxy config (no global mutation)."""

    async def test_sets_and_restores_ansible_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Session Galaxy config travels in the yielded env snapshot, not os.environ.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture for safe env manipulation.
        """
        from apme_engine.daemon.engine_server import EngineServicer

        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\nserver_list = certified\n")
        servicer = EngineServicer()
        monkeypatch.setenv("ANSIBLE_CONFIG", "/tmp/original.cfg")

        async with servicer._activate_galaxy_proxy_config(cfg) as env:
            assert env["ANSIBLE_CONFIG"] == str(cfg)
            assert os.environ["ANSIBLE_CONFIG"] == "/tmp/original.cfg"

        assert os.environ["ANSIBLE_CONFIG"] == "/tmp/original.cfg"

    async def test_clears_ansible_config_when_unset_beforehand(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Snapshot carries the session config; global env stays unset.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture for safe env manipulation.
        """
        from apme_engine.daemon.engine_server import EngineServicer

        cfg = tmp_path / "ansible.cfg"
        cfg.write_text("[galaxy]\nserver_list = certified\n")
        servicer = EngineServicer()
        monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)

        async with servicer._activate_galaxy_proxy_config(cfg) as env:
            assert env["ANSIBLE_CONFIG"] == str(cfg)
            assert "ANSIBLE_CONFIG" not in os.environ

        assert "ANSIBLE_CONFIG" not in os.environ


class TestSessionReplayState:
    """Unit tests for _session_replay_state (session resume)."""

    async def test_replays_tier1_summary(self) -> None:
        """Replay emits tier1_complete when tier1 patches exist."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="test")
        session.tier1_patches = [
            FilePatch(path="x.yml", original=b"o", patched=b"p"),
        ]
        session.report = FixReport(passes=1, fixed=1)
        session.status = 3

        events = [e async for e in servicer._session_replay_state(session)]
        types = [e.WhichOneof("event") for e in events]
        assert "tier1_complete" in types

    async def test_replays_pending_proposals(self) -> None:
        """Replay emits tier1_complete and proposals when awaiting approval."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="test")
        session.tier1_patches = [FilePatch(path="x.yml")]
        session.report = FixReport()
        session.proposals = {"p1": Proposal(id="p1", file="x.yml", rule_id="L001")}
        session.current_tier = 2
        session.status = 1

        events = [e async for e in servicer._session_replay_state(session)]
        types = [e.WhichOneof("event") for e in events]
        assert "tier1_complete" in types
        assert "proposals" in types

    async def test_replays_declined_only_gate2_proposals(self) -> None:
        """Declined-only Gate 2 review rows replay after reconnect."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="declined-replay")
        session.report = FixReport()
        session.review_declined_proposals = {
            "ai-declined-0000": Proposal(
                id="ai-declined-0000",
                file="play.yml",
                rule_id="M001",
                tier=2,
                status="declined",
                source="ai",
            ),
        }
        session.current_tier = 2
        session.status = 1

        events = [e async for e in servicer._session_replay_state(session)]
        proposal_events = [e for e in events if e.WhichOneof("event") == "proposals"]
        assert len(proposal_events) == 1
        assert len(proposal_events[0].proposals.proposals) == 1
        assert proposal_events[0].proposals.proposals[0].id == "ai-declined-0000"

    async def test_replays_result_when_complete(self) -> None:
        """Replay emits final result when session status is complete."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        session = SessionState(session_id="test")
        session.original_files = {"a.yml": b"orig"}
        session.working_files = {"a.yml": b"patched"}
        session.report = FixReport(passes=1, fixed=1)
        session.status = 3

        events = [e async for e in servicer._session_replay_state(session)]
        types = [e.WhichOneof("event") for e in events]
        assert "result" in types


class TestSessionGraphRemediate:
    """Tests for _session_graph_remediate (graph engine remediation path).

    Exercises ``GraphRemediationEngine`` in-memory convergence, splicing
    results to disk, and graph-authoritative reporting of remaining violations.
    """

    @pytest.mark.parametrize("splice_conflict", [False, True])  # type: ignore[untyped-decorator]
    async def test_happy_path_produces_patches_and_events(self, tmp_path: Path, splice_conflict: bool) -> None:
        """Graph engine fixes violations, splices files, and emits correct events.

        Args:
            tmp_path: Pytest temporary directory fixture.
            splice_conflict: Whether source assembly reports conflicting edits.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.graph_engine import FilePatch as EngineFilePatch
        from apme_engine.remediation.graph_engine import GraphFixReport, SpliceConflictError

        servicer = EngineServicer.__new__(EngineServicer)

        yaml_content = "- name: Install\n  apt:\n    name: nginx\n    state: present\n"
        patched_content = "- name: Install\n  ansible.builtin.apt:\n    name: nginx\n    state: present\n"
        play_file = tmp_path / "play.yml"
        play_file.write_text(yaml_content)

        graph = ContentGraph()
        graph.add_node(
            ContentNode(
                identity=NodeIdentity(path="play.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
                file_path=str(play_file),
                yaml_lines=yaml_content,
            )
        )

        session = SessionState(session_id="test-graph-happy")
        session.working_files = {"play.yml": yaml_content.encode()}
        session.original_files = dict(session.working_files)

        call_count = [0]

        async def scan_fn(paths: list[str]) -> list[ViolationDict]:
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"rule_id": "L001", "message": "Use FQCN", "file": "play.yml", "line": 2}]
            return []

        mock_report = GraphFixReport(
            passes=1,
            fixed=1,
            nodes_modified=1,
            fixed_violations=[{"rule_id": "L001", "message": "Use FQCN", "file": "play.yml", "line": 2}],
        )
        mock_patches = [
            EngineFilePatch(
                path=str(play_file),
                original=yaml_content,
                patched=patched_content,
                diff="",
                rule_ids=["L001"],
            )
        ]

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=mock_patches, unpatched=[]),
                side_effect=SpliceConflictError("conflicting graph edits") if splice_conflict else None,
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch(
                "apme_engine.daemon.engine_server._refresh_session_file_violations", new_callable=AsyncMock
            ) as refresh,
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=mock_report)

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-graph-1",
                registry=MagicMock(),
                scan_fn=scan_fn,
                captured_graph=[graph],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        if splice_conflict:
            refresh.assert_not_awaited()
        else:
            refresh.assert_awaited_once_with(session)
        assert call_count[0] == 1, f"scan_fn should be called once (initial only, no final scan), got {call_count[0]}"

        # Verify the validator bridge (rescan_fn) was wired into the engine
        _, gre_kwargs = MockGRE.call_args
        assert "rescan_fn" in gre_kwargs, "rescan_fn must be passed to GraphRemediationEngine"
        assert callable(gre_kwargs["rescan_fn"])

        event_types = [e.WhichOneof("event") for e in events]
        if splice_conflict:
            assert event_types[-1] == "error"
            assert "tier1_complete" not in event_types
            assert "result" not in event_types
            assert events[-1].error.code == "remediation_failed"
            assert events[-1].error.message == "conflicting graph edits"
            assert session.working_files["play.yml"] == yaml_content.encode()
            assert play_file.read_text() == yaml_content
            assert session.status != 3
            return
        assert "progress" in event_types
        assert "tier1_complete" in event_types
        assert "result" in event_types

        progress_msgs = [e.progress.message for e in events if e.HasField("progress")]
        assert any("Graph Tier 1 converged" in m for m in progress_msgs)
        assert any("1 pass(es)" in m for m in progress_msgs)
        assert any("1 nodes modified" in m for m in progress_msgs)

        t1 = next(e for e in events if e.HasField("tier1_complete"))
        assert len(t1.tier1_complete.applied_patches) == 1
        assert t1.tier1_complete.applied_patches[0].path == "play.yml"
        assert t1.tier1_complete.applied_patches[0].applied_rules == ["L001"]
        report = t1.tier1_complete.report
        assert report is not None
        assert report.passes == 1
        assert report.fixed == 1

        assert session.status == 3  # COMPLETE
        assert len(session.tier1_patches) == 1
        assert session.working_files["play.yml"] == patched_content.encode()

        assert play_file.read_text() == patched_content

    def test_approval_reconciliation_keeps_unspliced_findings_unresolved(self, tmp_path: Path) -> None:
        """Approved edits that produce no patch are not reconciled as fixed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.daemon.engine_server import EngineServicer, _reconcile_after_approval
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        node_id = "play.yml/plays[0]/tasks[0]"
        baseline = "- name: Install\n  apt:\n    name: nginx\n"
        patched = "- name: Install\n  ansible.builtin.apt:\n    name: nginx\n"

        graph = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path=node_id, node_type=NodeType.TASK),
            file_path="play.yml",
            yaml_lines=baseline,
        )
        node.line_start = 1
        node.line_end = 3
        graph.add_node(node)
        node.record_state(0, "scanned")
        graph.register_violations([{"rule_id": "L001", "message": "Use FQCN", "file": "play.yml", "path": node_id}], 0)
        node.yaml_lines = patched
        node.record_state(1, "transformed", source="deterministic")
        assert graph.resolve_violations(node_id, set(), fixed_by="deterministic", pass_number=1, status="proposed")

        session = SessionState(session_id="approval-unspliced", temp_dir=tmp_path)
        session.content_graph = graph
        # No original content for play.yml: splice_with_outcome cannot produce a
        # patch, so the approved edit never reaches the source bytes.
        session.graph_originals = {}
        session.proposals = {
            "t1-0000": Proposal(
                id="t1-0000",
                file="play.yml",
                path=node_id,
                rule_id="L001",
                tier=1,
                source="deterministic",
            )
        }

        servicer = EngineServicer()
        applied, patches = servicer._session_apply_approved(session, {"t1-0000"}, finalize=False)

        assert applied == 1
        assert list(patches or []) == []
        assert session.report is not None
        # ApprovalAck surfaces ``session.report.fixed`` — a skipped edit must
        # not be counted there.
        assert session.report.fixed == 0
        assert session.report.fixed_violations == []
        assert [v.rule_id for v in session.report.remaining_violations] == ["L001"]

        # The ledger must agree: a finding whose bytes never changed cannot
        # stay ``fixed``, or the Gate 2 report rebuild would re-report it as
        # resolved from the ledger alone.
        assert graph.query_violations(status="fixed") == []
        assert [v["rule_id"] for v in graph.query_violations(status="open")] == ["L001"]

        # A later rebuild with no new approvals keeps it unresolved.
        _reconcile_after_approval(session, graph, set())
        assert session.report.fixed == 0
        assert [v.rule_id for v in session.report.remaining_violations] == ["L001"]

    @pytest.mark.parametrize("produces_patch", [False, True])  # type: ignore[untyped-decorator]
    async def test_skipped_splice_does_not_report_findings_fixed(self, tmp_path: Path, produces_patch: bool) -> None:
        """Only findings whose edits reached the bytes count as fixed.

        A file listed in ``SpliceOutcome.unpatched`` that produced no patch
        still holds its original source, so its findings stay unresolved and
        are marked ``edit_skipped``. An approximate-baseline file (also in
        ``unpatched``) did produce a patch, so its findings stay fixed — the
        truncation is reported separately.

        Args:
            tmp_path: Pytest temporary directory fixture.
            produces_patch: Whether the skipped-class file still produced a
                best-effort patch (approximate-baseline case).
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.graph_engine import FilePatch as EngineFilePatch
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        yaml_content = "- name: Install\n  apt:\n    name: nginx\n    state: present\n"
        patched_content = "- name: Install\n  ansible.builtin.apt:\n    name: nginx\n    state: present\n"
        play_file = tmp_path / "play.yml"
        play_file.write_text(yaml_content)

        node_id = "play.yml/plays[0]/tasks[0]"
        graph = ContentGraph()
        graph.add_node(
            ContentNode(
                identity=NodeIdentity(path=node_id, node_type=NodeType.TASK),
                file_path=str(play_file),
                yaml_lines=yaml_content,
            )
        )

        session = SessionState(session_id="test-graph-skipped")
        session.working_files = {"play.yml": yaml_content.encode()}
        session.original_files = dict(session.working_files)

        mock_report = GraphFixReport(
            passes=1,
            fixed=1,
            nodes_modified=1,
            fixed_violations=[
                {"rule_id": "L001", "message": "Use FQCN", "file": "play.yml", "line": 2, "path": node_id}
            ],
        )
        patches = (
            [
                EngineFilePatch(
                    path=str(play_file),
                    original=yaml_content,
                    patched=patched_content,
                    diff="",
                    rule_ids=["L001"],
                )
            ]
            if produces_patch
            else []
        )

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                # The file is in ``unpatched`` either way; only the patch
                # decides whether its edits reached the source.
                return_value=SpliceOutcome(patches=patches, unpatched=[str(play_file)]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.daemon.engine_server._refresh_session_file_violations", new_callable=AsyncMock),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=mock_report)

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-graph-skipped",
                registry=MagicMock(),
                scan_fn=async_scan_fn,
                captured_graph=[graph],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        report = next(e for e in events if e.HasField("tier1_complete")).tier1_complete.report
        assert report is not None
        if produces_patch:
            assert report.fixed == 1
            assert [v.rule_id for v in report.fixed_violations] == ["L001"]
            assert report.remaining_violations == []
        else:
            assert report.fixed == 0
            assert report.fixed_violations == []
            assert [v.rule_id for v in report.remaining_violations] == ["L001"]

    async def test_no_violations_yields_clean_report(self, tmp_path: Path) -> None:
        """No violations → no patches, clean report with passes=1, fixed=0.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: OK\n  ansible.builtin.debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-clean")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=[], unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=GraphFixReport(passes=1, fixed=0))

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-clean",
                registry=MagicMock(),
                scan_fn=async_scan_fn,
                captured_graph=[ContentGraph()],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        assert session.status == 3
        assert session.tier1_patches == []
        assert session.report is not None
        assert session.report.passes == 1
        assert session.report.fixed == 0

    async def test_fallback_on_missing_graph(self, tmp_path: Path) -> None:
        """When captured_graph has None, falls back to empty ContentGraph.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-none")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_none(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=[], unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=GraphFixReport(passes=1, fixed=0))

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-none",
                registry=MagicMock(),
                scan_fn=async_scan_fn_none,
                captured_graph=[None],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        assert session.status == 3
        MockGRE.assert_called_once()

    async def test_remaining_violations_from_graph(self, tmp_path: Path) -> None:
        """Remaining violations come from graph_report, not a final scan.

        All remaining violations are stored in session.remaining_ai.
        Classification counts come from count_by_remediation_class.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-part")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        remaining: list[ViolationDict] = [
            {"rule_id": "L042", "message": "Complex fix needed", "file": "play.yml", "line": 1},
            {"rule_id": "L099", "message": "Manual review needed", "file": "play.yml", "line": 1},
        ]

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_part(_paths: list[str]) -> list[ViolationDict]:
            return remaining

        mock_report = GraphFixReport(
            passes=1,
            fixed=0,
            remaining_violations=remaining,
        )

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=[], unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=mock_report)

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-part",
                registry=MagicMock(),
                scan_fn=async_scan_fn_part,
                captured_graph=[ContentGraph()],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        assert session.remaining_ai == remaining
        assert session.remaining_manual == []
        assert session.report is not None
        assert session.report.remaining_ai == 2
        assert session.report.remaining_manual == 0

    def test_reconcile_after_approval_preserves_dependency_health_violations(self) -> None:
        """Post-approval reconciliation keeps dependency-health findings.

        These violations are not stored in the graph ledger, so they must be
        carried in session state across the approval boundary.
        """
        from apme_engine.daemon.engine_server import _reconcile_after_approval
        from apme_engine.engine.models import RemediationClass
        from apme_engine.graph.content_graph import ContentGraph

        session = SessionState(session_id="dep-health-preserve")
        session.dep_health_violations = [
            {
                "rule_id": "P001",
                "message": "Certified collection health issue",
                "file": "",
                "source": "collection_health",
                "remediation_class": RemediationClass.MANUAL_REVIEW,
            }
        ]
        session.report = FixReport(passes=1, fixed=0, remaining_manual=1)

        _reconcile_after_approval(session, ContentGraph(), set())

        assert session.remaining_ai == []
        assert len(session.remaining_manual) == 1
        assert session.remaining_manual[0]["source"] == "collection_health"
        assert session.report is not None
        assert session.report.remaining_ai == 0
        assert session.report.remaining_manual == 1

    async def test_skips_tier2_ai(self, tmp_path: Path) -> None:
        """Graph path goes directly to COMPLETE — no Tier 2 proposals.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-no-t2")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_no_t2(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=[], unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=GraphFixReport(passes=1, fixed=0))

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-no-t2",
                registry=MagicMock(),
                scan_fn=async_scan_fn_no_t2,
                captured_graph=[ContentGraph()],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        event_types = [e.WhichOneof("event") for e in events]
        assert "proposals" not in event_types
        assert "result" in event_types
        assert session.status == 3  # COMPLETE


class TestSessionRescanBridge:
    """Tests for the validator bridge (rescan_fn) wired into _session_graph_remediate.

    PR 5: Verifies that _session_graph_remediate constructs a bridge
    closure and passes it to GraphRemediationEngine as rescan_fn.
    """

    @pytest.mark.parametrize("argspec_rule", ["L058", "L059"])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("coverage_state", ["incomplete", "complete", "missing", "malformed"])  # type: ignore[untyped-decorator]
    async def test_bridge_passed_to_graph_engine_as_rescan_fn(
        self, tmp_path: Path, argspec_rule: str, coverage_state: str
    ) -> None:
        """Bridge closure is constructed and passed into GraphRemediationEngine as rescan_fn.

        Args:
            tmp_path: Pytest temporary directory fixture.
            argspec_rule: Rule whose severity override must also apply on rescan.
            coverage_state: Independent coverage metadata from the Ansible RPC.
        """
        from unittest.mock import MagicMock

        from apme.v1.common_pb2 import SEVERITY_HIGH, ValidatorDiagnostics
        from apme.v1.engine_pb2 import RuleConfig
        from apme_engine.argspec_coverage import ARGSPEC_COVERAGE_KEY, ARGSPEC_RULE_IDS, encode_argspec_coverage
        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.graph_engine import FilePatch as EngineFilePatch
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        yaml_content = "- name: Install\n  apt:\n    name: nginx\n    state: present\n"
        patched_content = "- name: Install\n  ansible.builtin.apt:\n    name: nginx\n    state: present\n"
        play_file = tmp_path / "play.yml"
        play_file.write_text(yaml_content)

        graph = ContentGraph()
        graph.add_node(
            ContentNode(
                identity=NodeIdentity(path="play.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
                file_path=str(play_file),
                yaml_lines=yaml_content,
            )
        )

        session = SessionState(session_id="test-bridge")
        session.working_files = {"play.yml": yaml_content.encode()}
        session.original_files = dict(session.working_files)

        scan_call_count = [0]

        async def scan_fn(paths: list[str]) -> list[ViolationDict]:
            scan_call_count[0] += 1
            if scan_call_count[0] == 1:
                return [{"rule_id": "L001", "message": "Use FQCN", "file": "play.yml", "line": 2, "source": "native"}]
            return []

        mock_report = GraphFixReport(passes=1, fixed=1, nodes_modified=1, fixed_violations=[])
        mock_patches = [
            EngineFilePatch(
                path=str(play_file),
                original=yaml_content,
                patched=patched_content,
                diff="",
                rule_ids=["L001"],
            )
        ]

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        captured_rescan_fn: list[object] = [None]

        def capture_gre_init(*args: object, **kwargs: object) -> MagicMock:
            captured_rescan_fn[0] = kwargs.get("rescan_fn")
            mock_engine = MagicMock()
            mock_engine.remediate = AsyncMock(return_value=mock_report)
            return mock_engine

        configs: list[object] = [
            RuleConfig(rule_id=argspec_rule, enabled=True, severity=SEVERITY_HIGH),
            RuleConfig(rule_id="L001", enabled=False),
        ]
        if argspec_rule == "L059":
            configs.append(RuleConfig(rule_id="L058", enabled=False))

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", return_value=([], [])),
            patch(
                "apme_engine.remediation.graph_engine.GraphRemediationEngine",
                side_effect=capture_gre_init,
            ),
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=mock_patches, unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch(
                "apme_engine.daemon.engine_server._refresh_session_file_violations", new_callable=AsyncMock
            ) as refresh,
        ):
            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-bridge-1",
                registry=MagicMock(),
                scan_fn=scan_fn,
                captured_graph=[graph],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
                rule_configs=configs,
            ):
                events.append(event)

        refresh.assert_awaited_once_with(session)
        assert captured_rescan_fn[0] is not None, "rescan_fn must be passed to engine"
        assert callable(captured_rescan_fn[0])

        # Rescans retain configuration and local plugins from the same upload.
        import json
        import threading

        from apme_engine.daemon.engine_server import _ValidatorResult
        from apme_engine.engine import graph_opa_payload
        from apme_engine.graph.content_graph import EdgeType

        play = ContentNode(
            identity=NodeIdentity(path="play.yml/plays[0]", node_type=NodeType.PLAY),
            options={"module_defaults": {"ansible.builtin.file": {"mode": "0644"}}},
        )
        second = ContentNode(
            identity=NodeIdentity(path="play.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            module="ansible.builtin.file",
            module_options={"path": "/tmp/second"},
        )
        graph.add_node(play)
        graph.add_node(second)
        graph.add_edge(play.node_id, "play.yml/plays[0]/tasks[0]", EdgeType.CONTAINS)
        graph.add_edge(play.node_id, second.node_id, EdgeType.CONTAINS)
        event_loop_thread = threading.get_ident()
        serialization_threads: list[int] = []
        original_context_builder = graph_opa_payload._build_module_defaults_contexts

        def record_context_builder(g: ContentGraph) -> object:
            serialization_threads.append(threading.get_ident())
            return original_context_builder(g)

        session.venv_path = "/session/venv"
        session.working_files.update(
            {
                "ansible.cfg": b"[defaults]\nlibrary = ./library\n",
                "library/apt.py": b"# local\n",
                "docs/large-unrelated.bin": b"x" * (50 * 1024 * 1024 + 1),
            }
        )
        dirty_ids = frozenset({"play.yml/plays[0]/tasks[0]", second.node_id})
        coverage = {"play.yml/plays[0]/tasks[0]": ARGSPEC_RULE_IDS} if coverage_state == "incomplete" else {}
        diagnostics = None
        if coverage_state != "missing":
            diagnostics = ValidatorDiagnostics(
                metadata={
                    ARGSPEC_COVERAGE_KEY: "[]" if coverage_state == "malformed" else encode_argspec_coverage(coverage)
                }
            )
        disabled_findings: list[ViolationDict] = []
        if argspec_rule == "L059":
            disabled_findings.append({"rule_id": "L058", "severity": "warning", "path": "play.yml/plays[0]/tasks[0]"})
        with (
            patch.dict(
                os.environ,
                {"NATIVE_GRPC_ADDRESS": "native", "OPA_GRPC_ADDRESS": "opa", "ANSIBLE_GRPC_ADDRESS": "ansible"},
            ),
            patch("apme_engine.daemon.engine_server._call_validator", new_callable=AsyncMock) as call_validator,
            patch.object(graph_opa_payload, "_build_module_defaults_contexts", side_effect=record_context_builder),
        ):
            call_validator.return_value = _ValidatorResult(
                violations=[
                    {
                        "rule_id": argspec_rule,
                        "severity": "error",
                        "path": "play.yml/plays[0]/tasks[0]",
                        "message": "Missing required parameter 'path' for ansible.builtin.file",
                    },
                    {"rule_id": "L001", "severity": "error", "path": "play.yml/plays[0]/tasks[0]"},
                    *disabled_findings,
                ],
                diagnostics=diagnostics,
            )
            rescanned = await captured_rescan_fn[0](graph, dirty_ids)
        assert rescanned.violations
        assert {finding["rule_id"] for finding in rescanned.violations} == {argspec_rule}
        assert all(finding["severity"] == "high" for finding in rescanned.violations)
        expected = {"*": ARGSPEC_RULE_IDS} if coverage_state in {"missing", "malformed"} else coverage
        assert rescanned.unassessed == expected
        assert len(serialization_threads) == 1
        assert serialization_threads[0] != event_loop_thread
        request = next(call.args[1] for call in call_validator.call_args_list if call.args[0] == "ansible")
        task_payload = json.loads(request.hierarchy_payload)["hierarchy"][0]["nodes"]
        assert {node["key"] for node in task_payload} == dirty_ids
        assert all(node["module_defaults_contexts"] == [[play.options["module_defaults"]]] for node in task_payload)
        assert {file.path: file.content for file in request.files} == {
            "ansible.cfg": b"[defaults]\nlibrary = ./library\n",
            "library/apt.py": b"# local\n",
        }
        assert request.ByteSize() < 50 * 1024 * 1024
        assert set(request.dirty_node_ids) == dirty_ids
        ansible_call = next(call for call in call_validator.call_args_list if call.args[0] == "ansible")
        assert ansible_call.kwargs["stream_large_request"] is True
        native_request = next(call.args[1] for call in call_validator.call_args_list if call.args[0] == "native")
        opa_request = next(call.args[1] for call in call_validator.call_args_list if call.args[0] == "opa")
        assert native_request.content_graph_data and not native_request.hierarchy_payload
        assert opa_request.hierarchy_payload and not opa_request.content_graph_data and not opa_request.files

        if coverage_state == "complete" and argspec_rule == "L058":
            from apme_engine.daemon.engine_server import GraphTransportError

            for backend in ("native", "gitleaks"):

                async def overflow(
                    address: str, request: object, *, _backend: str = backend, **kwargs: object
                ) -> _ValidatorResult:
                    """Fail one rescan transport without inventing clean coverage.

                    Args:
                        address: Validator endpoint label.
                        request: Complete rescan input.
                        _backend: Endpoint whose transport limit is exceeded.
                        **kwargs: Per-validator transport settings.

                    Returns:
                        An empty response from unaffected validators.

                    Raises:
                        GraphTransportError: The selected request cannot be sent.
                    """
                    if address == _backend:
                        raise GraphTransportError("Complete request exceeds transport; validation is incomplete")
                    return _ValidatorResult()

                with (
                    patch.dict(
                        os.environ,
                        {
                            "NATIVE_GRPC_ADDRESS": "native",
                            "OPA_GRPC_ADDRESS": "opa",
                            "ANSIBLE_GRPC_ADDRESS": "ansible",
                            "GITLEAKS_GRPC_ADDRESS": "gitleaks",
                        },
                    ),
                    patch("apme_engine.daemon.engine_server._call_validator", side_effect=overflow),
                    pytest.raises(GraphTransportError, match="validation is incomplete"),
                ):
                    await captured_rescan_fn[0](graph, dirty_ids)

    async def test_bridge_uses_correct_rules_dir(self, tmp_path: Path) -> None:
        """load_graph_rules is called with the native rules directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.engine_server import EngineServicer
        from apme_engine.graph.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = EngineServicer.__new__(EngineServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: OK\n  ansible.builtin.debug:\n    msg: hi\n")

        session = SessionState(session_id="test-rules-dir")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        captured_rules_dir: list[str | None] = [None]

        def mock_load_graph_rules(rules_dir: str = "", **kwargs: object) -> tuple[list[object], list[str]]:
            captured_rules_dir[0] = rules_dir
            return [], []

        async def async_scan_fn_rules(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.graph.scanner.load_graph_rules", side_effect=mock_load_graph_rules),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch(
                "apme_engine.remediation.graph_engine.splice_with_outcome",
                return_value=SpliceOutcome(patches=[], unpatched=[]),
            ),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=GraphFixReport())

            events: list[SessionEvent] = []
            async for event in servicer._session_graph_remediate(
                session=session,
                scan_id="scan-rules-dir",
                registry=MagicMock(),
                scan_fn=async_scan_fn_rules,
                captured_graph=[ContentGraph()],
                yaml_paths=[str(play_file)],
                temp_dir=tmp_path,
                max_passes=5,
                progress_queue=progress_queue,
                progress_callback=lambda *a: None,
                _heartbeat=_noop_heartbeat,
                format_content=_noop_format,
                format_diffs=[],
            ):
                events.append(event)

        rules_dir = captured_rules_dir[0]
        assert rules_dir is not None, "load_graph_rules must be called with rules_dir"
        assert rules_dir.endswith("graph/rules"), f"Expected graph rules dir, got: {captured_rules_dir[0]}"


# ---------------------------------------------------------------------------
# Helpers for graph remediation tests
# ---------------------------------------------------------------------------


async def _noop_heartbeat() -> None:
    """Heartbeat coroutine that sleeps indefinitely (cancelled by test)."""
    await asyncio.sleep(3600)


def _noop_format(text: str, **kwargs: object) -> object:
    """Format callback that reports no changes.

    Args:
        text: Content to format (ignored).
        **kwargs: Extra keyword arguments (ignored).

    Returns:
        Object with ``changed=False``.
    """
    from types import SimpleNamespace

    return SimpleNamespace(changed=False)


# ---------------------------------------------------------------------------
# Part 3: FixSession RPC integration tests
# ---------------------------------------------------------------------------


async def _mock_session_process_complete(
    self: object,
    session: SessionState,
    scan_id: str,
) -> AsyncIterator[SessionEvent]:
    """Mock _session_process that completes immediately with no changes.

    Args:
        self: Servicer instance (unused, required by patch.object).
        session: Session state to update.
        scan_id: Scan identifier (unused in this mock).

    Yields:
        SessionEvent: Tier1 summary then final result.
    """
    session.status = 3
    session.report = FixReport(passes=1, fixed=0)
    yield SessionEvent(
        tier1_complete=Tier1Summary(idempotency_ok=True, report=FixReport(passes=1)),
    )
    yield SessionEvent(
        result=SessionResult(patches=[], report=FixReport(passes=1)),
    )


async def _mock_session_process_with_proposals(
    self: object,
    session: SessionState,
    scan_id: str,
) -> AsyncIterator[SessionEvent]:
    """Mock _session_process that yields tier 1 then proposals for approval.

    Args:
        self: Servicer instance (unused, required by patch.object).
        session: Session state to update.
        scan_id: Scan identifier (unused in this mock).

    Yields:
        SessionEvent: Tier1 summary then proposals ready for approval.
    """
    p = Proposal(
        id="t2-0000",
        file="test.yml",
        rule_id="L001",
        before_text="old",
        after_text="new",
        explanation="Replace old with new",
    )
    session.proposals = {"t2-0000": p}
    session.original_files.setdefault("test.yml", b"old content")
    session.working_files.setdefault("test.yml", b"old content")
    session.status = 1  # AWAITING_APPROVAL
    session.report = FixReport(passes=1, fixed=0, remaining_ai=1)

    yield SessionEvent(
        tier1_complete=Tier1Summary(idempotency_ok=True, report=session.report),
    )
    yield SessionEvent(
        proposals=ProposalsReady(proposals=[p], tier=2, status=1),
    )


class TestFixSessionRPC:
    """Integration tests for FixSession RPC on the servicer."""

    async def test_venv_guard_exhaustion_maps_to_unavailable(self) -> None:
        """Exhausted lock contention is retryable and distinct from worker failure."""
        from apme_engine.daemon.engine_server import EngineServicer, _VenvWriteGuardTimeout

        async def _guard_busy(
            self: EngineServicer,
            session: SessionState,
            scan_id: str,
        ) -> AsyncIterator[SessionEvent]:
            del self, session, scan_id
            raise _VenvWriteGuardTimeout("busy")
            yield SessionEvent()  # pragma: no cover

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(SessionCommand(upload=ScanChunk(scan_id="guard-busy", last=True)))
        with (
            patch.object(EngineServicer, "_session_process", _guard_busy),
            pytest.raises(_AbortSignal) as exc_info,
        ):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert exc_info.value.code.name == "UNAVAILABLE"
        assert "retry the scan" in exc_info.value.details

    async def test_venv_read_guard_timeout_maps_to_unavailable(self) -> None:
        """Read-guard contention is surfaced as a retryable gRPC status."""
        from apme_engine.daemon.engine_server import EngineServicer, _VenvReadGuardTimeout

        async def _guard_busy(
            self: EngineServicer,
            session: SessionState,
            scan_id: str,
        ) -> AsyncIterator[SessionEvent]:
            del self, session, scan_id
            raise _VenvReadGuardTimeout("read guard busy")
            yield SessionEvent()  # pragma: no cover

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(SessionCommand(upload=ScanChunk(scan_id="read-guard-busy", last=True)))
        with (
            patch.object(EngineServicer, "_session_process", _guard_busy),
            pytest.raises(_AbortSignal) as exc_info,
        ):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert exc_info.value.code.name == "UNAVAILABLE"
        assert "retry the scan" in exc_info.value.details

    async def test_session_created_on_first_upload(self) -> None:
        """First upload yields SessionCreated with ID and positive TTL."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-1", last=True),
            )
        )

        created = None
        with patch.object(
            EngineServicer,
            "_session_process",
            _mock_session_process_complete,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                oneof = event.WhichOneof("event")
                if oneof == "created" and created is None:
                    created = event.created
                elif oneof == "result":
                    stream.send(SessionCommand(close=CloseRequest()))
                elif oneof == "closed":
                    break

        assert created is not None
        assert len(created.session_id) == 12
        assert created.ttl_seconds > 0

    async def test_close_yields_closed_event(self) -> None:
        """Close command cleanly ends the stream."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-close", last=True),
            )
        )

        last_event = None
        with patch.object(
            EngineServicer,
            "_session_process",
            _mock_session_process_complete,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                last_event = event
                oneof = event.WhichOneof("event")
                if oneof in ("tier1_complete", "result"):
                    stream.send(SessionCommand(close=CloseRequest()))
                elif oneof == "closed":
                    break

        assert last_event is not None
        assert last_event.WhichOneof("event") == "closed"

    async def test_extend_refreshes_session_ttl(self) -> None:
        """Extend command responds with SessionCreated carrying refreshed TTL."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-ext", last=True),
            )
        )

        created_count = 0
        extend_sent = False
        with patch.object(
            EngineServicer,
            "_session_process",
            _mock_session_process_complete,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                oneof = event.WhichOneof("event")
                if oneof == "created":
                    created_count += 1
                    if created_count == 2:
                        assert event.created.ttl_seconds > 0
                        stream.send(SessionCommand(close=CloseRequest()))
                elif oneof == "tier1_complete" and not extend_sent or oneof == "result" and not extend_sent:
                    stream.send(SessionCommand(extend=ExtendRequest()))
                    extend_sent = True
                elif oneof == "closed":
                    break

        assert created_count >= 2

    async def test_resume_existing_session(self) -> None:
        """Resuming an active session replays its state."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()

        session = store.create()
        session.tier1_patches = [
            FilePatch(path="x.yml", original=b"o", patched=b"p"),
        ]
        session.report = FixReport(passes=1, fixed=1)
        session.status = 3
        session.original_files = {"x.yml": b"o"}
        session.working_files = {"x.yml": b"p"}

        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(
            SessionCommand(
                resume=ResumeRequest(session_id=session.session_id),
            )
        )

        events = []
        async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
            events.append(event)
            oneof = event.WhichOneof("event")
            if oneof == "result":
                stream.send(SessionCommand(close=CloseRequest()))
            elif oneof == "closed":
                break

        types = [e.WhichOneof("event") for e in events]
        assert "created" in types
        assert "tier1_complete" in types
        assert "result" in types

    async def test_resume_nonexistent_aborts(self) -> None:
        """Resuming an unknown session aborts with NOT_FOUND."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                resume=ResumeRequest(session_id="does-not-exist"),
            )
        )

        with pytest.raises(_AbortSignal):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert ctx.aborted

    async def test_approval_flow_end_to_end(self) -> None:
        """Upload → proposals → approve → result → close."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-approval", last=True),
            )
        )

        events: list[SessionEvent] = []
        with (
            patch.object(EngineServicer, "_session_process", _mock_session_process_with_proposals),
            patch(
                "apme_engine.daemon.engine_server._refresh_session_file_violations", new_callable=AsyncMock
            ) as refresh,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                events.append(event)
                oneof = event.WhichOneof("event")
                if oneof == "proposals":
                    ids = [p.id for p in event.proposals.proposals]
                    stream.send(
                        SessionCommand(
                            approve=ApprovalRequest(approved_ids=ids),
                        )
                    )
                elif oneof == "result":
                    stream.send(SessionCommand(close=CloseRequest()))
                elif oneof == "closed":
                    break

        refresh.assert_awaited_once()
        types = [e.WhichOneof("event") for e in events]
        assert "created" in types
        assert "tier1_complete" in types
        assert "proposals" in types
        assert "approval_ack" in types
        assert "result" in types
        assert "closed" in types

    async def test_max_sessions_returns_resource_exhausted(self) -> None:
        """Exceeding max sessions raises RESOURCE_EXHAUSTED."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()

        for _ in range(_MAX_SESSIONS):
            store.create()

        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="over-limit", last=True),
            )
        )

        with pytest.raises(_AbortSignal):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

    async def test_resume_stream_end_preserves_session(self) -> None:
        """Disconnecting a resumed stream without close keeps the session alive."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()

        session = store.create()
        session.report = FixReport(passes=1, fixed=0)
        session.status = 1
        session.proposals = {
            "t2-0000": Proposal(id="t2-0000", file="test.yml", rule_id="L001"),
        }

        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(
            SessionCommand(
                resume=ResumeRequest(session_id=session.session_id),
            )
        )

        async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
            if event.WhichOneof("event") == "proposals":
                stream.close()
                break

        assert store.get(session.session_id) is session

    async def test_upload_stream_end_preserves_session(self) -> None:
        """Disconnecting an upload stream without close keeps the session for resume."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()
        before = store.count

        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-persist", last=True),
            )
        )

        session_id = None
        with patch.object(
            EngineServicer,
            "_session_process",
            _mock_session_process_with_proposals,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                oneof = event.WhichOneof("event")
                if oneof == "created" and session_id is None:
                    session_id = event.created.session_id
                elif oneof == "proposals":
                    stream.close()
                    break

        assert session_id is not None
        assert store.count == before + 1
        assert store.get(session_id) is not None

    @pytest.mark.parametrize(
        "phase,preserve",
        [  # type: ignore[untyped-decorator]
            ("upload", False),
            ("processing", False),
            ("approval", True),
            ("review", True),
            ("complete", True),
            ("triage", True),
        ],
    )
    async def test_client_cancel_removes_created_session(self, phase: str, preserve: bool) -> None:
        """Client cancellation discards incomplete work and retains resumable phases.

        Args:
            phase: Session phase active when the client disconnects.
            preserve: Whether the session has a durable or owned resumable phase.
        """
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()
        before = store.count
        created_session_id: str | None = None

        class _CancellingStream:
            """Yield one upload, then raise CancelledError like a dead client."""

            def __init__(self) -> None:
                """Initialize with the first command not yet delivered."""
                self._sent_first = False

            def __aiter__(self) -> _CancellingStream:
                """Return self as async iterator.

                Returns:
                    Self.
                """
                return self

            async def __anext__(self) -> SessionCommand:
                """Deliver the upload once, then simulate client cancel.

                Returns:
                    The initial upload command.

                Raises:
                    asyncio.CancelledError: On every call after the first.
                """
                if not self._sent_first:
                    self._sent_first = True
                    return SessionCommand(
                        upload=ScanChunk(scan_id="test-cancel", last=False),
                    )
                assert created_session_id is not None
                created = store.get(created_session_id)
                assert created is not None
                created.upload_sealed = phase != "upload"
                created.approval_in_flight = phase == "approval"
                created.status = {"review": 1, "complete": 3, "triage": 4}.get(phase, 2)
                raise asyncio.CancelledError

        ctx = FakeGrpcContext()
        with (
            patch.object(
                EngineServicer,
                "_session_process",
                _mock_session_process_complete,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            async for event in servicer.FixSession(_CancellingStream(), ctx):  # type: ignore[arg-type]
                if event.HasField("created"):
                    created_session_id = event.created.session_id

        try:
            assert store.count == before + int(preserve)
            assert created_session_id is not None
            assert (store.get(created_session_id) is not None) == preserve
        finally:
            if created_session_id is not None:
                store.remove(created_session_id)

    async def test_cancel_after_upload_then_resume_removes_upload_session(self) -> None:
        """Cancellation removes the upload-created session, not a later resume target."""
        from apme_engine.daemon.engine_server import EngineServicer

        servicer = EngineServicer()
        store = servicer._get_session_store()
        resume_session = store.create()
        resume_session.report = FixReport(passes=1, fixed=0)

        upload_created_id: str | None = None

        class _UploadThenResumeCancelStream:
            """Upload into one session, resume another, then cancel."""

            def __init__(self) -> None:
                self._step = 0

            def __aiter__(self) -> _UploadThenResumeCancelStream:
                return self

            async def __anext__(self) -> SessionCommand:
                if self._step == 0:
                    self._step = 1
                    return SessionCommand(
                        upload=ScanChunk(scan_id="upload-then-resume", last=False),
                    )
                if self._step == 1:
                    self._step = 2
                    return SessionCommand(
                        resume=ResumeRequest(session_id=resume_session.session_id),
                    )
                raise asyncio.CancelledError

        ctx = FakeGrpcContext()
        with pytest.raises(asyncio.CancelledError):
            async for event in servicer.FixSession(
                _UploadThenResumeCancelStream(),
                ctx,  # type: ignore[arg-type]
            ):
                if event.WhichOneof("event") == "created" and upload_created_id is None:
                    upload_created_id = event.created.session_id

        assert upload_created_id is not None
        assert store.get(upload_created_id) is None
        assert store.get(resume_session.session_id) is resume_session


async def test_concurrent_replays_share_pending_file_reassessment() -> None:
    """Concurrent resumes run a pending AI continuation only once."""
    from apme_engine.daemon.engine_server import EngineServicer

    session = SessionState(session_id="concurrent-reassessment", status=2)
    session.pending_file_reassessment = "ai"
    entered = asyncio.Event()
    release = asyncio.Event()
    refresh = AsyncMock()
    transitions = 0

    async def reassess(_session: SessionState) -> None:
        entered.set()
        await release.wait()

    async def enter_ai(_self: EngineServicer, current: SessionState) -> AsyncIterator[SessionEvent]:
        nonlocal transitions
        transitions += 1
        current.status = 4
        current.awaiting_ai_triage = True
        yield SessionEvent(progress=ProgressUpdate(message="AI gate"))

    refresh.side_effect = reassess
    servicer = EngineServicer()

    async def replay() -> list[SessionEvent]:
        return [event async for event in servicer._session_replay_state(session)]

    with (
        patch("apme_engine.daemon.engine_server._refresh_session_file_violations", refresh),
        patch.object(EngineServicer, "_session_enter_ai_phase", enter_ai),
    ):
        first = asyncio.create_task(replay())
        await entered.wait()
        second = asyncio.create_task(replay())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
    refresh.assert_awaited_once_with(session)
    assert transitions == 1
    assert not session.pending_file_reassessment
