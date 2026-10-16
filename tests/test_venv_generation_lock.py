"""Concurrency tests for venv generation read-write locks (N15)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest

from apme_engine.cache import BoundedCache
from apme_engine.venv_manager.session import (
    VenvGenerationLock,
    clear_generation_locks,
    get_generation_lock,
    venv_read_guard,
    venv_write_guard,
)

if TYPE_CHECKING:
    from apme_engine.daemon.engine_server import EngineServicer


async def _wait_for(predicate: Callable[[], object], timeout: float = 2.0, interval: float = 0.005) -> bool:
    """Poll predicate until true or deadline passes (deterministic wait).

    Event-loop friendly (yields via ``asyncio.sleep``): lock-ordering
    assertions rendezvous on actual state instead of fixed wall-clock
    sleeps. Negative assertions use ``assert not await _wait_for(...)``
    with a short timeout so a buggy early entry fails fast while the
    passing case waits out the bounded window.

    Args:
        predicate: Zero-arg callable returning truthy when the condition holds.
        timeout: Maximum seconds to wait.
        interval: Poll interval in seconds.

    Returns:
        True when the predicate held before the deadline.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


async def test_generation_locks_are_shared_per_key() -> None:
    """Locks for the same generation are shared; different keys differ."""
    clear_generation_locks()
    try:
        first = get_generation_lock("sess-a", "2.17")
        second = get_generation_lock("sess-a", "2.17.0")
        other = get_generation_lock("sess-a", "2.18")
        assert first is second
        assert first is not other
    finally:
        clear_generation_locks()


async def test_multiple_readers_hold_concurrently() -> None:
    """Two readers can hold the shared side at the same time."""
    clear_generation_locks()
    try:
        entered = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []

        async def _reader(name: str) -> None:
            async with venv_read_guard("sess-r", "2.17"):
                order.append(f"{name}-enter")
                entered.set()
                await asyncio.wait_for(release.wait(), timeout=2.0)
                order.append(f"{name}-exit")

        first = asyncio.create_task(_reader("r1"))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        entered.clear()
        second = asyncio.create_task(_reader("r2"))
        # Rendezvous on actual state: both readers entered without waiting
        # for the other to exit.
        assert await _wait_for(lambda: "r1-enter" in order and "r2-enter" in order), (
            "second reader never entered concurrently"
        )
        assert "r1-exit" not in order
        release.set()
        await asyncio.gather(first, second)
        assert order.count("r1-exit") == 1
        assert order.count("r2-exit") == 1
    finally:
        clear_generation_locks()


async def test_write_blocks_until_readers_finish() -> None:
    """An install (write) waits while validator fan-out (read) holds the lock."""
    clear_generation_locks()
    try:
        release_read = asyncio.Event()
        read_entered = asyncio.Event()
        writer_started = asyncio.Event()
        events: list[str] = []

        async def _reader() -> None:
            async with venv_read_guard("sess-rw", "2.17"):
                events.append("read-enter")
                read_entered.set()
                await asyncio.wait_for(release_read.wait(), timeout=2.0)
                events.append("read-exit")

        async def _writer() -> None:
            # Rendezvous: only contend once the reader actually holds the lock.
            await asyncio.wait_for(read_entered.wait(), timeout=2.0)
            writer_started.set()
            async with venv_write_guard("sess-rw", "2.17"):
                events.append("write-enter")
                events.append("write-exit")

        reader = asyncio.create_task(_reader())
        assert await _wait_for(lambda: events == ["read-enter"]), "reader never entered"
        writer = asyncio.create_task(_writer())
        # Writer announced its attempt but must not enter while the reader
        # holds the lock (bounded poll: fails fast on a buggy early entry).
        assert await _wait_for(writer_started.is_set), "writer never started"
        assert not await _wait_for(lambda: "write-enter" in events, timeout=0.2), (
            "writer entered while the reader held the lock"
        )
        release_read.set()
        await asyncio.gather(reader, writer)
        assert events == ["read-enter", "read-exit", "write-enter", "write-exit"]
    finally:
        clear_generation_locks()


async def test_read_blocks_while_writer_holds() -> None:
    """Fan-out (read) waits while an install (write) holds the lock."""
    clear_generation_locks()
    try:
        release_write = asyncio.Event()
        write_entered = asyncio.Event()
        reader_started = asyncio.Event()
        events: list[str] = []

        async def _writer() -> None:
            async with venv_write_guard("sess-wr", "2.17"):
                events.append("write-enter")
                write_entered.set()
                await asyncio.wait_for(release_write.wait(), timeout=2.0)
                events.append("write-exit")

        async def _reader() -> None:
            # Rendezvous: only contend once the writer actually holds the lock.
            await asyncio.wait_for(write_entered.wait(), timeout=2.0)
            reader_started.set()
            async with venv_read_guard("sess-wr", "2.17"):
                events.append("read-enter")

        writer = asyncio.create_task(_writer())
        assert await _wait_for(lambda: events == ["write-enter"]), "writer never entered"
        reader = asyncio.create_task(_reader())
        # Reader announced its attempt but must not enter while the writer
        # holds the lock (bounded poll: fails fast on a buggy early entry).
        assert await _wait_for(reader_started.is_set), "reader never started"
        assert not await _wait_for(lambda: "read-enter" in events, timeout=0.2), (
            "reader entered while the writer held the lock"
        )
        release_write.set()
        await asyncio.gather(writer, reader)
        assert events == ["write-enter", "write-exit", "read-enter"]
    finally:
        clear_generation_locks()


async def test_second_reader_blocks_on_writer() -> None:
    """A second concurrent reader still blocks while a writer holds the lock.

    Regression test for the ``readers == 1`` bug: only the first reader
    awaited the exclusive side, so a second reader arriving while a writer
    held it slipped straight into the critical section.
    """
    clear_generation_locks()
    try:
        release_write = asyncio.Event()
        entered = asyncio.Event()
        events: list[str] = []
        started: list[str] = []

        async def _writer() -> None:
            async with venv_write_guard("sess-r2w", "2.17"):
                events.append("write-enter")
                entered.set()
                await asyncio.wait_for(release_write.wait(), timeout=5.0)
                events.append("write-exit")

        async def _reader(name: str) -> None:
            started.append(name)
            async with venv_read_guard("sess-r2w", "2.17"):
                events.append(f"{name}-enter")

        writer = asyncio.create_task(_writer())
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        first = asyncio.create_task(_reader("r1"))
        assert await _wait_for(lambda: "r1" in started), "first reader never started"
        second = asyncio.create_task(_reader("r2"))
        assert await _wait_for(lambda: sorted(started) == ["r1", "r2"]), "second reader never started"
        # Neither reader may enter while the writer holds the lock (bounded
        # poll: fails fast on a buggy early entry).
        assert not await _wait_for(lambda: "r1-enter" in events or "r2-enter" in events, timeout=0.2), (
            "reader entered while the writer held the lock"
        )
        release_write.set()
        await asyncio.gather(writer, first, second)
        assert events[0] == "write-enter"
        assert events[1] == "write-exit"
        assert sorted(events[2:]) == ["r1-enter", "r2-enter"]
    finally:
        clear_generation_locks()


async def test_writer_waits_for_two_readers_then_proceeds() -> None:
    """A writer drains two concurrent readers before entering (no starvation)."""
    clear_generation_locks()
    try:
        release = asyncio.Event()
        writer_started = asyncio.Event()
        events: list[str] = []

        async def _reader(name: str) -> None:
            async with venv_read_guard("sess-2rw", "2.17"):
                events.append(f"{name}-enter")
                await asyncio.wait_for(release.wait(), timeout=5.0)
                events.append(f"{name}-exit")

        async def _writer() -> None:
            writer_started.set()
            async with venv_write_guard("sess-2rw", "2.17"):
                events.append("write-enter")
                events.append("write-exit")

        readers = [asyncio.create_task(_reader(f"r{i}")) for i in range(2)]
        assert await _wait_for(lambda: events.count("r1-enter") + events.count("r0-enter") == 2), (
            "readers never entered"
        )
        writer = asyncio.create_task(_writer())
        # Writer announced its attempt but must not enter while the readers
        # hold the lock (bounded poll: fails fast on a buggy early entry).
        assert await _wait_for(writer_started.is_set), "writer never started"
        assert not await _wait_for(lambda: "write-enter" in events, timeout=0.2), (
            "writer entered while readers held the lock"
        )
        release.set()
        await asyncio.gather(*readers, writer)
        assert events[-2:] == ["write-enter", "write-exit"]
    finally:
        clear_generation_locks()


async def test_acquire_read_times_out_behind_writer() -> None:
    """A reader blocked by a held writer fails fast with TimeoutError."""
    clear_generation_locks()
    try:
        lock = get_generation_lock("sess-timeout", "2.17")
        await lock.acquire_write()
        try:
            with pytest.raises(TimeoutError):
                await lock.acquire_read(timeout=0.05)
        finally:
            await lock.release_write()
        # After release the read path works again.
        await lock.acquire_read(timeout=1.0)
        await lock.release_read()
    finally:
        clear_generation_locks()


async def test_acquire_write_times_out_behind_readers() -> None:
    """A writer blocked by held readers fails fast with TimeoutError."""
    clear_generation_locks()
    try:
        lock = get_generation_lock("sess-timeout-w", "2.17")
        await lock.acquire_read()
        try:
            with pytest.raises(TimeoutError):
                await lock.acquire_write(timeout=0.05)
        finally:
            await lock.release_read()
        await lock.acquire_write(timeout=1.0)
        await lock.release_write()
    finally:
        clear_generation_locks()


async def test_generation_lock_eviction_skips_busy_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """LRU eviction drops the oldest IDLE lock, never a held one.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.venv_manager.session as sess_mod

    monkeypatch.setattr(
        sess_mod,
        "_GENERATION_LOCKS",
        BoundedCache(maxsize=2, is_idle=lambda lock: lock.is_idle()),
    )
    try:
        busy = get_generation_lock("sess-busy", "2.17")
        await busy.acquire_read()
        try:
            get_generation_lock("sess-idle", "2.17")
            surviving = get_generation_lock("sess-new", "2.17")
            assert surviving is not busy
            # The held lock survives; the idle one was evicted instead.
            assert get_generation_lock("sess-busy", "2.17") is busy
        finally:
            await busy.release_read()
    finally:
        clear_generation_locks()


async def test_generation_lock_all_busy_grows_over_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every lock is in use the map grows over cap (no live drop).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import apme_engine.venv_manager.session as sess_mod

    monkeypatch.setattr(
        sess_mod,
        "_GENERATION_LOCKS",
        BoundedCache(maxsize=2, is_idle=lambda lock: lock.is_idle()),
    )
    first: VenvGenerationLock | None = None
    second: VenvGenerationLock | None = None
    try:
        first = get_generation_lock("sess-o1", "2.17")
        second = get_generation_lock("sess-o2", "2.17")
        await first.acquire_read()
        await second.acquire_read()
        try:
            third = get_generation_lock("sess-o3", "2.17")
            assert len(sess_mod._GENERATION_LOCKS) == 3
            assert get_generation_lock("sess-o1", "2.17") is first
            assert get_generation_lock("sess-o2", "2.17") is second
            assert get_generation_lock("sess-o3", "2.17") is third
        finally:
            await first.release_read()
            await second.release_read()
    finally:
        clear_generation_locks()


async def test_abandoned_writer_wakes_parked_readers() -> None:
    """A writer that times out wakes readers parked on writer-preference.

    A reader parks while a writer announcement is outstanding. When the
    writer abandons its wait (timeout), the reader must be woken and
    acquire promptly instead of hanging until its own timeout.
    """
    clear_generation_locks()
    try:
        lock = get_generation_lock("sess-abandon", "2.17")
        await lock.acquire_read()
        try:
            writer_task = asyncio.create_task(lock.acquire_write(timeout=0.05))
            # Wait until the writer announcement is outstanding.
            announced = False
            for _ in range(100):
                async with lock._cond:
                    if lock._writer_waiting > 0:
                        announced = True
                        break
                await asyncio.sleep(0.005)
            assert announced, "writer never announced"
            # Park a reader on the outstanding announcement.
            reader_acquired = asyncio.Event()
            reader_started = asyncio.Event()

            async def _parked_reader() -> None:
                reader_started.set()
                await lock.acquire_read(timeout=2.0)
                reader_acquired.set()
                await lock.release_read()

            reader_task = asyncio.create_task(_parked_reader())
            await asyncio.wait_for(reader_started.wait(), timeout=2.0)
            # Zero-duration yields let the started reader park on the
            # announcement without consuming the writer's 0.05s budget.
            # Guarded: on a stalled runner the writer may time out first
            # (waking the reader via the abandon notification), so only
            # assert parked-waiting while the writer is still pending.
            for _ in range(10):
                await asyncio.sleep(0)
            if not writer_task.done():
                assert not reader_acquired.is_set()
            # Let the writer abandon its wait (0.05s timeout).
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(writer_task, timeout=2.0)
            # The parked reader must wake promptly via the abandon
            # notification — not hang until its own 2s timeout.
            await asyncio.wait_for(reader_task, timeout=2.0)
            assert reader_acquired.is_set()
        finally:
            await lock.release_read()
    finally:
        clear_generation_locks()


async def test_wait_out_worker_joins_on_cancel() -> None:
    """Cancelling a wait_out_worker waiter does not release early.

    The waiter stays pending until the shielded worker finishes and
    then raises CancelledError; the worker result is never observed
    by the cancelled waiter.
    """
    import time as _time

    from apme_engine.venv_manager.session import wait_out_worker

    loop = asyncio.get_running_loop()
    worker_done = asyncio.Event()

    def _slow() -> str:
        _time.sleep(0.3)
        loop.call_soon_threadsafe(worker_done.set)
        return "built"

    worker = loop.run_in_executor(None, _slow)
    waiter = asyncio.create_task(wait_out_worker(worker))
    await asyncio.sleep(0.05)
    assert not worker_done.is_set()
    waiter.cancel()
    # Still joining the worker shortly after cancel: not done yet.
    await asyncio.sleep(0.1)
    assert not waiter.done()
    assert not worker_done.is_set()
    # Once the worker finishes, the waiter surfaces cancellation.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=2.0)
    assert worker_done.is_set()
    assert worker.result() == "built"


async def test_wait_out_worker_returns_result_without_cancel() -> None:
    """Without cancellation the helper returns the worker result."""
    from apme_engine.venv_manager.session import wait_out_worker

    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(None, lambda: "ok")
    assert await wait_out_worker(worker) == "ok"


class _FakeGuard:
    """Async guard double: raises TimeoutError N times, then succeeds."""

    def __init__(self, failures: int, calls: list[str]) -> None:
        """Record guard entries and fail the first *failures* of them.

        Args:
            failures: Number of initial entries raising TimeoutError.
            calls: Shared list appended with "guard" per entry.
        """
        self._failures = failures
        self.calls = calls

    async def __aenter__(self) -> str:
        self.calls.append("guard")
        if len([c for c in self.calls if c == "guard"]) <= self._failures:
            raise TimeoutError("guard busy")
        return "guard"

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSlot:
    """Async slot double yielding a no-op release hook."""

    async def __aenter__(self) -> object:
        def _release(_fut: object) -> None:
            return None

        return _release

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeGalaxy:
    """Async Galaxy-env double yielding an empty env."""

    async def __aenter__(self) -> dict[str, str]:
        return {}

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _fake_server(sentinel: object) -> object:
    """Build a minimal server double for the retry helper.

    Args:
        sentinel: Value the fake venv-manager acquire returns.

    Returns:
        Namespace with the two server methods the helper needs.
        Callers cast to ``EngineServicer`` when invoking the helper.
    """
    import types

    class _Mgr:
        def acquire(self, *args: object, **kwargs: object) -> object:
            return sentinel

    # _activate_galaxy_proxy_config is used as an async context manager
    # factory; model it as a plain (non-async) function returning _FakeGalaxy.
    server = types.SimpleNamespace(
        _get_venv_manager=lambda: _Mgr(),
        _activate_galaxy_proxy_config=lambda _path: _FakeGalaxy(),
    )
    return server


async def test_guard_contention_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard TimeoutError before the worker starts re-queues with backoff (#5).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import contextvars

    import apme_engine.daemon.engine_server as _es

    sentinel = object()
    guard_calls: list[str] = []
    monkeypatch.setattr(_es, "venv_write_guard", lambda _sid, _ver: _FakeGuard(2, guard_calls))
    monkeypatch.setattr(_es, "limit_venv_builds", lambda: _FakeSlot())

    async def _ok(_fut: object) -> object:
        return sentinel

    monkeypatch.setattr(_es, "wait_out_worker", _ok)
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    server = _fake_server(sentinel)
    got = await _es.EngineServicer._acquire_venv_session_with_retries(
        cast("EngineServicer", server),
        sid="sess-retry",
        core_version="2.17",
        collection_specs=[],
        galaxy_cfg_path=None,
        scan_id="scan-retry",
        ctx=contextvars.copy_context(),
    )
    assert got is sentinel
    assert guard_calls.count("guard") == 3
    assert sleeps == [
        _es._VENV_GUARD_ACQUIRE_BACKOFF_S * 1,
        _es._VENV_GUARD_ACQUIRE_BACKOFF_S * 2,
    ]


async def test_worker_timeout_fails_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker TimeoutError after start raises immediately without retry (#5).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import contextvars

    import apme_engine.daemon.engine_server as _es

    guard_calls: list[str] = []
    monkeypatch.setattr(_es, "venv_write_guard", lambda _sid, _ver: _FakeGuard(0, guard_calls))
    monkeypatch.setattr(_es, "limit_venv_builds", lambda: _FakeSlot())

    async def _boom(_fut: object) -> object:
        raise TimeoutError("worker blew up")

    monkeypatch.setattr(_es, "wait_out_worker", _boom)
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    server = _fake_server(object())
    with pytest.raises(TimeoutError):
        await _es.EngineServicer._acquire_venv_session_with_retries(
            cast("EngineServicer", server),
            sid="sess-worker",
            core_version="2.17",
            collection_specs=[],
            galaxy_cfg_path=None,
            scan_id="scan-worker",
            ctx=contextvars.copy_context(),
        )
    assert guard_calls.count("guard") == 1
    assert sleeps == []


async def test_guard_exhaustion_raises_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent guard contention raises after exactly N attempts (#5).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import contextvars

    import apme_engine.daemon.engine_server as _es

    guard_calls: list[str] = []
    monkeypatch.setattr(
        _es, "venv_write_guard", lambda _sid, _ver: _FakeGuard(_es._VENV_GUARD_ACQUIRE_RETRIES, guard_calls)
    )
    monkeypatch.setattr(_es, "limit_venv_builds", lambda: _FakeSlot())
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    server = _fake_server(object())
    with pytest.raises(TimeoutError):
        await _es.EngineServicer._acquire_venv_session_with_retries(
            cast("EngineServicer", server),
            sid="sess-exhaust",
            core_version="2.17",
            collection_specs=[],
            galaxy_cfg_path=None,
            scan_id="scan-exhaust",
            ctx=contextvars.copy_context(),
        )
    assert guard_calls.count("guard") == _es._VENV_GUARD_ACQUIRE_RETRIES
    assert len(sleeps) == _es._VENV_GUARD_ACQUIRE_RETRIES - 1
