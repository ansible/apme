from __future__ import annotations

# Tests use names as specifications; per-test docstrings add no signal.
# ruff: noqa: D100,D103
from typing import Any

import pytest

from apme_mcp import server


def test_server_registers_core_tools() -> None:
    assert callable(server.start_project_scan)
    assert callable(server.start_remediation)
    assert callable(server.approve_proposals)
    assert callable(server.submit_remediation)


@pytest.mark.asyncio
async def test_tools_cover_project_and_remediation_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, path, payload))
        if path.endswith("/operation") and method == "GET":
            return {"operation_id": "op-1", "status": "completed", "proposals": [{"id": "p-1"}]}
        return {"ok": True}

    monkeypatch.setattr(server, "_call", fake_call)

    await server.list_projects()
    await server.get_project("project-1")
    await server.create_project("demo", "https://git.example/demo", scm_provider="github")
    await server.list_galaxy_servers()
    await server.create_galaxy_server("community", "https://galaxy.example", validate_certs=True)
    await server.start_project_scan("project-1", {"profile": "full"})
    await server.start_remediation("project-1")
    await server.get_operation("project-1")
    await server.get_project_violations("project-1")
    await server.get_project_activity("project-1")
    await server.get_project_dependencies("project-1")
    await server.get_project_sbom("project-1")
    await server.get_project_graph("project-1")
    assert await server.get_proposals("project-1") == {
        "operation_id": "op-1",
        "status": "completed",
        "proposals": [{"id": "p-1"}],
    }
    await server.begin_remediate("project-1")
    await server.approve_proposals("project-1", ["p-1"], "gate-1")
    await server.update_draft_proposals("project-1", [{"proposal_id": "p-1", "status": "approved"}])
    await server.escalate_ai("project-1", [{"path": "play.yml", "rule_ids": ["L001"]}])
    await server.cancel_operation("project-1")
    await server.submit_remediation("project-1", branch_name="apme/fix", create_pr=True, title="Apply fixes")

    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "/api/v1/projects"),
        ("GET", "/api/v1/projects/project-1"),
        ("POST", "/api/v1/projects"),
        ("GET", "/api/v1/settings/galaxy-servers"),
        ("POST", "/api/v1/settings/galaxy-servers"),
        ("POST", "/api/v1/projects/project-1/operation"),
        ("POST", "/api/v1/projects/project-1/operation"),
        ("GET", "/api/v1/projects/project-1/operation"),
        ("GET", "/api/v1/projects/project-1/violations"),
        ("GET", "/api/v1/projects/project-1/activity"),
        ("GET", "/api/v1/projects/project-1/dependencies"),
        ("GET", "/api/v1/projects/project-1/sbom"),
        ("GET", "/api/v1/projects/project-1/graph"),
        ("GET", "/api/v1/projects/project-1/operation"),
        ("POST", "/api/v1/projects/project-1/operation/begin-remediate"),
        ("POST", "/api/v1/projects/project-1/operation/approve"),
        ("PATCH", "/api/v1/projects/project-1/operation/proposals"),
        ("POST", "/api/v1/projects/project-1/operation/escalate-ai"),
        ("POST", "/api/v1/projects/project-1/operation/cancel"),
        ("POST", "/api/v1/projects/project-1/operation/submit"),
    ]
    assert calls[2][2] == {
        "name": "demo",
        "repo_url": "https://git.example/demo",
        "branch": "main",
        "scm_provider": "github",
    }
    assert calls[4][2] == {
        "name": "community",
        "url": "https://galaxy.example",
        "token": "",
        "auth_url": "",
        "validate_certs": True,
    }
    assert calls[15][2] == {"approved_ids": ["p-1"], "approval_gate_id": "gate-1"}
    assert calls[19][2] == {"create_pr": True, "branch_name": "apme/fix", "title": "Apply fixes"}


@pytest.mark.asyncio
async def test_wait_for_operation_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="between 1 and 600"):
        await server.wait_for_operation("project-1", 0)


@pytest.mark.asyncio
async def test_wait_for_operation_returns_terminal_sse_event(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        is_error = False

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def aiter_lines(self) -> Any:
            for line in (
                "event: status_changed",
                'data: {"status":"completed","operation_id":"op-1"}',
                "",
            ):
                yield line

    class FakeGateway:
        client = None

        def __init__(self) -> None:
            self.client = self

        async def __aenter__(self) -> FakeGateway:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(server, "GatewayClient", FakeGateway)

    assert await server.wait_for_operation("project-1", 5) == {
        "event": "status_changed",
        "terminal": True,
        "operation": {"status": "completed", "operation_id": "op-1"},
    }


@pytest.mark.asyncio
async def test_wait_for_operation_handles_non_json_sse_and_nonterminal_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        is_error = False

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def aiter_lines(self) -> Any:
            for line in ("data: not-json", ""):
                yield line

    class FakeGateway:
        client = None

        def __init__(self) -> None:
            self.client = self

        async def __aenter__(self) -> FakeGateway:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

        async def get(self, path: str) -> dict[str, str]:
            return {"status": "running", "path": path}

    monkeypatch.setattr(server, "GatewayClient", FakeGateway)

    assert await server.wait_for_operation("project-1", 5) == {
        "terminal": False,
        "operation": {"status": "running", "path": "/api/v1/projects/project-1/operation"},
    }
