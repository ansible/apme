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
from unittest.mock import AsyncMock, patch

import pytest

from apme.v1.common_pb2 import ProgressUpdate
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
from apme_engine.engine.models import ViolationDict

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


# ---------------------------------------------------------------------------
# Part 1: SessionState unit tests
# ---------------------------------------------------------------------------


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
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert state.expired is True

    def test_expired_after_max_lifetime(self) -> None:
        """Session expires after max lifetime is exceeded."""
        state = SessionState(session_id="abc")
        state.created_at = datetime.now(timezone.utc) - timedelta(
            seconds=_MAX_LIFETIME + 1,
        )
        assert state.expired is True

    def test_expiring_soon_within_warning_window(self) -> None:
        """Low remaining TTL marks session as expiring soon."""
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL - 200,
        )
        assert state.expiring_soon is True

    def test_touch_resets_idle_timer(self) -> None:
        """touch() refreshes idle activity and increases remaining TTL."""
        state = SessionState(session_id="abc")
        state.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        old_ttl = state.ttl_seconds
        state.touch()
        assert state.ttl_seconds > old_ttl

    def test_lifetime_seconds_near_zero_on_create(self) -> None:
        """lifetime_seconds is near zero immediately after creation."""
        state = SessionState(session_id="abc")
        assert state.lifetime_seconds < 5

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
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(
            seconds=_DEFAULT_TTL + 1,
        )
        assert store.get(s.session_id) is None
        assert store.count == 0

    def test_touch_refreshes_activity(self) -> None:
        """touch() updates last activity so TTL recovers."""
        store = SessionStore()
        s = store.create()
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=100)
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
        s.last_activity_at = datetime.now(timezone.utc) - timedelta(
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


class TestBuildProposals:
    """Unit tests for _build_proposals_from_ai."""

    def test_converts_ai_proposals_to_protos(self) -> None:
        """AIProposal with snippets become Proposal protos."""
        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.remediation.ai_provider import AIProposal

        ai_proposals = [
            AIProposal(
                file="a.yml",
                original_snippet="line2\n",
                fixed_snippet="fixed2\n",
                diff="@@ -2 +2 @@\n-line2\n+fixed2",
                rule_ids=["L001"],
                confidence=0.95,
                explanation="Fixed line 2",
            ),
        ]
        proposals = PrimaryServicer._build_proposals_from_ai(ai_proposals)

        assert len(proposals) == 1
        assert proposals[0].id == "t2-0000"
        assert proposals[0].file == "a.yml"
        assert proposals[0].rule_id == "L001"
        assert proposals[0].before_text == "line2\n"
        assert proposals[0].after_text == "fixed2\n"
        assert proposals[0].confidence == pytest.approx(0.95)
        assert proposals[0].tier == 2


class TestSessionApplyApproved:
    """Unit tests for _session_apply_approved."""

    def test_full_approval_sets_complete(self) -> None:
        """Approving all proposals marks session complete and clears proposals."""
        from apme_engine.daemon.primary_server import PrimaryServicer

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

        applied = PrimaryServicer._session_apply_approved(session, {"t2-0000"})
        assert applied == 1
        assert session.status == 3  # COMPLETE
        assert session.proposals == {}

    def test_partial_approval_completes_session(self) -> None:
        """Partial approval completes the session; unapproved proposals remain listed."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        session = SessionState(session_id="test")
        session.proposals = {
            "p1": Proposal(id="p1", file="a.yml", rule_id="L001", before_text="old1", after_text="new1"),
            "p2": Proposal(id="p2", file="b.yml", rule_id="L002", before_text="old2", after_text="new2"),
        }
        session.status = 1
        session.working_files = {"a.yml": b"old1", "b.yml": b"old2"}

        applied = PrimaryServicer._session_apply_approved(session, {"p1"})
        assert applied == 1
        assert session.status == 3  # COMPLETE after approval processing
        assert "p2" in session.proposals

    def test_approval_modifies_working_files(self) -> None:
        """Approved proposal replaces before_text with after_text in working_files."""
        from apme_engine.daemon.primary_server import PrimaryServicer

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

        PrimaryServicer._session_apply_approved(session, {"p1"})
        assert session.working_files["test.yml"] == b"goodbye world"


class TestSessionBuildResult:
    """Unit tests for _session_build_result async generator."""

    async def test_includes_only_changed_files(self) -> None:
        """Result patches list only files whose content changed."""
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
        """Patch diff uses unified diff markers and line changes."""
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
    """Unit tests for _session_replay_state (session resume)."""

    async def test_replays_tier1_summary(self) -> None:
        """Replay emits tier1_complete when tier1 patches exist."""
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
        """Replay emits tier1_complete and proposals when awaiting approval."""
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
        """Replay emits final result when session status is complete."""
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


class TestSessionNodeIndexWiring:
    """Verify _session_process wires NodeIndex from hierarchy payload."""

    async def test_scan_fn_sets_node_index_on_engine(self) -> None:
        """scan_fn captures hierarchy_payload and calls engine.set_node_index().

        Mocks _scan_pipeline to return a hierarchy payload and verifies that
        RemediationEngine.set_node_index is called with a populated NodeIndex.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.node_index import NodeIndex
        from apme_engine.remediation.engine import RemediationEngine

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        hierarchy_payload: dict[str, object] = {
            "hierarchy": [
                {
                    "nodes": [
                        {"key": "task0", "type": "taskcall", "file": "play.yml", "line": [1, 3]},
                        {"key": "task1", "type": "taskcall", "file": "play.yml", "line": [5, 7]},
                    ],
                },
            ],
        }

        async def fake_scan_pipeline(
            temp_dir: object,
            files: object,
            scan_id: object,
            **kwargs: object,
        ) -> tuple[list[object], None, str, list[object], dict[str, object], None, list[str], set[str], set[str], None]:
            return [], None, "sid", [], hierarchy_payload, None, [], set(), set(), None

        servicer._scan_pipeline = fake_scan_pipeline  # type: ignore[assignment]

        session = SessionState(session_id="test-ni")
        session.working_files = {"play.yml": b"- name: test\n  debug:\n    msg: hi\n"}
        session.original_files = dict(session.working_files)
        session.fix_options = MagicMock()
        session.fix_options.ansible_core_version = ""
        session.fix_options.collection_specs = []
        session.fix_options.session_id = ""
        session.fix_options.max_passes = 1
        session.fix_options.enable_ai = False
        session.fix_options.ai_model = ""
        session.scan_options = None

        captured_node_index: list[NodeIndex | None] = [None]

        def fake_remediate(engine_self: RemediationEngine, file_paths: list[str], apply: bool = True) -> MagicMock:
            engine_self._scan_fn(file_paths)
            return MagicMock(applied_patches=[], ai_proposed=[], remaining=[])

        with (
            patch.object(PrimaryServicer, "_format_files", return_value=[]),
            patch.object(
                RemediationEngine,
                "remediate",
                fake_remediate,
            ),
            patch.object(
                RemediationEngine,
                "set_node_index",
                side_effect=lambda ni: captured_node_index.__setitem__(0, ni),
            ) as mock_set_ni,
        ):
            async for _ in servicer._session_process(session, "scan-1"):
                pass

            assert mock_set_ni.called, "set_node_index was never called"
            ni = captured_node_index[0]
            assert ni is not None
            assert len(ni) == 2
            assert ni.get("task0") is not None
            assert ni.get("task1") is not None


class TestSessionProgressStreaming:
    """Verify _session_process yields progress events during remediation."""

    async def test_progress_events_during_remediation(self) -> None:
        """Progress events from the engine drain loop appear in the stream.

        Mocks _scan_pipeline and RemediationEngine.remediate so the
        convergence loop produces progress callbacks which should be
        yielded as SessionEvent(progress=...) through the drain loop.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.remediation.engine import RemediationEngine

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        async def fake_scan_pipeline(
            temp_dir: object,
            files: object,
            scan_id: object,
            **kwargs: object,
        ) -> tuple[list[object], None, str, list[object], None, None, list[str], set[str], set[str], None]:
            return [], None, "sid", [], None, None, [], set(), set(), None

        servicer._scan_pipeline = fake_scan_pipeline  # type: ignore[assignment]

        session = SessionState(session_id="test-prog")
        session.working_files = {"play.yml": b"- name: test\n  debug:\n    msg: hi\n"}
        session.original_files = dict(session.working_files)
        session.fix_options = MagicMock()
        session.fix_options.ansible_core_version = ""
        session.fix_options.collection_specs = []
        session.fix_options.session_id = ""
        session.fix_options.max_passes = 1
        session.fix_options.enable_ai = False
        session.fix_options.ai_model = ""
        session.scan_options = None

        def fake_remediate(
            engine_self: RemediationEngine,
            file_paths: list[str],
            **kwargs: object,
        ) -> MagicMock:
            engine_self._progress("tier1", "Pass 1/1: scanning...", 0.0)
            engine_self._progress("tier1", "Pass 1: 3 fixable violations", 0.0)
            engine_self._progress("tier1", "Pass 1: 3 transforms applied", 0.0)
            engine_self._progress("tier1", "Converged at pass 1 (0 fixable)", 1.0)
            return MagicMock(
                applied_patches=[],
                ai_proposed=[],
                remaining_ai=[],
                remaining_manual=[],
                passes=1,
                fixed=3,
                oscillation_detected=False,
            )

        with (
            patch.object(PrimaryServicer, "_format_files", return_value=[]),
            patch.object(RemediationEngine, "remediate", fake_remediate),
        ):
            events: list[SessionEvent] = []
            async for event in servicer._session_process(session, "scan-1"):
                events.append(event)

        progress_events = [e for e in events if e.HasField("progress")]
        progress_msgs = [e.progress.message for e in progress_events]

        assert any("Pass 1" in m for m in progress_msgs), (
            f"Expected 'Pass 1' progress from drain loop, got: {progress_msgs}"
        )
        assert any("scanning" in m for m in progress_msgs), f"Expected 'scanning' progress, got: {progress_msgs}"
        assert any("transforms applied" in m for m in progress_msgs), (
            f"Expected 'transforms applied' progress, got: {progress_msgs}"
        )


class TestSessionGraphRemediate:
    """Tests for _session_graph_remediate (graph engine remediation path, PR 4).

    Exercises the ``APME_USE_GRAPH_ENGINE=1`` code path that runs
    ``GraphRemediationEngine`` in-memory, splices results to disk,
    and performs a final full-pipeline scan.
    """

    async def test_happy_path_produces_patches_and_events(self, tmp_path: Path) -> None:
        """Graph engine fixes violations, splices files, and emits correct events.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.engine import FilePatch as EngineFilePatch
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

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
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=mock_patches),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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

        assert call_count[0] == 2, f"scan_fn should be called twice (initial + final), got {call_count[0]}"

        # Verify the validator bridge (rescan_fn) was wired into the engine
        _, gre_kwargs = MockGRE.call_args
        assert "rescan_fn" in gre_kwargs, "rescan_fn must be passed to GraphRemediationEngine"
        assert callable(gre_kwargs["rescan_fn"])

        event_types = [e.WhichOneof("event") for e in events]
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

    async def test_no_violations_yields_clean_report(self, tmp_path: Path) -> None:
        """No violations → no patches, clean report with passes=1, fixed=0.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: OK\n  ansible.builtin.debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-clean")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=[]),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-none")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_none(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=[]),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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

    async def test_remaining_violations_partitioned(self, tmp_path: Path) -> None:
        """Remaining violations from final scan are split into AI and manual.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-part")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        ai_violations: list[ViolationDict] = [
            {"rule_id": "L042", "message": "Complex fix needed", "file": "play.yml", "line": 1},
        ]
        manual_violations: list[ViolationDict] = [
            {"rule_id": "L099", "message": "Manual review needed", "file": "play.yml", "line": 1},
        ]

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_part(_paths: list[str]) -> list[ViolationDict]:
            return ai_violations + manual_violations

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=[]),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch(
                "apme_engine.remediation.partition.partition_violations",
                return_value=([], ai_violations, manual_violations),
            ),
        ):
            MockGRE.return_value.remediate = AsyncMock(return_value=GraphFixReport(passes=1, fixed=0))

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

        assert session.remaining_ai == ai_violations
        assert session.remaining_manual == manual_violations
        assert session.report is not None
        assert session.report.remaining_ai == 1
        assert session.report.remaining_manual == 1

    async def test_skips_tier2_ai(self, tmp_path: Path) -> None:
        """Graph path goes directly to COMPLETE — no Tier 2 proposals.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: Test\n  debug:\n    msg: hi\n")

        session = SessionState(session_id="test-graph-no-t2")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        async def async_scan_fn_no_t2(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=[]),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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

    async def test_bridge_passed_to_graph_engine_as_rescan_fn(self, tmp_path: Path) -> None:
        """Bridge closure is constructed and passed into GraphRemediationEngine as rescan_fn.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
        from apme_engine.remediation.engine import FilePatch as EngineFilePatch
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

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

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", return_value=[]),
            patch(
                "apme_engine.remediation.graph_engine.GraphRemediationEngine",
                side_effect=capture_gre_init,
            ),
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=mock_patches),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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
            ):
                events.append(event)

        assert captured_rescan_fn[0] is not None, "rescan_fn must be passed to engine"
        assert callable(captured_rescan_fn[0])

    async def test_bridge_uses_correct_rules_dir(self, tmp_path: Path) -> None:
        """load_graph_rules is called with the native rules directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from unittest.mock import MagicMock

        from apme_engine.daemon.primary_server import PrimaryServicer
        from apme_engine.engine.content_graph import ContentGraph
        from apme_engine.remediation.graph_engine import GraphFixReport

        servicer = PrimaryServicer.__new__(PrimaryServicer)

        play_file = tmp_path / "play.yml"
        play_file.write_text("- name: OK\n  ansible.builtin.debug:\n    msg: hi\n")

        session = SessionState(session_id="test-rules-dir")
        session.working_files = {"play.yml": play_file.read_bytes()}
        session.original_files = dict(session.working_files)

        progress_queue: asyncio.Queue[ProgressUpdate | None] = asyncio.Queue()

        captured_rules_dir: list[str | None] = [None]

        def mock_load_graph_rules(rules_dir: str = "", **kwargs: object) -> list[object]:
            captured_rules_dir[0] = rules_dir
            return []

        async def async_scan_fn_rules(_paths: list[str]) -> list[ViolationDict]:
            return []

        with (
            patch("apme_engine.engine.graph_scanner.load_graph_rules", side_effect=mock_load_graph_rules),
            patch("apme_engine.remediation.graph_engine.GraphRemediationEngine") as MockGRE,
            patch("apme_engine.remediation.graph_engine.splice_modifications", return_value=[]),
            patch("apme_engine.remediation.partition.add_classification_to_violations"),
            patch("apme_engine.remediation.partition.partition_violations", return_value=([], [], [])),
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
        assert rules_dir.endswith("native/rules"), f"Expected native rules dir, got: {captured_rules_dir[0]}"


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

    async def test_session_created_on_first_upload(self) -> None:
        """First upload yields SessionCreated with ID and positive TTL."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-1", last=True),
            )
        )

        created = None
        with patch.object(
            PrimaryServicer,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-close", last=True),
            )
        )

        last_event = None
        with patch.object(
            PrimaryServicer,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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
            PrimaryServicer,
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
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
        from apme_engine.daemon.primary_server import PrimaryServicer

        servicer = PrimaryServicer()
        stream = AsyncCommandStream()
        ctx = FakeGrpcContext()

        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="test-approval", last=True),
            )
        )

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
                    stream.send(
                        SessionCommand(
                            approve=ApprovalRequest(approved_ids=ids),
                        )
                    )
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
        stream.send(
            SessionCommand(
                upload=ScanChunk(scan_id="over-limit", last=True),
            )
        )

        with pytest.raises(_AbortSignal):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass
