"""Project deletion and operation creation share a lifecycle lock."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from apme_gateway import operation_registry as operation_registry_module
from apme_gateway.api import operation_router, router
from apme_gateway.api.operation_router import OperateRequest, initiate_operation
from apme_gateway.api.router import delete_project_endpoint
from apme_gateway.db import queries as q
from apme_gateway.operation_registry import OperationRegistry
from apme_gateway.operation_types import OperationStatus


class _Session:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


async def test_delete_rejects_a_project_with_active_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active registry operation prevents project deletion.

    Args:
        monkeypatch: Replaces database access with deterministic async mocks.
    """
    registry = OperationRegistry()
    registry.create(
        operation_id="operation-1",
        project_id="project-1",
        scan_id="scan-1",
        scan_type="check",
    )
    resolve_project = AsyncMock(return_value=SimpleNamespace(id="project-1"))
    delete_project = AsyncMock(return_value=True)
    monkeypatch.setattr(router, "get_session", _Session)
    monkeypatch.setattr(q, "resolve_project", resolve_project)
    monkeypatch.setattr(q, "delete_project", delete_project)
    monkeypatch.setattr(operation_registry_module, "get_operation_registry", lambda: registry)

    with pytest.raises(HTTPException) as exc_info:
        await delete_project_endpoint("project-1")

    assert exc_info.value.status_code == 409
    delete_project.assert_not_awaited()


async def test_delete_removes_terminal_operation_snapshot_and_idle_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleted projects no longer retain operation results or lifecycle locks.

    Args:
        monkeypatch: Replaces database access with deterministic async mocks.
    """
    registry = OperationRegistry()
    registry.create(
        operation_id="operation-1",
        project_id="project-1",
        scan_id="scan-1",
        scan_type="check",
    )
    registry.transition("operation-1", OperationStatus.COMPLETED)
    subscriber = registry.subscribe("operation-1")
    resolve_project = AsyncMock(return_value=SimpleNamespace(id="project-1"))
    delete_project = AsyncMock(return_value=True)
    monkeypatch.setattr(router, "get_session", _Session)
    monkeypatch.setattr(q, "resolve_project", resolve_project)
    monkeypatch.setattr(q, "delete_project", delete_project)
    monkeypatch.setattr(operation_registry_module, "get_operation_registry", lambda: registry)

    await delete_project_endpoint("project-1")

    assert delete_project.await_count == 1
    assert registry.get("operation-1") is None
    assert registry.get_by_project("project-1") is None
    assert registry._project_locks == {}
    assert subscriber is not None
    assert subscriber.get_nowait() == {"_close": True}


async def test_project_lock_keeps_one_lock_for_waiters_then_reclaims_it() -> None:
    """Waiters share a lock and the final user releases its registry entry."""
    registry = OperationRegistry()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with registry.project_lock("project-1"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        await first_entered.wait()
        async with registry.project_lock("project-1"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    lock = registry._project_locks["project-1"]
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)

    assert registry._project_locks["project-1"] is lock
    assert registry._project_lock_users["project-1"] == 2

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert second_entered.is_set()
    assert registry._project_locks == {}
    assert registry._project_lock_users == {}


async def test_operation_rechecks_project_after_waiting_for_deletion_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operation creation fails if deletion wins the project lock.

    Args:
        monkeypatch: Simulates the project disappearing before lock acquisition.
    """
    registry = OperationRegistry()
    resolve_project = AsyncMock(side_effect=[SimpleNamespace(id="project-1"), None])
    monkeypatch.setattr(operation_router, "get_session", _Session)
    monkeypatch.setattr(q, "resolve_project", resolve_project)
    monkeypatch.setattr(operation_router, "get_operation_registry", lambda: registry)

    with pytest.raises(HTTPException) as exc_info:
        await initiate_operation("project-1", OperateRequest(action="check"))

    assert exc_info.value.status_code == 404
    assert resolve_project.await_count == 2
