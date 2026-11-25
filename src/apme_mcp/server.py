"""APME MCP server backed by the Gateway REST API."""  # noqa: DOC101,DOC103,DOC201,DOC501,DOC503

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import MCPServer

from apme_mcp.gateway_client import GatewayClient, project_path

logger = logging.getLogger(__name__)
mcp = MCPServer("apme")


async def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    async with GatewayClient() as gateway:
        return await gateway.request(method, path, json=payload) if payload is not None else await gateway.get(path)


@mcp.tool()
async def list_projects() -> Any:  # noqa: DOC201
    """List projects registered in APME."""
    return await _call("GET", "/api/v1/projects")


@mcp.tool()
async def get_project(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get one project by ID."""
    return await _call("GET", project_path(project_id))


@mcp.tool()
async def create_project(  # noqa: DOC101,DOC103,DOC201
    name: str,
    repo_url: str,
    branch: str = "main",
    scm_provider: str | None = None,
    scm_token: str | None = None,
    requirements_token: str | None = None,
) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Create a repository-backed project; credentials are never returned."""
    payload: dict[str, Any] = {"name": name, "repo_url": repo_url, "branch": branch}
    for key, value in (("scm_provider", scm_provider), ("scm_token", scm_token), ("requirements_token", requirements_token)):
        if value is not None:
            payload[key] = value
    return await _call("POST", "/api/v1/projects", payload)


@mcp.tool()
async def list_galaxy_servers() -> Any:  # noqa: DOC201
    """List configured Galaxy servers."""
    return await _call("GET", "/api/v1/settings/galaxy-servers")


@mcp.tool()
async def create_galaxy_server(  # noqa: DOC101,DOC103,DOC201
    name: str,
    url: str,
    token: str = "",
    auth_url: str = "",
    validate_certs: bool | None = None,
) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Add a Galaxy server; token is accepted for setup but never returned."""
    payload: dict[str, Any] = {"name": name, "url": url, "token": token, "auth_url": auth_url}
    if validate_certs is not None:
        payload["validate_certs"] = validate_certs
    return await _call("POST", "/api/v1/settings/galaxy-servers", payload)


@mcp.tool()
async def start_project_scan(project_id: str, options: dict[str, Any] | None = None) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Start asynchronous check scan for a project."""
    return await _call("POST", project_path(project_id, "/operation"), {"action": "check", "options": options or {}})


@mcp.tool()
async def start_remediation(project_id: str, options: dict[str, Any] | None = None) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Start asynchronous remediation; does not approve proposals."""
    return await _call("POST", project_path(project_id, "/operation"), {"action": "remediate", "options": options or {}})


@mcp.tool()
async def get_operation(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Read current operation snapshot for a project."""
    return await _call("GET", project_path(project_id, "/operation"))


@mcp.tool()
async def wait_for_operation(project_id: str, timeout_seconds: int = 30) -> Any:  # noqa: DOC101,DOC103,DOC201,DOC501,DOC503
    """Wait on Gateway SSE until operation terminal or timeout; never cancels it."""
    if timeout_seconds < 1 or timeout_seconds > 600:
        raise ValueError("timeout_seconds must be between 1 and 600")
    path = project_path(project_id, "/operation/events")
    async with GatewayClient() as gateway:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with gateway.client.stream("GET", path, headers={"Accept": "text/event-stream"}) as response:
                    if response.is_error:
                        raise RuntimeError(f"Gateway operation stream returned HTTP {response.status_code}")
                    event_name = "message"
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            raw = "\n".join(data_lines)
                            data_lines = []
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                payload = {"raw": raw}
                            if event_name in {"result", "status_changed"} and payload.get("status") in {
                                "completed", "failed", "cancelled", "pr_submitted"
                            }:
                                return {"event": event_name, "terminal": True, "operation": payload}
                            event_name = "message"
        except TimeoutError:
            snapshot = await gateway.get(project_path(project_id, "/operation"))
            return {"terminal": False, "timed_out": True, "operation": snapshot}
    return {"terminal": False, "operation": await _call("GET", project_path(project_id, "/operation"))}


@mcp.tool()
async def get_project_violations(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get findings for the latest project scan."""
    return await _call("GET", project_path(project_id, "/violations"))


@mcp.tool()
async def get_project_activity(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get persisted scan and remediation activity for a project."""
    return await _call("GET", project_path(project_id, "/activity"))


@mcp.tool()
async def get_project_dependencies(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get latest collection, Python package, and ansible-core dependencies."""
    return await _call("GET", project_path(project_id, "/dependencies"))


@mcp.tool()
async def get_project_sbom(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get latest project SBOM data."""
    return await _call("GET", project_path(project_id, "/sbom"))


@mcp.tool()
async def get_project_graph(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get latest project content graph."""
    return await _call("GET", project_path(project_id, "/graph"))


@mcp.tool()
async def get_proposals(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Get current remediation proposals."""
    snapshot = await _call("GET", project_path(project_id, "/operation"))
    return {"operation_id": snapshot.get("operation_id"), "status": snapshot.get("status"), "proposals": snapshot.get("proposals", [])}


@mcp.tool()
async def begin_remediate(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Advance an assessed operation into remediation."""
    return await _call("POST", project_path(project_id, "/operation/begin-remediate"), {})


@mcp.tool()
async def approve_proposals(project_id: str, approved_ids: list[str], approval_gate_id: str | None = None) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Approve exactly supplied proposal IDs; never auto-approves omitted proposals."""
    payload: dict[str, Any] = {"approved_ids": approved_ids}
    if approval_gate_id is not None:
        payload["approval_gate_id"] = approval_gate_id
    return await _call("POST", project_path(project_id, "/operation/approve"), payload)


@mcp.tool()
async def update_draft_proposals(project_id: str, updates: list[dict[str, str]]) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Update proposal working-set statuses without applying fixes."""
    return await _call("PATCH", project_path(project_id, "/operation/proposals"), {"updates": updates})


@mcp.tool()
async def escalate_ai(project_id: str, targets: list[dict[str, Any]]) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Send explicitly selected paths and rule IDs to AI remediation."""
    return await _call("POST", project_path(project_id, "/operation/escalate-ai"), {"targets": targets})


@mcp.tool()
async def cancel_operation(project_id: str) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Cancel the active project operation."""
    return await _call("POST", project_path(project_id, "/operation/cancel"), {})


@mcp.tool()
async def submit_remediation(  # noqa: DOC101,DOC103,DOC201
    project_id: str,
    branch_name: str | None = None,
    create_pr: bool = False,
    title: str | None = None,
    body: str | None = None,
) -> Any:  # noqa: DOC101,DOC103,DOC201
    """Submit completed remediation to SCM; PR creation is opt-in."""
    payload: dict[str, Any] = {"create_pr": create_pr}
    for key, value in (("branch_name", branch_name), ("title", title), ("body", body)):
        if value is not None:
            payload[key] = value
    return await _call("POST", project_path(project_id, "/operation/submit"), payload)


def main() -> None:
    """Run stdio MCP transport."""
    logging.basicConfig(level=os.getenv("APME_MCP_LOG_LEVEL", "WARNING"), stream=__import__("sys").stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
