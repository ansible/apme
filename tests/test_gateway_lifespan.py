"""Gateway startup and shutdown lifecycle tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

import apme_gateway.app as gateway_app


async def test_outbox_reconciliation_runs_in_background_and_is_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow outbox drain does not delay startup and is cancelled on shutdown.

    Args:
        monkeypatch: Pytest fixture for replacing startup dependencies.
    """
    # Bound the test so a regression that moves the drain back before yield
    # fails promptly instead of hanging the test process.
    started = asyncio.Event()
    cancelled = asyncio.Event()
    finish = asyncio.Event()

    async def _drain() -> int:
        started.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return 0

    registry = MagicMock()
    registry.shutdown = AsyncMock()
    galaxy_sync = AsyncMock()
    stop_pending_push = AsyncMock()
    monkeypatch.setattr(gateway_app, "reconcile_galaxy_config", galaxy_sync)
    monkeypatch.setattr(gateway_app, "stop_pending_push", stop_pending_push)
    monkeypatch.setattr(gateway_app, "get_operation_registry", lambda: registry)
    monkeypatch.setattr("apme_gateway.notifications.drain_notification_outbox", _drain)

    async with asyncio.timeout(1.0):
        async with gateway_app._lifespan(FastAPI()):
            await asyncio.wait_for(started.wait(), timeout=0.5)
            assert not cancelled.is_set()

    assert cancelled.is_set()
    galaxy_sync.assert_awaited_once()
    stop_pending_push.assert_awaited_once()
    registry.shutdown.assert_awaited_once()


async def test_outbox_startup_invokes_one_bounded_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup delegates one bounded, cursor-based outbox drain.

    Args:
        monkeypatch: Pytest fixture for replacing startup dependencies.
    """
    calls = 0
    drained = asyncio.Event()

    async def _drain() -> int:
        nonlocal calls
        calls += 1
        drained.set()
        return 0

    registry = MagicMock()
    registry.shutdown = AsyncMock()
    monkeypatch.setattr(gateway_app, "reconcile_galaxy_config", AsyncMock())
    monkeypatch.setattr(gateway_app, "stop_pending_push", AsyncMock())
    monkeypatch.setattr(gateway_app, "get_operation_registry", lambda: registry)
    monkeypatch.setattr("apme_gateway.notifications.drain_notification_outbox", _drain)

    async with asyncio.timeout(1.0):
        async with gateway_app._lifespan(FastAPI()):
            await asyncio.wait_for(drained.wait(), timeout=0.5)
            await asyncio.sleep(0)

    assert calls == 1
