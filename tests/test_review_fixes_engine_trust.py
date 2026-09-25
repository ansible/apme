"""Trust-boundary tests for the engine daemon (review fixes #1, #10, #14-#17, #21, #26).

Covers upload path normalization, session upload caps, FixSession replay/unseal/drop
semantics, health fan-out bounds, hierarchy decode failures, rule-catalog audit, and
real diagnostics aggregation through the scan pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn
from unittest.mock import MagicMock, patch

import grpc
import httpx
import pytest

import apme_engine.daemon.engine_server as engine_server
from apme.v1 import validate_pb2, validate_pb2_grpc
from apme.v1.common_pb2 import File, HealthRequest, ServiceHealth, ValidatorDiagnostics
from apme.v1.engine_pb2 import CloseRequest, RuleConfig, ScanChunk, SessionCommand, SessionEvent
from apme_engine.daemon.engine_server import (
    EngineServicer,
    _apply_rule_configs,
    _normalize_upload_path,
    _validate_rule_configs,
    _ValidatorResult,
)
from apme_engine.daemon.session import SessionState
from apme_engine.daemon.validator_errors import PUBLIC_VALIDATOR_ERROR, RULE_VALIDATOR_FAILURE
from apme_engine.engine.models import ViolationDict

_session_upload_append = EngineServicer._session_upload_append


def _upload_chunk(files: dict[str, bytes], *, last: bool = False, scan_id: str = "s") -> ScanChunk:
    """Build an upload ScanChunk from a path-to-bytes mapping.

    Args:
        files: File paths mapped to content bytes.
        last: Whether this is the terminal chunk.
        scan_id: Scan identifier for the chunk.

    Returns:
        ScanChunk proto with the given files.
    """
    return ScanChunk(
        scan_id=scan_id,
        files=[File(path=path, content=content) for path, content in files.items()],
        last=last,
    )


class _AbortSignal(Exception):
    """Raised by _FakeGrpcContext.abort to unwind the servicer under test."""

    def __init__(self, code: object, details: str) -> None:
        """Record the abort code and details.

        Args:
            code: gRPC status code.
            details: Error details string.
        """
        super().__init__(f"{code}: {details}")
        self.code = code
        self.details = details


class _FakeGrpcContext:
    """Minimal stub for grpc.aio.ServicerContext in tests."""

    def __init__(self) -> None:
        """Initialize with no abort state."""
        self.code: object = None
        self.details: str | None = None
        self.aborted: bool = False

    async def abort(self, code: object, details: str) -> NoReturn:
        """Record abort and raise to exit the servicer under test.

        Args:
            code: gRPC status code.
            details: Error details string.

        Raises:
            _AbortSignal: Always, to unwind the test call stack.
        """
        self.code = code
        self.details = details
        self.aborted = True
        raise _AbortSignal(code, details)

    def set_code(self, code: object) -> None:
        """Set the recorded status code.

        Args:
            code: gRPC status code.
        """
        self.code = code

    def set_details(self, details: str) -> None:
        """Set the recorded error details.

        Args:
            details: Error details string.
        """
        self.details = details

    def peer(self) -> str:
        """Return a fake peer address.

        Returns:
            Fake peer identifier string.
        """
        return "ipv4:127.0.0.1:50051"


class _AsyncCommandStream:
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

    def __aiter__(self) -> _AsyncCommandStream:
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


class TestNormalizeUploadPath:
    """Tests for _normalize_upload_path canonicalization and escape rejection (#1)."""

    def test_backslash_and_leading_slash(self) -> None:
        """Backslashes become slashes and leading slashes are stripped."""
        cases = {
            "a\\b\\c.yml": "a/b/c.yml",
            "/a/b.yml": "a/b.yml",
            "///a/b.yml": "a/b.yml",
            "\\a\\b.yml": "a/b.yml",
        }
        for raw, expected in cases.items():
            assert _normalize_upload_path(raw) == expected

    def test_dot_segments_collapse(self) -> None:
        """Dot segments collapse so ./a and a are the same key."""
        assert _normalize_upload_path("./a.yml") == "a.yml"
        assert _normalize_upload_path("a/./b.yml") == "a/b.yml"
        assert _normalize_upload_path("a//b.yml") == "a/b.yml"
        assert _normalize_upload_path("a/x/../b.yml") == "a/b.yml"
        assert _normalize_upload_path("a.yml") == "a.yml"

    def test_dot_forms_share_one_key(self) -> None:
        """./a.yml and a.yml normalize identically (enables last-wins dedup)."""
        assert _normalize_upload_path("./a.yml") == _normalize_upload_path("a.yml")

    def test_empty_and_escape_paths_raise(self) -> None:
        """Empty, dot-only, and root-escaping paths raise ValueError."""
        for raw in ["", ".", "./", "..", "../evil.yml", "a/../../evil.yml", "/../evil.yml", "a/.."]:
            with pytest.raises(ValueError, match="escapes session root or is empty"):
                _normalize_upload_path(raw)


class TestSessionUploadAppend:
    """Tests for _session_upload_append caps and dedup semantics (#1)."""

    def test_dedup_last_wins_single_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One chunk repeating a path (a.yml + ./a.yml) stores one entry, last wins.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_SESSION_MAX_UPLOAD_BYTES", 2)
        session = SessionState(session_id="dedup-1")
        chunk = ScanChunk(
            scan_id="s",
            files=[File(path="a.yml", content=b"v1"), File(path="./a.yml", content=b"v2")],
            last=False,
        )
        _session_upload_append(session, chunk)
        assert list(session.original_files) == ["a.yml"]
        assert session.original_files["a.yml"] == b"v2"
        assert session.working_files["a.yml"] == b"v2"

    def test_replace_byte_math(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replaced bytes are subtracted: 5B replaced by 7B plus 2B new == 9B total.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        session = SessionState(session_id="repl-1")
        session.original_files["a.yml"] = b"12345"
        monkeypatch.setattr(engine_server, "_SESSION_MAX_UPLOAD_BYTES", 8)
        with pytest.raises(ValueError, match="size limit exceeded"):
            _session_upload_append(session, _upload_chunk({"a.yml": b"1234567", "b.yml": b"12"}))
        monkeypatch.setattr(engine_server, "_SESSION_MAX_UPLOAD_BYTES", 9)
        session2 = SessionState(session_id="repl-2")
        session2.original_files["a.yml"] = b"12345"
        _session_upload_append(session2, _upload_chunk({"a.yml": b"1234567", "b.yml": b"12"}))
        assert session2.original_files["a.yml"] == b"1234567"

    def test_file_cap_raises_without_mutation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exceeding the file cap raises before mutating any session state.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_SESSION_MAX_UPLOAD_FILES", 1)
        session = SessionState(session_id="cap-1")
        with pytest.raises(ValueError, match="file limit exceeded"):
            _session_upload_append(session, _upload_chunk({"a.yml": b"1", "b.yml": b"2"}))
        assert session.original_files == {}
        assert session.working_files == {}

    def test_byte_cap_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exceeding the byte cap raises ValueError.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_SESSION_MAX_UPLOAD_BYTES", 10)
        session = SessionState(session_id="cap-2")
        with pytest.raises(ValueError, match="size limit exceeded"):
            _session_upload_append(session, _upload_chunk({"a.yml": b"x" * 11}))

    def test_sealed_rejects(self) -> None:
        """Appending to a sealed session raises ValueError."""
        session = SessionState(session_id="seal-1")
        session.upload_sealed = True
        with pytest.raises(ValueError, match="already processed"):
            _session_upload_append(session, _upload_chunk({"a.yml": b"1"}))

    def test_escape_path_raises(self) -> None:
        """Escaping paths are rejected by the shared normalizer."""
        session = SessionState(session_id="esc-1")
        with pytest.raises(ValueError, match="escapes session root"):
            _session_upload_append(session, _upload_chunk({"../evil.yml": b"x"}))


class TestFixSessionTrust:
    """FixSession replay / unseal / drop semantics (#10)."""

    async def test_duplicate_terminal_replays_without_rerun(self) -> None:
        """A duplicate terminal chunk replays state; process runs exactly once."""
        servicer = EngineServicer()
        stream = _AsyncCommandStream()
        ctx = _FakeGrpcContext()
        stream.send(SessionCommand(upload=_upload_chunk({"play.yml": b"orig: 1\n"}, last=True, scan_id="dup-1")))

        process_calls: list[str] = []
        replay_calls: list[str] = []
        seen: list[SessionState] = []
        real_append = EngineServicer._session_upload_append

        async def _noop_process(
            self: EngineServicer, session: SessionState, scan_id: str
        ) -> AsyncIterator[SessionEvent]:
            """Record the call and yield no events.

            Args:
                self: Servicer instance (unused, required by patch.object).
                session: Session state under test.
                scan_id: Scan identifier of the terminal chunk.

            Yields:
                SessionEvent: No events (empty stream).
            """
            process_calls.append(scan_id)
            empty: list[SessionEvent] = []
            for event in empty:
                yield event

        async def _noop_replay(self: EngineServicer, session: SessionState) -> AsyncIterator[SessionEvent]:
            """Record the replay and yield no events.

            Args:
                self: Servicer instance (unused, required by patch.object).
                session: Session state under test.

            Yields:
                SessionEvent: No events (empty stream).
            """
            replay_calls.append(session.session_id)
            empty: list[SessionEvent] = []
            for event in empty:
                yield event

        def _spy_append(session: SessionState, chunk: ScanChunk) -> None:
            """Record the session then delegate to the real append.

            Args:
                session: Session state under test.
                chunk: Upload chunk to append.
            """
            seen.append(session)
            real_append(session, chunk)

        fed = False
        with (
            patch.object(EngineServicer, "_session_process", _noop_process),
            patch.object(EngineServicer, "_session_replay_state", _noop_replay),
            patch.object(EngineServicer, "_session_upload_append", staticmethod(_spy_append)),
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                if event.WhichOneof("event") == "created" and not fed:
                    fed = True
                    stream.send(
                        SessionCommand(
                            upload=ScanChunk(
                                scan_id="dup-1",
                                files=[File(path="play.yml", content=b"CHANGED")],
                                last=True,
                            )
                        )
                    )
                    stream.send(SessionCommand(close=CloseRequest()))
                elif event.WhichOneof("event") == "closed":
                    break

        assert process_calls == ["dup-1"]
        assert len(replay_calls) == 1
        assert seen[0].original_files["play.yml"] == b"orig: 1\n"

    async def test_unseal_on_exception(self) -> None:
        """An exception in _session_process unseals the session for retry."""
        servicer = EngineServicer()
        stream = _AsyncCommandStream()
        ctx = _FakeGrpcContext()
        stream.send(SessionCommand(upload=_upload_chunk({"play.yml": b"x: 1\n"}, last=True, scan_id="unseal-1")))

        seen: list[SessionState] = []
        real_append = EngineServicer._session_upload_append

        async def _boom_process(
            self: EngineServicer, session: SessionState, scan_id: str
        ) -> AsyncIterator[SessionEvent]:
            """Yield nothing, then fail to exercise unseal-on-exception.

            Args:
                self: Servicer instance (unused, required by patch.object).
                session: Session state under test.
                scan_id: Scan identifier of the terminal chunk.

            Yields:
                SessionEvent: Single empty event before failing.

            Raises:
                RuntimeError: Always, to exercise unseal-on-exception.
            """
            yield SessionEvent()
            raise RuntimeError("boom")

        def _spy_append(session: SessionState, chunk: ScanChunk) -> None:
            """Record the session then delegate to the real append.

            Args:
                session: Session state under test.
                chunk: Upload chunk to append.
            """
            seen.append(session)
            real_append(session, chunk)

        with (
            patch.object(EngineServicer, "_session_process", _boom_process),
            patch.object(EngineServicer, "_session_upload_append", staticmethod(_spy_append)),
            pytest.raises(RuntimeError, match="boom"),
        ):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert seen[0].upload_sealed is False

    async def test_value_error_drops_created_session(self) -> None:
        """A ValueError (bad upload) removes the created session and aborts."""
        servicer = EngineServicer()
        store = servicer._get_session_store()
        removed: list[str] = []
        real_remove = store.remove

        def _spy_remove(session_id: str) -> bool:
            """Record the removal then delegate to the real remove.

            Args:
                session_id: Session identifier to remove.

            Returns:
                True if the session was removed.
            """
            removed.append(session_id)
            return real_remove(session_id)

        stream = _AsyncCommandStream()
        ctx = _FakeGrpcContext()
        stream.send(SessionCommand(upload=_upload_chunk({"../evil.yml": b"x"}, last=True, scan_id="evil-1")))

        with patch.object(store, "remove", _spy_remove), pytest.raises(_AbortSignal):
            async for _event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                pass

        assert ctx.aborted
        assert ctx.code == grpc.StatusCode.INVALID_ARGUMENT
        assert len(removed) == 1
        assert store.count == 0


class TestFixSessionCharacterization:
    """Characterization of current FixSession seal/replay/drop behavior (#14 evidence)."""

    async def test_characterize_seal_set_before_process_and_kept(self) -> None:
        """Seal is set before process runs and stays set after success."""
        servicer = EngineServicer()
        stream = _AsyncCommandStream()
        ctx = _FakeGrpcContext()
        stream.send(SessionCommand(upload=_upload_chunk({"play.yml": b"x: 1\n"}, last=True, scan_id="seal-1")))

        process_calls: list[str] = []
        seen: list[SessionState] = []
        real_append = EngineServicer._session_upload_append

        async def _noop_process(
            self: EngineServicer, session: SessionState, scan_id: str
        ) -> AsyncIterator[SessionEvent]:
            """Record the call and yield no events.

            Args:
                self: Servicer instance (unused, required by patch.object).
                session: Session state under test.
                scan_id: Scan identifier of the terminal chunk.

            Yields:
                SessionEvent: No events (empty stream).
            """
            process_calls.append(scan_id)
            empty: list[SessionEvent] = []
            for event in empty:
                yield event

        def _spy_append(session: SessionState, chunk: ScanChunk) -> None:
            """Record the session then delegate to the real append.

            Args:
                session: Session state under test.
                chunk: Upload chunk to append.
            """
            seen.append(session)
            real_append(session, chunk)

        fed = False
        with (
            patch.object(EngineServicer, "_session_process", _noop_process),
            patch.object(EngineServicer, "_session_upload_append", staticmethod(_spy_append)),
        ):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                if event.WhichOneof("event") == "created" and not fed:
                    fed = True
                    stream.send(SessionCommand(close=CloseRequest()))
                elif event.WhichOneof("event") == "closed":
                    break

        assert process_calls == ["seal-1"]
        assert seen[0].upload_sealed is True

    async def test_characterize_nonterminal_after_seal_aborts_and_drops(self) -> None:
        """Today: a non-terminal chunk after the seal aborts and drops the session."""
        servicer = EngineServicer()
        stream = _AsyncCommandStream()
        ctx = _FakeGrpcContext()
        stream.send(SessionCommand(upload=_upload_chunk({"play.yml": b"x: 1\n"}, last=True, scan_id="seal-2")))

        async def _noop_process(
            self: EngineServicer, session: SessionState, scan_id: str
        ) -> AsyncIterator[SessionEvent]:
            """Record nothing and yield no events.

            Args:
                self: Servicer instance (unused, required by patch.object).
                session: Session state under test.
                scan_id: Scan identifier of the terminal chunk.

            Yields:
                SessionEvent: No events (empty stream).
            """
            empty: list[SessionEvent] = []
            for event in empty:
                yield event

        fed = False
        with patch.object(EngineServicer, "_session_process", _noop_process), pytest.raises(_AbortSignal):
            async for event in servicer.FixSession(stream, ctx):  # type: ignore[arg-type]
                if event.WhichOneof("event") == "created" and not fed:
                    fed = True
                    stream.send(SessionCommand(upload=_upload_chunk({"late.yml": b"y: 2\n"})))

        assert ctx.aborted
        assert ctx.code == grpc.StatusCode.INVALID_ARGUMENT
        assert servicer._get_session_store().count == 0


class _TimeoutChannel:
    """Fake grpc.aio channel that never dials."""

    async def close(self, grace: object = None) -> None:
        """No-op close.

        Args:
            grace: Grace period (ignored).
        """


class _TimeoutValidatorStub:
    """Stub whose Health always times out."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Ignore channel args.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).
        """

    async def Health(self, *args: object, **kwargs: object) -> object:
        """Raise TimeoutError like a hung probe.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).

        Returns:
            Never returns.

        Raises:
            TimeoutError: Always, simulating a hung probe.
        """
        raise TimeoutError("hung")


class _RefusedValidatorStub(_TimeoutValidatorStub):
    """Stub whose Health fails fast with a connection error."""

    async def Health(self, *args: object, **kwargs: object) -> object:
        """Raise RuntimeError like a refused connection.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always, simulating a refused connection.
        """
        raise RuntimeError("refused")


class _InvalidBodyResponse:
    """200 response whose JSON body is invalid.

    Attributes:
        status_code: HTTP status code (always 200 in this stub).
    """

    status_code = 200

    def json(self) -> dict[str, object]:
        """Raise to simulate a non-JSON /health body.

        Returns:
            Never returns.

        Raises:
            ValueError: Always, simulating an invalid body.
        """
        raise ValueError("not json")


class _FakeGalaxyClient:
    """Minimal async stand-in for httpx.AsyncClient."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Ignore client args.

        Args:
            *args: Positional args (ignored).
            **kwargs: Keyword args (ignored).
        """

    async def __aenter__(self) -> _FakeGalaxyClient:
        """Return self as the client.

        Returns:
            Self.
        """
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """Exit without suppressing.

        Args:
            *exc: Exception info (ignored).

        Returns:
            False (never suppress).
        """
        return False

    async def get(self, url: str) -> _InvalidBodyResponse:
        """Return a 200 response with an invalid body.

        Args:
            url: Request URL (ignored).

        Returns:
            Invalid-body response.
        """
        return _InvalidBodyResponse()


class TestHealthFanOut:
    """Health aggregation: missing/degraded probes and the overall deadline (#17)."""

    async def test_required_missing_yields_unhealthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing required validators yield unhealthy; optional missing are skipped.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        for var in engine_server.VALIDATOR_ENV_VARS.values():
            monkeypatch.delenv(var, raising=False)

        async def _galaxy_ok(url: str) -> tuple[bool, ServiceHealth | None]:
            """Report Galaxy Proxy healthy.

            Args:
                url: Proxy URL (ignored).

            Returns:
                Healthy galaxy_proxy entry.
            """
            return (False, ServiceHealth(name="galaxy_proxy", status="ok", address=url or "stub"))

        monkeypatch.setattr(engine_server, "_probe_galaxy_proxy_health", _galaxy_ok)
        servicer = EngineServicer()
        resp = await servicer.Health(HealthRequest(), _FakeGrpcContext())  # type: ignore[arg-type]

        assert resp.status == "unhealthy"
        names = {entry.name for entry in resp.downstream}
        assert names == {"native", "opa", "ansible", "galaxy_proxy"}
        for entry in resp.downstream:
            if entry.name in ("native", "opa", "ansible"):
                assert "not configured" in entry.status

    async def test_per_probe_timeout_degrades(self) -> None:
        """A hung probe degrades to a timeout entry instead of raising."""
        with (
            patch("grpc.aio.insecure_channel", return_value=_TimeoutChannel()),
            patch.object(validate_pb2_grpc, "ValidatorStub", return_value=_TimeoutValidatorStub()),
        ):
            bad, entry = await engine_server._probe_validator_health("native", "localhost:59999")
        assert bad is True
        assert entry is not None
        assert entry.status == "error: health probe timed out"

    async def test_optional_probe_error_not_unhealthy(self) -> None:
        """An optional validator error degrades without marking unhealthy."""
        with (
            patch("grpc.aio.insecure_channel", return_value=_TimeoutChannel()),
            patch.object(validate_pb2_grpc, "ValidatorStub", return_value=_RefusedValidatorStub()),
        ):
            bad, entry = await engine_server._probe_validator_health("gitleaks", "localhost:59999")
        assert bad is False
        assert entry is not None
        assert "refused" in entry.status

    async def test_overall_deadline_harvests_wedged_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A probe hanging past the overall deadline becomes a timeout entry, bounded.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_HEALTH_OVERALL_TIMEOUT_S", 0.3)
        monkeypatch.setattr(engine_server, "_HEALTH_RECOVERY_GRACE_S", 0.2)

        async def _flaky_probe(name: str, addr: str) -> tuple[bool, ServiceHealth | None]:
            """Hang for opa, answer ok for everything else.

            Args:
                name: Validator name.
                addr: Validator address.

            Returns:
                Healthy entry, or never (opa hangs past the deadline).
            """
            if name == "opa":
                await asyncio.sleep(30.0)
                return (True, ServiceHealth(name=name, status="ok", address=addr))
            return (False, ServiceHealth(name=name, status="ok", address=addr))

        async def _galaxy_ok(url: str) -> tuple[bool, ServiceHealth | None]:
            """Report Galaxy Proxy healthy.

            Args:
                url: Proxy URL (ignored).

            Returns:
                Healthy galaxy_proxy entry.
            """
            return (False, ServiceHealth(name="galaxy_proxy", status="ok", address=url or "stub"))

        monkeypatch.setattr(engine_server, "_probe_validator_health", _flaky_probe)
        monkeypatch.setattr(engine_server, "_probe_galaxy_proxy_health", _galaxy_ok)
        for var in engine_server.VALIDATOR_ENV_VARS.values():
            monkeypatch.setenv(var, "localhost:59998")
        monkeypatch.setenv("APME_GALAXY_PROXY_URL", "http://proxy:9999")

        servicer = EngineServicer()
        t0 = time.monotonic()
        resp = await servicer.Health(HealthRequest(), _FakeGrpcContext())  # type: ignore[arg-type]
        elapsed = time.monotonic() - t0

        assert elapsed < 0.3 + 0.2 + 5.0
        assert resp.status == "unhealthy"
        by_name = {entry.name: entry for entry in resp.downstream}
        assert by_name["opa"].status == "error: health probe timed out"

    async def test_invalid_proxy_body_is_error(self) -> None:
        """A 200 /health with a non-ok body yields an error entry."""
        fake_httpx = SimpleNamespace(AsyncClient=_FakeGalaxyClient, TimeoutException=httpx.TimeoutException)
        with patch.object(engine_server, "httpx", fake_httpx):
            bad, entry = await engine_server._probe_galaxy_proxy_health("http://proxy:9999")
        assert bad is True
        assert entry is not None
        assert entry.status == "error: invalid /health body"

    async def test_unconfigured_proxy_is_error(self) -> None:
        """An empty proxy URL yields an error entry without any I/O."""
        bad, entry = await engine_server._probe_galaxy_proxy_health("")
        assert bad is True
        assert entry is not None
        assert "not configured" in entry.status


class TestHealthBudgetConstants:
    """Health timeout budget constants (#15)."""

    def test_recovery_grace_within_overall_budget(self) -> None:
        """Grace (2s) is smaller than the overall deadline; worst case ~= 14s."""
        assert engine_server._HEALTH_PER_PROBE_TIMEOUT_S == 5.0
        assert engine_server._HEALTH_OVERALL_TIMEOUT_S == 12.0
        assert engine_server._HEALTH_RECOVERY_GRACE_S == 2.0
        assert engine_server._HEALTH_RECOVERY_GRACE_S < engine_server._HEALTH_OVERALL_TIMEOUT_S
        worst = engine_server._HEALTH_OVERALL_TIMEOUT_S + engine_server._HEALTH_RECOVERY_GRACE_S
        assert worst == pytest.approx(14.0)


class TestHierarchyDecodeInfraError:
    """Malformed hierarchy payloads yield infra errors, not empty success (#10)."""

    async def test_opa_malformed_hierarchy_yields_infra_error(self) -> None:
        """OPA Validate with undecodable hierarchy returns R902 with request_id."""
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        request = validate_pb2.ValidateRequest(request_id="bad-hier-1", hierarchy_payload=b"{not valid json")
        resp = await OpaValidatorServicer().Validate(request, _FakeGrpcContext())  # type: ignore[arg-type]

        assert resp.request_id == "bad-hier-1"  # type: ignore[attr-defined]
        assert len(resp.violations) == 1  # type: ignore[attr-defined]
        assert resp.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]
        assert resp.violations[0].message == PUBLIC_VALIDATOR_ERROR  # type: ignore[attr-defined]
        assert not resp.HasField("diagnostics")  # type: ignore[attr-defined]

    async def test_ansible_malformed_hierarchy_yields_infra_error(self) -> None:
        """Ansible Validate with undecodable hierarchy returns R902 with request_id."""
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer

        request = validate_pb2.ValidateRequest(request_id="bad-hier-2", hierarchy_payload=b"\xff\xfe{bad")
        resp = await AnsibleValidatorServicer().Validate(request, _FakeGrpcContext())  # type: ignore[arg-type]

        assert resp.request_id == "bad-hier-2"  # type: ignore[attr-defined]
        assert len(resp.violations) == 1  # type: ignore[attr-defined]
        assert resp.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]
        assert resp.violations[0].message == PUBLIC_VALIDATOR_ERROR  # type: ignore[attr-defined]
        assert not resp.HasField("diagnostics")  # type: ignore[attr-defined]


class TestRuleCatalogAudit:
    """Bidirectional rule-catalog audit fail-closed behavior (#21)."""

    def test_empty_configs_complete_missing_is_sorted_known(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty configs with complete=True report every known ID missing, sorted.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_known_rule_ids", {"L002", "L001"})
        unknown, missing = _validate_rule_configs([], complete=True)
        assert unknown == []
        assert missing == ["L001", "L002"]

    def test_empty_catalog_complete_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty engine catalog with complete=True fails closed with a sentinel.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_known_rule_ids", set())
        unknown, missing = _validate_rule_configs([], complete=True)
        assert unknown == []
        assert missing == ["<engine-rule-catalog-empty>"]

    def test_empty_catalog_partial_skips_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty engine catalog with complete=False skips the audit (warns).

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_known_rule_ids", set())
        unknown, missing = _validate_rule_configs([], complete=False)
        assert (unknown, missing) == ([], [])

    def test_unknown_ids_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config IDs outside the known catalog are reported as unknown.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        monkeypatch.setattr(engine_server, "_known_rule_ids", {"L001"})
        configs: list[object] = [RuleConfig(rule_id="ZZ-999", enabled=True)]
        unknown, missing = _validate_rule_configs(configs, complete=False)
        assert unknown == ["ZZ-999"]
        assert missing == []

    def test_duplicate_normalized_warns_and_last_wins(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Duplicate normalized IDs warn; _apply_rule_configs resolves last-wins.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            caplog: Pytest log-capture fixture.
        """
        monkeypatch.setattr(engine_server, "_known_rule_ids", {"L001"})
        violations: list[ViolationDict] = [
            {"rule_id": "L001", "severity": "low", "message": "m", "file": "a.yml", "line": 1, "path": ""}
        ]
        with caplog.at_level(logging.WARNING, logger="apme.engine"):
            configs: list[object] = [
                RuleConfig(rule_id="native:L001", enabled=False),
                RuleConfig(rule_id="L001", enabled=True),
            ]
            unknown, missing = _validate_rule_configs(configs, complete=True)
            kept = _apply_rule_configs([dict(v) for v in violations], configs)
        assert unknown == []
        assert missing == []
        assert any("Duplicate normalized rule ID" in rec.message for rec in caplog.records)
        assert len(kept) == 1

        with caplog.at_level(logging.WARNING, logger="apme.engine"):
            reversed_configs: list[object] = [
                RuleConfig(rule_id="L001", enabled=True),
                RuleConfig(rule_id="native:L001", enabled=False),
            ]
            dropped = _apply_rule_configs([dict(v) for v in violations], reversed_configs)
        assert dropped == []


class TestDiagnosticsAggregation:
    """Real fan-out diagnostics aggregation through the scan pipeline (#26)."""

    async def test_scan_pipeline_merges_validator_diagnostics(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_execute_scan_pipeline merges stubbed validator diagnostics into ScanDiagnostics.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            tmp_path: Pytest-provided temporary directory.
        """
        monkeypatch.setenv("NATIVE_GRPC_ADDRESS", "stub:native")
        monkeypatch.setenv("OPA_GRPC_ADDRESS", "stub:opa")
        monkeypatch.setenv("ANSIBLE_GRPC_ADDRESS", "stub:ansible")
        for var in (
            "GITLEAKS_GRPC_ADDRESS",
            "COLLECTION_HEALTH_GRPC_ADDRESS",
            "DEP_AUDIT_GRPC_ADDRESS",
        ):
            monkeypatch.delenv(var, raising=False)

        results = [
            _ValidatorResult(
                violations=[],
                diagnostics=ValidatorDiagnostics(
                    validator_name="native", request_id="agg-real", total_ms=3.0, violations_found=0
                ),
                logs=[],
            ),
            _ValidatorResult(
                violations=[],
                diagnostics=ValidatorDiagnostics(
                    validator_name="opa", request_id="agg-real", total_ms=4.0, violations_found=0
                ),
                logs=[],
            ),
            _ValidatorResult(
                violations=[],
                diagnostics=ValidatorDiagnostics(
                    validator_name="ansible", request_id="agg-real", total_ms=5.0, violations_found=0
                ),
                logs=[],
            ),
        ]
        fake_ctx = SimpleNamespace(
            hierarchy_payload={"metadata": {}, "collection_set": []},
            scandata=None,
            engine_diagnostics=SimpleNamespace(
                parse_ms=1.0, annotate_ms=2.0, total_ms=5.0, files_scanned=1, graph_nodes_built=0
            ),
        )
        mgr = MagicMock()
        mgr.get.return_value = None
        mgr.acquire.return_value = SimpleNamespace(venv_root=tmp_path, failed_collections=[], installed_collections=[])

        servicer = EngineServicer()
        with (
            patch("apme_engine.daemon.engine_server.run_scan", return_value=fake_ctx),
            patch("apme_engine.daemon.engine_server._call_validator", side_effect=results) as mock_call,
            patch.object(EngineServicer, "_get_venv_manager", return_value=mgr),
        ):
            outcome = await servicer._execute_scan_pipeline(
                tmp_path, [File(path="play.yml", content=b"---\n")], "agg-real"
            )

        assert mock_call.call_count == 3
        scan_diag = outcome[1]
        assert scan_diag is not None
        assert {entry.validator_name for entry in scan_diag.validators} == {"native", "opa", "ansible"}
        assert {entry.request_id for entry in scan_diag.validators} == {"agg-real"}
        assert scan_diag.total_violations == 0
