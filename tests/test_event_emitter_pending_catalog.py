"""Stale catalog retry tests for the event emitter (N16)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest

from apme.v1.reporting_pb2 import RegisterRulesRequest, RegisterRulesResponse, RuleDefinition
from apme_engine.daemon import event_emitter


def _catalog_request(*rule_ids: str) -> RegisterRulesRequest:
    """Build a catalog request with the given rule ids.

    Args:
        *rule_ids: Rule identifiers to include.

    Returns:
        RegisterRulesRequest with one RuleDefinition per id.
    """
    return RegisterRulesRequest(
        pod_id="test-pod",
        is_authority=True,
        rules=[RuleDefinition(rule_id=r, source="native", description=f"rule {r}") for r in rule_ids],
    )


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _clear_emitter() -> AsyncIterator[None]:
    """Reset emitter sinks, retry task, and pending catalog around each test.

    Yields:
        None: Test runs between setup and teardown.
    """
    event_emitter._sinks.clear()
    event_emitter._rule_catalog_registered = False
    event_emitter._pending_catalog = None
    if event_emitter._rule_retry_task is not None:
        event_emitter._rule_retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_emitter._rule_retry_task
    event_emitter._rule_retry_task = None
    yield
    event_emitter._sinks.clear()
    event_emitter._rule_catalog_registered = False
    event_emitter._pending_catalog = None
    if event_emitter._rule_retry_task is not None:
        event_emitter._rule_retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_emitter._rule_retry_task
    event_emitter._rule_retry_task = None


class _RecordingSink:
    """Sink that fails twice then accepts, recording every request."""

    def __init__(self) -> None:
        """Initialise empty call log and failure counter."""
        self.calls: list[RegisterRulesRequest] = []
        self.failures_remaining = 2

    async def start(self) -> None:
        """No-op start."""

    async def stop(self) -> None:
        """No-op stop."""

    async def on_fix_completed(self, event: object) -> None:
        """No-op fix event.

        Args:
            event: Unused fix event.
        """

    async def register_rules(
        self,
        request: RegisterRulesRequest,
    ) -> RegisterRulesResponse | None:
        """Fail twice, then accept.

        Args:
            request: Registration payload to record.

        Returns:
            None while failures remain, else an accepted response.
        """
        self.calls.append(request)
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            return None
        return RegisterRulesResponse(accepted=True, rules_added=len(request.rules))


async def test_newer_catalog_supersedes_stale_retry() -> None:
    """Fail first, change catalog, fail second, assert newest is held."""
    sink = _RecordingSink()
    event_emitter._sinks.append(sink)

    first = _catalog_request("L001")
    second = _catalog_request("L001", "L002")

    with patch.object(event_emitter, "_RULE_RETRY_INITIAL_DELAY_S", 0.02):
        # First emit fails (sink returns None) and schedules the retry task.
        await event_emitter.emit_register_rules(first)
        assert event_emitter._rule_retry_task is not None
        first_task = event_emitter._rule_retry_task

        # Second emit while the retry is pending must not spawn a duplicate
        # task; it only updates the pending slot to the newest catalog.
        await event_emitter.emit_register_rules(second)
        assert event_emitter._rule_retry_task is first_task
        pending = event_emitter._pending_catalog
        assert pending is not None
        assert [r.rule_id for r in pending.rules] == ["L001", "L002"]

        # Let the retry loop run: first retry attempt fails (second failure),
        # second retry attempt succeeds with the newest catalog.
        await asyncio.wait_for(first_task, timeout=5.0)

    assert event_emitter._rule_catalog_registered is True
    assert event_emitter._pending_catalog is None
    # Calls: initial first, initial second, retry newest, retry newest.
    assert len(sink.calls) >= 3
    last = sink.calls[-1]
    assert [r.rule_id for r in last.rules] == ["L001", "L002"]


async def test_three_rapid_emits_collapse_to_newest() -> None:
    """Three rapid emits keep one retry task and the newest catalog only."""
    sink = _RecordingSink()
    sink.failures_remaining = 10**9
    event_emitter._sinks.append(sink)

    first = _catalog_request("L001")
    second = _catalog_request("L001", "L002")
    third = _catalog_request("L001", "L002", "L003")

    with patch.object(event_emitter, "_RULE_RETRY_INITIAL_DELAY_S", 0.02):
        await event_emitter.emit_register_rules(first)
        assert event_emitter._rule_retry_task is not None
        only_task = event_emitter._rule_retry_task

        await event_emitter.emit_register_rules(second)
        assert event_emitter._rule_retry_task is only_task
        await event_emitter.emit_register_rules(third)
        assert event_emitter._rule_retry_task is only_task

        # Single slot, newest wins: intermediate catalogs are dropped,
        # never queued behind each other.
        assert event_emitter._pending_catalog is third

    assert event_emitter._rule_catalog_registered is False


class _SerializationProbeSink:
    """Sink that blocks inside ``register_rules`` and records overlap."""

    def __init__(self) -> None:
        """Initialise the probe state."""
        self.in_flight = 0
        self.peak = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self) -> None:
        """No-op start."""

    async def stop(self) -> None:
        """No-op stop."""

    async def on_fix_completed(self, event: object) -> None:
        """No-op fix event.

        Args:
            event: Unused fix event.
        """

    async def register_rules(self, request: RegisterRulesRequest) -> RegisterRulesResponse | None:
        """Block until released, reporting the peak overlap.

        Args:
            request: Registration payload.

        Returns:
            Accepted response.
        """
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.entered.set()
        await self.release.wait()
        self.in_flight -= 1
        return RegisterRulesResponse(accepted=True, rules_added=len(request.rules))


async def test_concurrent_emits_are_serialized() -> None:
    """Two emits never have their registration RPCs in flight together.

    The Gateway applies whichever commit lands last, so overlapping sends let
    an older catalog overwrite a newer one while both report success and
    neither path queues another push.
    """
    sink = _SerializationProbeSink()
    event_emitter._sinks.append(sink)

    with patch.object(event_emitter, "_RULE_RETRY_INITIAL_DELAY_S", 0.02):
        first = asyncio.create_task(event_emitter.emit_register_rules(_catalog_request("L001")))
        await asyncio.wait_for(sink.entered.wait(), timeout=5.0)
        second = asyncio.create_task(event_emitter.emit_register_rules(_catalog_request("L001", "L002")))
        await asyncio.sleep(0.1)
        assert sink.peak == 1, "rule-catalog sends overlapped"
        sink.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=5.0)

    assert event_emitter._rule_catalog_registered is True
    assert event_emitter._pending_catalog is None


async def test_catalog_stored_during_cancel_gets_a_worker() -> None:
    """A catalog queued while the old retry worker is stopped is still pushed.

    The success path clears the pending slot and then stops the retry worker.
    A concurrent failing emit that stores a newer catalog in that window sees
    the (cancelling) worker as running and starts no replacement, so returning
    immediately would leave that catalog pending with nobody to deliver it.
    """

    class _AcceptAllSink:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def start(self) -> None:
            """No-op start."""

        async def stop(self) -> None:
            """No-op stop."""

        async def on_fix_completed(self, event: object) -> None:
            """No-op fix event.

            Args:
                event: Unused fix event.
            """

        async def register_rules(self, request: RegisterRulesRequest) -> RegisterRulesResponse | None:
            """Accept and record the catalog.

            Args:
                request: Registration payload.

            Returns:
                Accepted response.
            """
            self.calls.append([rule.rule_id for rule in request.rules])
            return RegisterRulesResponse(accepted=True, rules_added=len(request.rules))

    sink = _AcceptAllSink()
    event_emitter._sinks.append(sink)
    queued = _catalog_request("L009")

    async def _worker() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            # Stands in for a concurrent failing emit that stores its catalog
            # while this worker is being cancelled.
            event_emitter._pending_catalog = queued

    worker = asyncio.create_task(_worker())
    await asyncio.sleep(0)
    event_emitter._rule_retry_task = worker

    with patch.object(event_emitter, "_RULE_RETRY_INITIAL_DELAY_S", 0.02):
        await event_emitter.emit_register_rules(_catalog_request("L001"))
        assert event_emitter._rule_retry_task is not None, "queued catalog lost its worker"
        await asyncio.wait_for(event_emitter._rule_retry_task, timeout=5.0)

    assert [rule_id for rule_id in sink.calls[-1]] == ["L009"]
    assert event_emitter._pending_catalog is None
