"""Tests for session management and FixSession bidirectional stream (ADR-028).

Part 1: SessionState and SessionStore unit tests (no gRPC, no server).
Part 2: FixSession helper method tests (async generators, no server).
Part 3: FixSession RPC integration tests (full servicer with mocked pipeline).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from apme.v1.primary_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FilePatch,
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
from apme_engine.daemon.session import (
    _DEFAULT_TTL,
    _MAX_LIFETIME,
    _MAX_SESSIONS,
    ResourceExhaustedError,
    SessionState,
    SessionStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AsyncCommandStream:
    """Async iterator backed by a queue for feeding commands to FixSession."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()

    def send(self, cmd: SessionCommand) -> None:
        self._queue.put_nowait(cmd)

    def close(self) -> None:
        self._queue.put_nowait(None)

    def __aiter__(self) -> AsyncCommandStream:
        return self

    async def __anext__(self) -> SessionCommand:
        cmd = await self._queue.get()
        if cmd is None:
            raise StopAsyncIteration
        return cmd


class FakeGrpcContext:
    """Minimal stub for grpc.aio.ServicerContext in tests."""

    def __init__(self) -> None:
        self._code: object = None
        self._details: str | None = None
        self.aborted: bool = False

    async def abort(self, code: object, details: str) -> None:
        self._code = code
        self._details = details
        self.aborted = True
        raise _AbortSignal(code, details)

    def set_code(self, code: object) -> None:
        self._code = code

    def set_details(self, details: str) -> None:
        self._details = details


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


# ---------------------------------------------------------------------------
# Part 1: SessionState unit tests
# ---------------------------------------------------------------------------


class TestSessionState:
    """Pure unit tests for SessionState dataclass properties."""

    def test_initial_state(self) -> None:
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
        state = SessionState(session_id="abc")
        assert 0 < state.ttl_seconds <= _DEFAULT_TTL

    def test_not_expired_when_fresh(self) -> None:
        state = SessionState(session_id="abc")
        assert state.expired is False

    def test_not_expiring_soon_when_fresh(self) -> None:
        state = SessionState(session_id="abc")
        assert state.expiring_soon is False

    def test_expired_after_idle_timeout(self) -> None:
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert state.expired is True

    def test_expired_after_max_lifetime(self) -> None:
        state = SessionState(session_id="abc")
        state.created_at = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_LIFETIME + 1,
        )
        assert state.expired is True

    def test_expiring_soon_within_warning_window(self) -> None:
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL - 200,
        )
        assert state.expiring_soon is True

    def test_touch_resets_idle_timer(self) -> None:
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        old_ttl = state.ttl_seconds
        state.touch()
        assert state.ttl_seconds > old_ttl

    def test_lifetime_seconds_near_zero_on_create(self) -> None:
        state = SessionState(session_id="abc")
        assert state.lifetime_seconds < 5

    def test_cleanup_removes_temp_dir(self, tmp_path: Path) -> None:
        state = SessionState(session_id="abc")
        temp = tmp_path / "session_temp"
        temp.mkdir()
        (temp / "file.yml").write_text("---\n")
        state.temp_dir = temp

        state.cleanup()
        assert not temp.exists()
        assert state.temp_dir is None

    def test_cleanup_noop_without_temp_dir(self) -> None:
        state = SessionState(session_id="abc")
        state.cleanup()
        assert state.temp_dir is None


# ---------------------------------------------------------------------------
# Part 1b: SessionStore unit tests
# ---------------------------------------------------------------------------


class TestSessionStore:
    """Tests for SessionStore CRUD and capacity limits."""

    def test_create_returns_unique_session(self) -> None:
        store = SessionStore()
        s1 = store.create()
        s2 = store.create()
        assert s1.session_id != s2.session_id
        assert store.count == 2

    def test_get_returns_existing_session(self) -> None:
        store = SessionStore()
        s = store.create()
        assert store.get(s.session_id) is s

    def test_get_returns_none_for_unknown_id(self) -> None:
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_get_auto_removes_expired_session(self) -> None:
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert store.get(s.session_id) is None
        assert store.count == 0

    def test_touch_refreshes_activity(self) -> None:
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=100)
        store.touch(s.session_id)
        assert s.ttl_seconds > _DEFAULT_TTL - 5

    def test_remove_returns_true(self) -> None:
        store = SessionStore()
        s = store.create()
        assert store.remove(s.session_id) is True
        assert store.count == 0

    def test_remove_unknown_returns_false(self) -> None:
        store = SessionStore()
        assert store.remove("nope") is False

    def test_max_sessions_raises(self) -> None:
        store = SessionStore()
        for _ in range(_MAX_SESSIONS):
            store.create()
        with pytest.raises(ResourceExhaustedError, match="Maximum"):
            store.create()

    def test_remove_frees_slot_for_new_session(self) -> None:
        store = SessionStore()
        sessions = [store.create() for _ in range(_MAX_SESSIONS)]
        store.remove(sessions[0].session_id)
        store.create()
        assert store.count == _MAX_SESSIONS

    def test_remove_cleans_up_temp_dir(self, tmp_path: Path) -> None:
        store = SessionStore()
        s = store.create()
        temp = tmp_path / "sess_tmp"
        temp.mkdir()
        s.temp_dir = temp
        store.remove(s.session_id)
        assert not temp.exists()


class TestSessionStoreReaper:
    """Async tests for the background reaper task."""

    async def test_reaper_collects_expired_sessions(self) -> None:
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL + 10,
        )

        expired = [
            sid for sid, st in store._sessions.items() if st.expired
        ]
        for sid in expired:
            store._remove(sid)

        assert store.count == 0

    async def test_reaper_preserves_active_sessions(self) -> None:
        store = SessionStore()
        store.create().touch()

        expired = [
            sid for sid, st in store._sessions.items() if st.expired
        ]
        for sid in expired:
            store._remove(sid)

        assert store.count == 1

    async def test_start_and_stop_reaper(self) -> None:
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


class TestBuildProposals:
    """Tests for _build_proposals_from_remaining."""

    def test_converts_violations_to_proposals(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        violations = [
            {"file": "a.yml", "rule_id": "L001", "line": 5, "description": "Missing name"},
            {"file": "b.yml", "rule_id": "L002", "line": [10, 15], "description": "FQCN"},
        ]
        proposals = PrimaryServicer._build_proposals_from_remaining(violations, tier=2)  # type: ignore[arg-type]

        assert len(proposals) == 2
        assert proposals[0].id == "t2-0000"
        assert proposals[0].file == "a.yml"
        assert proposals[0].line_start == 5
        assert proposals[0].line_end == 5
        assert proposals[0].tier == 2
        assert proposals[1].line_start == 10
        assert proposals[1].line_end == 15

    def test_missing_line_defaults_to_zero(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        proposals = PrimaryServicer._build_proposals_from_remaining(
            [{"file": "x.yml", "rule_id": "L003"}], tier=3,
        )
        assert proposals[0].line_start == 0
        assert proposals[0].line_end == 0
        assert proposals[0].tier == 3


class TestSessionApplyApproved:
    """Tests for _session_apply_approved."""

    def test_full_approval_sets_complete(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "t2-0000": Proposal(
                id="t2-0000", file="t.yml", rule_id="L001",
                before_text="old", after_text="new",
            ),
        }
        session.status = 1
        session.working_files = {"t.yml": b"old content"}

        applied = PrimaryServicer._session_apply_approved(session, {"t2-0000"})
        assert applied == 1
        assert session.status == 3  # COMPLETE
        assert session.proposals == {}

    def test_partial_approval_stays_awaiting(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "p1": Proposal(id="p1", file="a.yml", rule_id="L001",
                           before_text="old1", after_text="new1"),
            "p2": Proposal(id="p2", file="b.yml", rule_id="L002",
                           before_text="old2", after_text="new2"),
        }
        session.status = 1
        session.working_files = {"a.yml": b"old1", "b.yml": b"old2"}

        applied = PrimaryServicer._session_apply_approved(session, {"p1"})
        assert applied == 1
        assert session.status == 1  # still awaiting
        assert "p2" in session.proposals

    def test_approval_modifies_working_files(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "p1": Proposal(
                id="p1", file="test.yml", rule_id="L001",
                before_text="hello", after_text="goodbye",
            ),
        }
        session.status = 1
        session.working_files = {"test.yml": b"hello world"}

        PrimaryServicer._session_apply_approved(session, {"p1"})
        assert session.working_files["test.yml"] == b"goodbye world"


class TestSessionBuildResult:
    """Tests for _session_build_result async generator."""

    async def test_includes_only_changed_files(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        session = SessionState(session_id="test")
        session.original_files = {"f.yml": b"line1\nline2\n"}
        session.working_files = {"f.yml": b"line1\nchanged\n"}
        session.report = FixReport()

        events = [e async for e in servicer._session_build_result(session)]
        diff = events[0].result.patches[0].diff
        assert "---" in diff and "+++" in diff
        assert "-line2" in diff and "+changed" in diff


class TestSessionReplayState:
    """Tests for _session_replay_state (session resume)."""

    async def test_replays_tier1_summary(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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

    async def test_replays_result_when_complete(self) -> None:
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        session = SessionState(session_id="test")
        session.original_files = {"a.yml": b"orig"}
        session.working_files = {"a.yml": b"patched"}
        session.report = FixReport(passes=1, fixed=1)
        session.status = 3

        events = [e async for e in servicer._session_replay_state(session)]
        types = [e.WhichOneof("event") for e in events]
        assert "result" in types


# ---------------------------------------------------------------------------
# Part 3: FixSession RPC integration tests
# ---------------------------------------------------------------------------


async def _mock_session_process_complete(
    self: object, session: SessionState, scan_id: str,
) -> AsyncIterator[SessionEvent]:
    """Mock _session_process that completes immediately with no changes.

    Args:
        self: Servicer instance (unused).
        session: Session state to populate.
        scan_id: Scan identifier (unused).

    Yields:
        SessionEvent: Messages for tier1 completion and result.
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
    self: object, session: SessionState, scan_id: str,
) -> AsyncIterator[SessionEvent]:
    """Mock _session_process that yields Tier 1 then proposals for approval.

    Args:
        self: Servicer instance (unused).
        session: Session state to populate with proposals.
        scan_id: Scan identifier (unused).

    Yields:
        SessionEvent: Messages for tier1 completion and proposals.
    """
    p = Proposal(
        id="t2-0000", file="test.yml", rule_id="L001",
        before_text="old", after_text="new",
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
    """Integration tests calling FixSession directly on the servicer."""

    async def test_session_created_on_first_upload(self) -> None:
        """First upload chunk yields SessionCreated with ID and TTL."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(SessionCommand(
            upload=ScanChunk(scan_id="test-1", last=True),
        ))

        created = None
        with patch.object(
            PrimaryServicer, "_session_process", _mock_session_process_complete,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(SessionCommand(
            upload=ScanChunk(scan_id="test-close", last=True),
        ))

        last_event = None
        with patch.object(
            PrimaryServicer, "_session_process", _mock_session_process_complete,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(SessionCommand(
            upload=ScanChunk(scan_id="test-ext", last=True),
        ))

        created_count = 0
        extend_sent = False
        with patch.object(
            PrimaryServicer, "_session_process", _mock_session_process_complete,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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
        stream.send(SessionCommand(
            resume=ResumeRequest(session_id=session.session_id),
        ))

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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(SessionCommand(
            resume=ResumeRequest(session_id="does-not-exist"),
        ))

        with pytest.raises(_AbortSignal):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert ctx.aborted

    async def test_approval_flow_end_to_end(self) -> None:
        """Upload → proposals → approve → result → close."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(SessionCommand(
            upload=ScanChunk(scan_id="test-approval", last=True),
        ))

        events: list[SessionEvent] = []
        with patch.object(
            PrimaryServicer,
            "_session_process",
            _mock_session_process_with_proposals,
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                events.append(event)
                oneof = event.WhichOneof("event")
                if oneof == "proposals":
                    ids = [p.id for p in event.proposals.proposals]
                    stream.send(SessionCommand(
                        approve=ApprovalRequest(approved_ids=ids),
                    ))
                elif oneof == "result":
                    stream.send(SessionCommand(close=CloseRequest()))
                elif oneof == "closed":
                    break

        types = [e.WhichOneof("event") for e in events]
        assert "created" in types
        assert "tier1_complete" in types
        assert "proposals" in types
        assert "approval_ack" in types
        assert "result" in types
        assert "closed" in types

    async def test_max_sessions_returns_resource_exhausted(self) -> None:
        """Exceeding max sessions raises RESOURCE_EXHAUSTED."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        store = servicer._get_session_store()

        for _ in range(_MAX_SESSIONS):
            store.create()

        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()
        stream.send(SessionCommand(
            upload=ScanChunk(scan_id="over-limit", last=True),
        ))

        with pytest.raises(Exception):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass
