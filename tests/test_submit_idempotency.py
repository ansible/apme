"""Approve-submit idempotency tests (N19)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apme_gateway.api.submit_idempotency import clear_submit_idempotency_store
from apme_gateway.app import create_app
from apme_gateway.db import get_session
from apme_gateway.db.models import PatchedFile, Project, Scan, Session
from apme_gateway.operation_registry import get_operation_registry
from apme_gateway.operation_types import OperationResult, OperationStatus, Proposal

pytestmark = pytest.mark.usefixtures("gateway_db")


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _clear_idempotency() -> AsyncIterator[None]:
    """Clear the submit idempotency cache around each test.

    Yields:
        None: Test runs between setup and teardown.
    """
    clear_submit_idempotency_store()
    yield
    clear_submit_idempotency_store()


@pytest.fixture  # type: ignore[untyped-decorator]
async def client() -> AsyncIterator[AsyncClient]:
    """Build an async test client for the gateway app.

    Yields:
        AsyncClient: Client bound to the ASGI app.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _op_url(project_id: str, suffix: str) -> str:
    """Build an operation endpoint URL.

    Args:
        project_id: Target project UUID.
        suffix: Endpoint suffix starting with ``/``.

    Returns:
        Full operation endpoint path.
    """
    return f"/api/v1/projects/{project_id}/operation{suffix}"


async def _setup_completed_operation(
    *,
    project_id: str = "proj-submit-1",
    scan_id: str = "scan-submit-1",
    operation_id: str = "op-submit-1",
) -> None:
    """Seed a project, scan, and completed live operation with patches.

    Args:
        project_id: Project UUID.
        scan_id: Scan UUID.
        operation_id: Registry operation id.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="submit-proj",
                repo_url="https://github.com/org/repo.git",
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token="tok",
                scm_provider="github",
            )
        )
        db.add(Session(session_id="sess-submit", project_path="/proj", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id=scan_id,
                session_id="sess-submit",
                project_id=project_id,
                project_path="/proj",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=2,
                fixed_count=2,
            )
        )
        db.add(PatchedFile(scan_id=scan_id, path="a.yml", content=b"fixed\n"))
        await db.commit()

    registry = get_operation_registry()
    state = registry.create(
        operation_id=operation_id,
        project_id=project_id,
        scan_id=scan_id,
        scan_type="remediate",
    )
    registry.transition(operation_id, OperationStatus.COMPLETED)
    state.result = OperationResult(total_violations=2, remediated_count=2, patches=[{"path": "a.yml"}])


async def _setup_awaiting_approval(
    *,
    project_id: str = "proj-approve-1",
    scan_id: str = "scan-approve-1",
    operation_id: str = "op-approve-1",
) -> None:
    """Register an awaiting-approval operation with one proposal.

    Args:
        project_id: Project UUID.
        scan_id: Scan UUID.
        operation_id: Registry operation id.
    """
    registry = get_operation_registry()
    state = registry.create(
        operation_id=operation_id,
        project_id=project_id,
        scan_id=scan_id,
        scan_type="remediate",
    )
    await registry.set_proposals(operation_id, [Proposal(id="t1-1", rule_id="L001", file="a.yml")])
    assert state.status == OperationStatus.AWAITING_APPROVAL


async def test_approve_returns_submit_token(client: AsyncClient) -> None:
    """Approve returns an additive submit_token alongside status.

    Args:
        client: Async HTTP test client.
    """
    await _setup_awaiting_approval()
    with patch("apme_gateway.proposals.draft.commit_gate_decisions", new_callable=AsyncMock) as mock_commit:
        mock_commit.return_value = None
        resp = await client.post(_op_url("proj-approve-1", "/approve"), json={"approved_ids": ["t1-1"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert "submit_token" in body
    assert isinstance(body["submit_token"], str)
    assert len(body["submit_token"]) == 32


async def test_double_approve_replays_same_token(client: AsyncClient) -> None:
    """Duplicate approve replays the issued token instead of 409/500 (#17).

    Args:
        client: Async HTTP test client.
    """
    await _setup_awaiting_approval()
    with patch("apme_gateway.proposals.draft.commit_gate_decisions", new_callable=AsyncMock) as mock_commit:
        mock_commit.return_value = None
        first = await client.post(_op_url("proj-approve-1", "/approve"), json={"approved_ids": ["t1-1"]})
    assert first.status_code == 200
    token = first.json()["submit_token"]
    second = await client.post(_op_url("proj-approve-1", "/approve"), json={"approved_ids": ["t1-1"]})
    assert second.status_code == 200
    assert second.json()["submit_token"] == token


async def test_double_submit_with_token_replays(client: AsyncClient) -> None:
    """Double-submit with the same token returns the stored result, not 409.

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation()

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-1"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/1", branch_name="apme/remediate-x", provider="github"
        )

    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-1", "/submit"),
            json={"submit_token": "a1b2c3d4e5f60718293a4b5c6d7e8f90"},
        )
        assert first.status_code == 200, first.text
        first_body = first.json()

        second = await client.post(
            _op_url("proj-submit-1", "/submit"),
            json={"submit_token": "a1b2c3d4e5f60718293a4b5c6d7e8f90"},
        )
        assert second.status_code == 200
        assert second.json() == first_body

    # Token binding is persisted on the scan row for restart replay.
    async with get_session() as db:
        scan = await db.get(Scan, "scan-submit-1")
    assert scan is not None
    assert scan.submit_token == "a1b2c3d4e5f60718293a4b5c6d7e8f90"


async def test_double_submit_with_idempotency_key_header(client: AsyncClient) -> None:
    """Idempotency-Key header behaves like the body submit_token.

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation(
        project_id="proj-submit-2",
        scan_id="scan-submit-2",
        operation_id="op-submit-2",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-2"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/2", branch_name="apme/remediate-x", provider="github"
        )

    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-2", "/submit"),
            json={},
            headers={"Idempotency-Key": "b2c3d4e5f60718293a4b5c6d7e8f90a1"},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            _op_url("proj-submit-2", "/submit"),
            json={},
            headers={"Idempotency-Key": "b2c3d4e5f60718293a4b5c6d7e8f90a1"},
        )
        assert second.status_code == 200
        assert second.json() == first.json()


async def test_double_submit_without_token_is_409(client: AsyncClient) -> None:
    """Double-submit without a token keeps the existing 409 on pr_url.

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation(
        project_id="proj-submit-3",
        scan_id="scan-submit-3",
        operation_id="op-submit-3",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-3"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/3", branch_name="apme/remediate-x", provider="github"
        )

    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(_op_url("proj-submit-3", "/submit"), json={})
        assert first.status_code == 200, first.text
        second = await client.post(_op_url("proj-submit-3", "/submit"), json={})
        assert second.status_code == 409
        assert isinstance(second.json()["detail"], str)


async def test_weak_token_is_400(client: AsyncClient) -> None:
    """Guessable tokens (e.g. tok-abc-123) are rejected with 400.

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation(
        project_id="proj-submit-weak",
        scan_id="scan-submit-weak",
        operation_id="op-submit-weak",
    )
    resp = await client.post(
        _op_url("proj-submit-weak", "/submit"),
        json={"submit_token": "tok-abc-123"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_idempotency_token"


async def test_replay_with_different_branch_is_409(client: AsyncClient) -> None:
    """Same token with a different branch_name returns 409 conflict.

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation(
        project_id="proj-submit-4",
        scan_id="scan-submit-4",
        operation_id="op-submit-4",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-4"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/4", branch_name="apme/remediate-x", provider="github"
        )

    token = "c3d4e5f60718293a4b5c6d7e8f90a1b2"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-4", "/submit"),
            json={"submit_token": token, "branch_name": "apme/branch-a"},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            _op_url("proj-submit-4", "/submit"),
            json={"submit_token": token, "branch_name": "apme/branch-b"},
        )
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "idempotency_conflict"


async def test_restart_replay_resolves_provider(client: AsyncClient) -> None:
    """Restart replay (DB token, cold cache) uses project provider, not hardcoded.

    Args:
        client: Async HTTP test client.
    """
    project_id = "proj-submit-gitlab"
    scan_id = "scan-submit-gitlab"
    token = "d4e5f60718293a4b5c6d7e8f90a1b2c3"
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="submit-gitlab",
                repo_url="https://gitlab.example.com/org/repo.git",
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token="tok",
                scm_provider="gitlab",
            )
        )
        db.add(Session(session_id="sess-gitlab", project_path="/proj", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id=scan_id,
                session_id="sess-gitlab",
                project_id=project_id,
                project_path="/proj",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=1,
                fixed_count=1,
                pr_url="https://gitlab.example.com/org/repo/-/merge_requests/1",
                branch_name="apme/remediate-gitlab",
                commit_sha="sha-gitlab",
                submit_token=token,
                scm_provider="gitlab",
            )
        )
        await db.commit()

    resp = await client.post(
        f"/api/v1/projects/{project_id}/operation/submit",
        json={"submit_token": token, "activity_id": scan_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "gitlab"
    assert body["pr_url"] is not None


async def test_idempotency_ttl_expiry_evicts() -> None:
    """Backdated entries (>24h) miss and are evicted from the store."""
    import time

    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        _SUBMIT_IDEMPOTENCY_STORE,
        _SUBMIT_IDEMPOTENCY_TTL_S,
        SubmitBinding,
        SubmitIdempotencyKey,
        _get_idempotency_entry,
        _put_idempotency_entry,
    )

    project_id = "proj-ttl"
    token = "e5f60718293a4b5c6d7e8f90a1b2c3d4"
    key = SubmitIdempotencyKey(project_id=project_id, token=token)
    _put_idempotency_entry(
        key,
        SubmitResponse(branch_name="apme/remediate-ttl", commit_sha="sha", pr_url=None, provider="github"),
        SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-ttl"),
    )
    assert _get_idempotency_entry(key) is not None
    # Backdate past TTL then fetch: miss + eviction.
    _SUBMIT_IDEMPOTENCY_STORE[(project_id, token)].created_at = time.monotonic() - _SUBMIT_IDEMPOTENCY_TTL_S - 1.0
    assert _get_idempotency_entry(key) is None
    assert (project_id, token) not in _SUBMIT_IDEMPOTENCY_STORE


async def test_idempotency_lru_evicts_oldest() -> None:
    """Inserting past the cap evicts the oldest entries first (relative, #25)."""
    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        _SUBMIT_IDEMPOTENCY_MAX,
        _SUBMIT_IDEMPOTENCY_STORE,
        SubmitBinding,
        SubmitIdempotencyKey,
        _get_idempotency_entry,
        _put_idempotency_entry,
    )

    cap = _SUBMIT_IDEMPOTENCY_MAX
    for i in range(cap + 1):
        tok = f"{i:032x}"
        _put_idempotency_entry(
            SubmitIdempotencyKey(project_id="proj-lru", token=tok),
            SubmitResponse(branch_name=f"apme/b-{i}", commit_sha="sha", pr_url=None, provider="github"),
            SubmitBinding(branch_name=None, activity_id=None, scan_id=f"scan-{i}"),
        )
    assert len(_SUBMIT_IDEMPOTENCY_STORE) == cap
    assert _get_idempotency_entry(SubmitIdempotencyKey(project_id="proj-lru", token=f"{0:032x}")) is None
    assert _get_idempotency_entry(SubmitIdempotencyKey(project_id="proj-lru", token=f"{cap:032x}")) is not None


async def test_atomic_retry_replays_across_fresh_scan_id() -> None:
    """Same (project, token) with a fresh scan_id replays instead of 409."""
    import pytest as _pytest
    from fastapi import HTTPException as _HTTPException

    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        SubmitBinding,
        SubmitIdempotencyKey,
        _check_idempotency_binding,
        _put_idempotency_entry,
    )

    project_id = "proj-atomic-replay"
    token = "f60718293a4b5c6d7e8f90a1b2c3d4e5"
    _put_idempotency_entry(
        SubmitIdempotencyKey(project_id=project_id, token=token),
        SubmitResponse(
            branch_name="apme/remediate-x",
            commit_sha="sha-1",
            pr_url="https://github.com/org/repo/pull/9",
            provider="github",
        ),
        SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-old"),
    )
    from apme_gateway.api.submit_idempotency import _SUBMIT_IDEMPOTENCY_STORE as _store

    entry = _store[(project_id, token)]
    # Fresh atomic scan_id with matching branch/activity replays (no raise).
    _check_idempotency_binding(entry, SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-fresh"))
    # A different explicit branch still conflicts.
    with _pytest.raises(_HTTPException) as exc_info:
        _check_idempotency_binding(
            entry, SubmitBinding(branch_name="apme/other", activity_id=None, scan_id="scan-fresh")
        )
    assert exc_info.value.status_code == 409


async def test_expiry_then_db_replay(client: AsyncClient) -> None:
    """Expired cache entry still replays via the persisted submit_token.

    Args:
        client: Async HTTP test client.
    """
    import time

    from apme_gateway.api.submit_idempotency import (
        _SUBMIT_IDEMPOTENCY_STORE,
        _SUBMIT_IDEMPOTENCY_TTL_S,
    )

    await _setup_completed_operation(
        project_id="proj-submit-expire",
        scan_id="scan-submit-expire",
        operation_id="op-submit-expire",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-exp"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/9", branch_name="apme/remediate-x", provider="github"
        )

    token = "0718293a4b5c6d7e8f90a1b2c3d4e5f6"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-expire", "/submit"),
            json={"submit_token": token},
        )
        assert first.status_code == 200, first.text
        # Expire the in-memory entry (restart-like); DB row keeps the token.
        _SUBMIT_IDEMPOTENCY_STORE[("proj-submit-expire", token)].created_at = (
            time.monotonic() - _SUBMIT_IDEMPOTENCY_TTL_S - 1.0
        )
        second = await client.post(
            _op_url("proj-submit-expire", "/submit"),
            json={"submit_token": token},
        )
        assert second.status_code == 200, second.text
        assert second.json() == first.json()


async def test_branch_only_submit_replays_after_cache_loss(client: AsyncClient) -> None:
    """Branch-only (pr_url None) retries replay via DB token, not re-push.

    Args:
        client: Async HTTP test client.
    """
    from apme_gateway.api.submit_idempotency import clear_submit_idempotency_store

    await _setup_completed_operation(
        project_id="proj-submit-branchonly",
        scan_id="scan-submit-branchonly",
        operation_id="op-submit-branchonly",
    )
    push_calls = 0

    async def _fake_push(*args: object, **kwargs: object) -> str:
        nonlocal push_calls
        push_calls += 1
        return "sha-branchonly"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    token = "18293a4b5c6d7e8f90a1b2c3d4e5f607"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-branchonly", "/submit"),
            json={"submit_token": token, "create_pr": False},
        )
        assert first.status_code == 200, first.text
        assert first.json()["pr_url"] is None
        assert push_calls == 1
        # Simulate restart: drop the cache; the DB row keeps token+branch.
        clear_submit_idempotency_store()
        second = await client.post(
            _op_url("proj-submit-branchonly", "/submit"),
            json={"submit_token": token, "create_pr": False},
        )
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        assert push_calls == 1


async def test_concurrent_same_token_submit_pushes_once(client: AsyncClient) -> None:
    """Concurrent same-token submits serialize; the duplicate replays.

    Args:
        client: Async HTTP test client.
    """
    import asyncio as _asyncio

    await _setup_completed_operation(
        project_id="proj-submit-race",
        scan_id="scan-submit-race",
        operation_id="op-submit-race",
    )
    push_calls = 0

    async def _slow_push(*args: object, **kwargs: object) -> str:
        nonlocal push_calls
        push_calls += 1
        await _asyncio.sleep(0.2)
        return "sha-race"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        await _asyncio.sleep(0.05)
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/7", branch_name="apme/remediate-x", provider="github"
        )

    token = "293a4b5c6d7e8f90a1b2c3d4e5f60718"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_slow_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first_coro = client.post(_op_url("proj-submit-race", "/submit"), json={"submit_token": token})
        second_coro = client.post(_op_url("proj-submit-race", "/submit"), json={"submit_token": token})
        first, second = await _asyncio.gather(first_coro, second_coro)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json() == second.json()
        assert push_calls == 1


# ── POST /operate integration tests (hermetic; engine/driver/scm mocked) ──


async def _setup_operate_project(
    *,
    project_id: str = "proj-operate-1",
    repo_url: str = "https://github.com/org/repo.git",
) -> None:
    """Seed a project row for POST /operate tests.

    Args:
        project_id: Project UUID.
        repo_url: SCM clone URL.
    """
    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="operate-proj",
                repo_url=repo_url,
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token="tok",
                scm_provider="github",
            )
        )
        await db.commit()


def _fake_completed_drive() -> object:
    """Return a fake _drive_operation completing with patches.

    The fake seeds the scan/patched-file rows the embedded submit reads,
    then transitions the registry operation to ``COMPLETED``. Patch into
    ``apme_gateway.api.operation_router._drive_operation`` via ``patch``
    with ``new=`` by callers.

    Returns:
        Fake drive coroutine function.
    """

    async def _fake_drive(
        *,
        operation_id: str,
        project_id: str,
        scan_id: str,
        remediate: bool,
        options: object = None,
        **kwargs: object,
    ) -> None:
        """Complete the operation with one patched file.

        Args:
            operation_id: Registry operation identifier.
            project_id: Owning project UUID.
            scan_id: Engine scan identifier.
            remediate: Whether this is a remediation.
            options: Ignored drive options.
            **kwargs: Ignored engine/SCM plumbing.
        """
        _ = (options, kwargs)
        scan_type = "remediate" if remediate else "check"
        sess_id = f"sess-{scan_id[:8]}"
        async with get_session() as db:
            db.add(Session(session_id=sess_id, project_path="/proj", first_seen="t0", last_seen="t1"))
            db.add(
                Scan(
                    scan_id=scan_id,
                    session_id=sess_id,
                    project_id=project_id,
                    project_path="/proj",
                    source="gateway",
                    created_at="2026-01-01T00:00:00Z",
                    scan_type=scan_type,
                    total_violations=1,
                    fixed_count=1,
                )
            )
            if remediate:
                db.add(PatchedFile(scan_id=scan_id, path="a.yml", content=b"fixed\n"))
            await db.commit()
        registry = get_operation_registry()
        state = registry.get(operation_id)
        if state is not None:
            if remediate:
                state.result = OperationResult(total_violations=1, remediated_count=1, patches=[{"path": "a.yml"}])
            else:
                state.result = OperationResult(total_violations=1, remediated_count=0, patches=[])
            registry.transition(operation_id, OperationStatus.COMPLETED)

    return _fake_drive


def _operate_url(project_id: str) -> str:
    """Build the atomic operate URL.

    Args:
        project_id: Target project UUID.

    Returns:
        Atomic operate endpoint path.
    """
    return f"/api/v1/projects/{project_id}/operate"


async def test_operate_defaults_to_readonly_check(client: AsyncClient) -> None:
    """Bare POST /operate defaults to a read-only check with no submit.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-default")
    with (
        patch(
            "apme_gateway.api.operation_router._drive_operation",
            new=_fake_completed_drive(),
        ),
        patch("apme_gateway.config.load_config") as mock_cfg,
        patch("apme_gateway._galaxy_inject.load_galaxy_server_defs", new=AsyncMock(return_value=[])),
    ):
        mock_cfg.return_value = type(
            "Cfg",
            (),
            {
                "engine_address": "localhost:50051",
                "scm_token": "tok",
                "github_api_url": "https://api.github.com",
                "gitlab_api_url": "https://gitlab.example.com/api/v4",
                "bitbucket_api_url": "https://api.bitbucket.org/2.0",
            },
        )()
        resp = await client.post(_operate_url("proj-operate-default"), json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["scan_type"] == "check"


async def test_operate_tier_flags_accepted(client: AsyncClient) -> None:
    """Tier auto-approve flags are accepted and complete hermetically.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-tiers")
    with (
        patch(
            "apme_gateway.api.operation_router._drive_operation",
            new=_fake_completed_drive(),
        ),
        patch("apme_gateway.config.load_config") as mock_cfg,
        patch("apme_gateway._galaxy_inject.load_galaxy_server_defs", new=AsyncMock(return_value=[])),
    ):
        mock_cfg.return_value = type(
            "Cfg",
            (),
            {
                "engine_address": "localhost:50051",
                "scm_token": "tok",
                "github_api_url": "https://api.github.com",
                "gitlab_api_url": "https://gitlab.example.com/api/v4",
                "bitbucket_api_url": "https://api.bitbucket.org/2.0",
            },
        )()
        resp = await client.post(
            _operate_url("proj-operate-tiers"),
            json={
                "action": "remediate",
                "options": {"auto_approve_tier1": True, "auto_approve_ai": True, "enable_ai": True},
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


async def test_operate_embedded_submit_replays_on_idempotency_key(client: AsyncClient) -> None:
    """Two /operate calls with the same Idempotency-Key push only once.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-replay")
    push_calls = 0

    async def _fake_push(*args: object, **kwargs: object) -> str:
        nonlocal push_calls
        push_calls += 1
        return "sha-operate"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/11",
            branch_name="apme/remediate-operate",
            provider="github",
        )

    token = "3a4b5c6d7e8f90a1b2c3d4e5f6071829"
    with (
        patch(
            "apme_gateway.api.operation_router._drive_operation",
            new=_fake_completed_drive(),
        ),
        patch("apme_gateway.config.load_config") as mock_cfg,
        patch("apme_gateway._galaxy_inject.load_galaxy_server_defs", new=AsyncMock(return_value=[])),
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        mock_cfg.return_value = type(
            "Cfg",
            (),
            {
                "engine_address": "localhost:50051",
                "scm_token": "tok",
                "github_api_url": "https://api.github.com",
                "gitlab_api_url": "https://gitlab.example.com/api/v4",
                "bitbucket_api_url": "https://api.bitbucket.org/2.0",
            },
        )()
        payload = {
            "action": "remediate",
            "options": {
                "auto_approve_tier1": True,
                "submit": {"create_pr": True, "branch_name": "apme/remediate-operate"},
            },
        }
        headers = {"Idempotency-Key": token}
        first = await client.post(_operate_url("proj-operate-replay"), json=payload, headers=headers)
        assert first.status_code == 200, first.text
        second = await client.post(_operate_url("proj-operate-replay"), json=payload, headers=headers)
        assert second.status_code == 200, second.text
    assert push_calls == 1
    assert first.json().get("pr_url") == "https://github.com/org/repo/pull/11"
    assert second.json().get("pr_url") == "https://github.com/org/repo/pull/11"


async def test_operate_409_on_active_operation(client: AsyncClient) -> None:
    """POST /operate 409s while a non-terminal operation is active.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-busy")
    registry = get_operation_registry()
    registry.create(
        operation_id="op-operate-busy",
        project_id="proj-operate-busy",
        scan_id="scan-operate-busy",
        scan_type="check",
    )
    resp = await client.post(_operate_url("proj-operate-busy"), json={"action": "check"})
    assert resp.status_code == 409


async def test_operate_early_400_on_weak_token(client: AsyncClient) -> None:
    """Weak submit tokens fail fast with 400 before any operation starts.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-weak")
    resp = await client.post(
        _operate_url("proj-operate-weak"),
        json={"action": "check", "options": {"submit": {"submit_token": "tok-abc-123"}}},
    )
    assert resp.status_code == 400
    assert get_operation_registry().get_by_project("proj-operate-weak") is None


async def test_operate_early_422_on_bad_branch(client: AsyncClient) -> None:
    """Invalid submit branch names fail fast with 422 before any operation starts.

    Args:
        client: Async HTTP test client.
    """
    await _setup_operate_project(project_id="proj-operate-branch")
    resp = await client.post(
        _operate_url("proj-operate-branch"),
        json={"action": "check", "options": {"submit": {"branch_name": "../escape"}}},
    )
    assert resp.status_code == 422
    assert get_operation_registry().get_by_project("proj-operate-branch") is None


async def test_atomic_ai_escalation_groups_rule_ids() -> None:
    """Auto-escalation groups candidates by path with non-empty rule_ids."""
    import asyncio as _asyncio

    from apme_gateway.api.atomic_operate import _auto_drive_atomic_gates

    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-ai-group",
        project_id="proj-ai-group",
        scan_id="scan-ai-group",
        scan_type="remediate",
    )
    registry.transition(state.operation_id, OperationStatus.AWAITING_AI_TRIAGE)
    state.ai_triage_candidates = [
        {"path": "play.yml#task1", "rule_id": "L001"},
        {"path": "play.yml#task1", "rule_id": "L002"},
        {"path": "other.yml#task9", "rule_id": "L001"},
    ]
    loop = _asyncio.get_running_loop()
    state.escalate_ai_future = loop.create_future()
    driver = _asyncio.create_task(
        _auto_drive_atomic_gates(operation_id=state.operation_id, auto_approve_tier1=False, auto_approve_ai=True)
    )
    try:
        targets = await _asyncio.wait_for(_asyncio.shield(state.escalate_ai_future), timeout=5.0)
    finally:
        driver.cancel()
        import contextlib as _ctx

        with _ctx.suppress(_asyncio.CancelledError):
            await driver
    by_path = {t["path"]: t["rule_ids"] for t in targets}
    assert sorted(by_path["play.yml#task1"]) == ["L001", "L002"]
    assert by_path["other.yml#task9"] == ["L001"]
    assert all(t["rule_ids"] for t in targets)


async def test_conflict_scan_ignores_expired_entries(client: AsyncClient) -> None:
    """Expired entries are evicted by the conflict scan, not 409s.

    A same-scan re-submit with a fresh token and different branch must
    not hit ``idempotency_conflict`` from a TTL-dead entry; the dead
    row is evicted like the single-key path treats it as a miss.

    Args:
        client: Async HTTP test client.
    """
    import time

    from apme_gateway.api.submit_idempotency import (
        _SUBMIT_IDEMPOTENCY_STORE,
        _SUBMIT_IDEMPOTENCY_TTL_S,
    )
    from apme_gateway.scm.base import PullRequestResult

    await _setup_completed_operation(
        project_id="proj-submit-conflict-ttl",
        scan_id="scan-submit-conflict-ttl",
        operation_id="op-submit-conflict-ttl",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-conflict-ttl"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/9", branch_name="apme/remediate-x", provider="github"
        )

    token1 = "c3d4e5f60718293a4b5c6d7e8f90a1b2"
    token2 = "d4e5f60718293a4b5c6d7e8f90a1b2c3"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first = await client.post(
            _op_url("proj-submit-conflict-ttl", "/submit"),
            json={"submit_token": token1},
        )
        assert first.status_code == 200, first.text
        # Expire the stored entry (logically dead; single-key path misses).
        _SUBMIT_IDEMPOTENCY_STORE[("proj-submit-conflict-ttl", token1)].created_at = (
            time.monotonic() - _SUBMIT_IDEMPOTENCY_TTL_S - 1.0
        )
        second = await client.post(
            _op_url("proj-submit-conflict-ttl", "/submit"),
            json={"submit_token": token2, "branch_name": "apme/other"},
        )
        # The dead entry must not raise idempotency_conflict; the call
        # still 409s because the scan already has a PR (plain message).
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert isinstance(detail, str)
        assert "PR already created" in detail


# ── #4: patch-hash determinism, 409, lock timeout, mutex, equivalence ──


async def test_compute_patch_hash_determinism() -> None:
    """Patch hash is deterministic across shapes and orderings."""
    from apme_gateway.api.submit_idempotency import _compute_patch_hash

    assert _compute_patch_hash(None) is None
    assert _compute_patch_hash([]) is None

    dict_rows = [
        {"path": "b.yml", "content": "b-content\n"},
        {"path": "a.yml", "content": b"a-content\n"},
    ]

    class _Row:
        def __init__(self, path: str, content: bytes) -> None:
            self.path = path
            self.content = content

    obj_rows = [_Row("a.yml", b"a-content\n"), _Row("b.yml", b"b-content\n")]
    assert _compute_patch_hash(dict_rows) == _compute_patch_hash(obj_rows)

    reordered = [
        {"path": "a.yml", "content": b"a-content\n"},
        {"path": "b.yml", "content": "b-content\n"},
    ]
    assert _compute_patch_hash(dict_rows) == _compute_patch_hash(reordered)

    different = [
        {"path": "a.yml", "content": b"a-content-changed\n"},
        {"path": "b.yml", "content": "b-content\n"},
    ]
    assert _compute_patch_hash(dict_rows) != _compute_patch_hash(different)


async def test_patch_hash_mismatch_is_409() -> None:
    """Same token with a different patch set replays as 409."""
    import pytest as _pytest
    from fastapi import HTTPException as _HTTPException

    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        SubmitBinding,
        SubmitIdempotencyKey,
        _check_idempotency_binding,
        _put_idempotency_entry,
    )

    key = SubmitIdempotencyKey(project_id="proj-hash", token="a1b2c3d4e5f60718293a4b5c6d7e8f9012")
    _put_idempotency_entry(
        key,
        SubmitResponse(branch_name="apme/remediate-x", commit_sha="sha", pr_url=None, provider="github"),
        SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1", patch_hash="aaa"),
    )
    from apme_gateway.api.submit_idempotency import _SUBMIT_IDEMPOTENCY_STORE as _store

    entry = _store[("proj-hash", "a1b2c3d4e5f60718293a4b5c6d7e8f9012")]
    with _pytest.raises(_HTTPException) as exc_info:
        _check_idempotency_binding(
            entry, SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1", patch_hash="bbb")
        )
    assert exc_info.value.status_code == 409


async def test_patch_hash_replay_when_one_side_none() -> None:
    """One-sided hashes bind fail-closed: stored-only 409s, stored-None backfills (#13)."""
    import pytest as _pytest
    from fastapi import HTTPException as _HTTPException

    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        SubmitBinding,
        SubmitIdempotencyKey,
        _check_idempotency_binding,
        _put_idempotency_entry,
    )

    key = SubmitIdempotencyKey(project_id="proj-hash-none", token="b2c3d4e5f60718293a4b5c6d7e8f9012a3")
    _put_idempotency_entry(
        key,
        SubmitResponse(branch_name="apme/remediate-x", commit_sha="sha", pr_url=None, provider="github"),
        SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1", patch_hash=None),
    )
    from apme_gateway.api.submit_idempotency import _SUBMIT_IDEMPOTENCY_STORE as _store

    entry = _store[("proj-hash-none", "b2c3d4e5f60718293a4b5c6d7e8f9012a3")]
    # Stored None + incoming hash present: replays and binds the hash.
    _check_idempotency_binding(
        entry, SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1", patch_hash="abc")
    )
    assert entry.patch_hash == "abc"

    key2 = SubmitIdempotencyKey(project_id="proj-hash-none2", token="c3d4e5f60718293a4b5c6d7e8f9012a3b4")
    _put_idempotency_entry(
        key2,
        SubmitResponse(branch_name="apme/remediate-x", commit_sha="sha", pr_url=None, provider="github"),
        SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1", patch_hash="abc"),
    )
    entry2 = _store[("proj-hash-none2", "c3d4e5f60718293a4b5c6d7e8f9012a3b4")]
    # Stored hash + incoming None: 409 fail-closed (cannot prove same patch set).
    with _pytest.raises(_HTTPException) as exc_info:
        _check_idempotency_binding(entry2, SubmitBinding(branch_name=None, activity_id=None, scan_id="scan-1"))
    assert exc_info.value.status_code == 409


async def test_acquire_lock_timeout_503_with_retry_after() -> None:
    """A hung lock fails fast with 503 + Retry-After."""
    import asyncio as _asyncio

    import pytest as _pytest
    from fastapi import HTTPException as _HTTPException

    from apme_gateway.api.submit_idempotency import _acquire_lock_with_timeout

    lock = _asyncio.Lock()
    await lock.acquire()
    try:
        with _pytest.raises(_HTTPException) as exc_info:
            await _acquire_lock_with_timeout(lock, timeout=0.05)
        assert exc_info.value.status_code == 503
        assert exc_info.value.headers is not None
        assert "Retry-After" in exc_info.value.headers
    finally:
        lock.release()


async def test_cross_token_same_scan_serializes(client: AsyncClient) -> None:
    """Concurrent different-token submits for one scan push only once.

    Args:
        client: Async HTTP test client.
    """
    import asyncio as _asyncio

    await _setup_completed_operation(
        project_id="proj-submit-xtoken",
        scan_id="scan-submit-xtoken",
        operation_id="op-submit-xtoken",
    )
    push_calls = 0

    async def _slow_push(*args: object, **kwargs: object) -> str:
        nonlocal push_calls
        push_calls += 1
        await _asyncio.sleep(0.2)
        return "sha-xtoken"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/21", branch_name="apme/remediate-x", provider="github"
        )

    token1 = "d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
    token2 = "e5f60718293a4b5c6d7e8f90a1b2c3d4e5"
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_slow_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        first_coro = client.post(_op_url("proj-submit-xtoken", "/submit"), json={"submit_token": token1})
        second_coro = client.post(_op_url("proj-submit-xtoken", "/submit"), json={"submit_token": token2})
        first, second = await _asyncio.gather(first_coro, second_coro)
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 409], (first.text, second.text)
        assert push_calls == 1


async def test_submit_binding_equivalence() -> None:
    """Put/get roundtrip preserves the dataclass binding fields."""
    from apme_gateway.api.schemas import SubmitResponse
    from apme_gateway.api.submit_idempotency import (
        SubmitBinding,
        SubmitIdempotencyKey,
        _check_idempotency_binding,
        _get_idempotency_entry,
        _put_idempotency_entry,
    )

    key = SubmitIdempotencyKey(project_id="proj-equiv", token="f60718293a4b5c6d7e8f90a1b2c3d4e5f6")
    binding = SubmitBinding(
        branch_name="apme/branch-a",
        activity_id="scan-equiv",
        scan_id="scan-equiv",
        patch_hash="deadbeef",
    )
    response = SubmitResponse(branch_name="apme/branch-a", commit_sha="sha", pr_url=None, provider="github")
    _put_idempotency_entry(key, response, binding)
    entry = _get_idempotency_entry(key)
    assert entry is not None
    assert entry.response == response
    assert entry.branch_name == binding.branch_name
    assert entry.activity_id == binding.activity_id
    assert entry.scan_id == binding.scan_id
    assert entry.patch_hash == binding.patch_hash
    # An equal binding replays without raising.
    _check_idempotency_binding(
        entry,
        SubmitBinding(
            branch_name="apme/branch-a",
            activity_id="scan-equiv",
            scan_id="scan-other",
            patch_hash="deadbeef",
        ),
    )


async def test_restart_replay_cross_scan_no_second_push(client: AsyncClient) -> None:
    """Fresh scan_id + same token replays the stored PR after cache loss.

    Args:
        client: Async HTTP test client.
    """
    import re as _re

    from apme_gateway.api.submit_idempotency import clear_submit_idempotency_store

    project_id = "proj-submit-restart-xscan"
    old_scan = "scan-submit-restart-old"
    fresh_scan = "scan-submit-restart-fresh"
    token = "0718293a4b5c6d7e8f90a1b2c3d4e5f607"
    token_short = _re.sub(r"[^0-9a-f]", "", token)[:8]
    branch = f"apme/remediate-{token_short}"
    pr_url = "https://github.com/org/repo/pull/31"

    async with get_session() as db:
        db.add(
            Project(
                id=project_id,
                name="submit-restart-xscan",
                repo_url="https://github.com/org/repo.git",
                branch="main",
                created_at="2026-01-01T00:00:00Z",
                scm_token="tok",
                scm_provider="github",
            )
        )
        db.add(Session(session_id="sess-restart-xscan", project_path="/proj", first_seen="t0", last_seen="t1"))
        db.add(
            Scan(
                scan_id=old_scan,
                session_id="sess-restart-xscan",
                project_id=project_id,
                project_path="/proj",
                source="gateway",
                created_at="2026-01-01T00:00:00Z",
                scan_type="remediate",
                total_violations=1,
                fixed_count=1,
                branch_name=branch,
                commit_sha="sha-old",
                pr_url=pr_url,
                submit_token=token,
                scm_provider="github",
            )
        )
        db.add(PatchedFile(scan_id=old_scan, path="a.yml", content=b"fixed\n"))
        db.add(
            Scan(
                scan_id=fresh_scan,
                session_id="sess-restart-xscan",
                project_id=project_id,
                project_path="/proj",
                source="gateway",
                created_at="2026-01-02T00:00:00Z",
                scan_type="remediate",
                total_violations=1,
                fixed_count=1,
            )
        )
        db.add(PatchedFile(scan_id=fresh_scan, path="a.yml", content=b"fixed\n"))
        await db.commit()

    registry = get_operation_registry()
    state = registry.create(
        operation_id="op-submit-restart-xscan",
        project_id=project_id,
        scan_id=fresh_scan,
        scan_type="remediate",
    )
    registry.transition("op-submit-restart-xscan", OperationStatus.COMPLETED)
    state.result = OperationResult(total_violations=1, remediated_count=1, patches=[{"path": "a.yml"}])

    clear_submit_idempotency_store()
    create_branch_mock = AsyncMock(return_value="base-sha")
    push_mock = AsyncMock(return_value="sha-fresh")
    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=push_mock),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=create_branch_mock),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(
            _op_url(project_id, "/submit"),
            json={"submit_token": token},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pr_url"] == pr_url
        assert body["branch_name"] == branch
        create_branch_mock.assert_not_called()
        push_mock.assert_not_called()


async def test_uppercase_token_keeps_deterministic_branch(client: AsyncClient) -> None:
    """Uppercase hex tokens derive the same auto-branch as lowercase (#N1).

    Args:
        client: Async HTTP test client.
    """
    await _setup_completed_operation(
        project_id="proj-submit-upper",
        scan_id="scan-submit-upper",
        operation_id="op-submit-upper",
    )

    async def _fake_push(*args: object, **kwargs: object) -> str:
        return "sha-upper"

    async def _fake_branch(*args: object, **kwargs: object) -> str:
        return "base-sha"

    from apme_gateway.scm.base import PullRequestResult

    async def _fake_pr(*args: object, **kwargs: object) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/org/repo/pull/9", branch_name="apme/remediate-x", provider="github"
        )

    with (
        patch("apme_gateway.scm.github.GitHubProvider.push_files", new=_fake_push),
        patch("apme_gateway.scm.github.GitHubProvider.create_branch", new=_fake_branch),
        patch("apme_gateway.scm.github.GitHubProvider.create_pull_request", new=_fake_pr),
        patch("apme_gateway.scm.github.GitHubProvider.branch_head_sha", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(
            _op_url("proj-submit-upper", "/submit"),
            json={},
            headers={"Idempotency-Key": "B2C3D4E5F60718293A4B5C6D7E8F90A1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["branch_name"] == "apme/remediate-b2c3d4e5"
