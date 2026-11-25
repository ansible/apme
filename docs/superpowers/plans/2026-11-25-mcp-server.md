# APME MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed MCP server that drives APME project scan and remediation workflows through the existing Gateway REST API.

**Architecture:** Add a stateless Python MCP adapter under `src/apme_mcp/`. Tools call Gateway `/api/v1` routes through `httpx`; long-running waits consume the Gateway operation SSE stream. The adapter never accesses Engine gRPC or PostgreSQL directly.

**Tech Stack:** Python 3.12, official MCP Python SDK, `httpx`, Pydantic models, pytest/pytest-asyncio, tox quality gates.

**Spec:** `docs/superpowers/specs/2026-11-25-mcp-server-design.md`

## Global Constraints

- Preserve Gateway as owner of FixSession, persistence, SCM, approvals, and credentials.
- No implicit proposal approval, AI escalation, branch push, PR creation, or project deletion.
- Redact credential inputs from errors and logs.
- Use tox for all lint and test commands.
- Do not modify public Gateway REST schemas.

---

### Task 1: Add MCP dependency and package entry point

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/apme_mcp/__init__.py`
- Create: `src/apme_mcp/__main__.py`
- Create: `src/apme_mcp/server.py`

- [ ] Add the official `mcp` SDK as a direct dependency and expose an `apme-mcp` console entry point.
- [ ] Create a guarded server entry point that reads `APME_GATEWAY_URL` and runs stdio transport by default.
- [ ] Keep server startup logging on stderr; stdout remains MCP transport.
- [ ] Add a smoke test that imports the package and confirms server tool registration.
- [ ] Run `tox -e unit -- tests/mcp/test_server.py`.

### Task 2: Implement Gateway HTTP client and error model

**Files:**
- Create: `src/apme_mcp/gateway_client.py`
- Create: `tests/mcp/test_gateway_client.py`

- [ ] Implement async request helpers for JSON `GET`, `POST`, `PATCH`, and `DELETE` calls.
- [ ] Implement stable exception containing HTTP status, endpoint, and sanitized detail.
- [ ] Redact values for fields named `token`, `scm_token`, `requirements_token`, `password`, `secret`, and `api_key`.
- [ ] Add typed helpers for project, operation, findings, proposals, settings, and submit routes.
- [ ] Test successful request serialization, non-JSON errors, HTTP errors, timeout, and redaction.
- [ ] Run the focused unit tests through tox.

### Task 3: Implement project, settings, scan, and result tools

**Files:**
- Create: `src/apme_mcp/tools/projects.py`
- Create: `src/apme_mcp/tools/scans.py`
- Create: `src/apme_mcp/tools/results.py`
- Create: `tests/mcp/test_project_tools.py`
- Create: `tests/mcp/test_scan_tools.py`

- [ ] Register typed tools for project listing, lookup, creation, Galaxy server listing/creation, scan start, operation snapshot, and persisted results.
- [ ] Return operation IDs and status without waiting inside start tools.
- [ ] Keep Galaxy credential fields write-only and exclude them from returned structures.
- [ ] Test tool input/output schemas and Gateway request paths.
- [ ] Run focused tests through tox.

### Task 4: Implement SSE operation waiting

**Files:**
- Create: `src/apme_mcp/operation_wait.py`
- Modify: `src/apme_mcp/tools/scans.py`
- Create: `tests/mcp/test_operation_wait.py`

- [ ] Consume `GET /api/v1/projects/{project_id}/operation/events` as SSE.
- [ ] Return latest operation snapshot on timeout or disconnect without cancelling the Gateway operation.
- [ ] Return terminal result on `completed`, `failed`, or `cancelled`.
- [ ] Handle an already-terminal operation through the initial SSE snapshot.
- [ ] Test event parsing, terminal completion, timeout, disconnect, and malformed event handling.
- [ ] Run focused tests through tox.

### Task 5: Implement remediation and SCM tools

**Files:**
- Create: `src/apme_mcp/tools/remediation.py`
- Create: `src/apme_mcp/tools/scm.py`
- Create: `tests/mcp/test_remediation_tools.py`
- Create: `tests/mcp/test_scm_tools.py`

- [ ] Register remediation start, begin-remediate, proposal retrieval, draft updates, approval, AI escalation, and cancellation tools.
- [ ] Require explicit approved proposal IDs.
- [ ] Require explicit AI target list.
- [ ] Register submit tool with explicit branch and `create_pr` parameters.
- [ ] Test request payloads and ensure no implicit approval or PR creation.
- [ ] Run focused tests through tox.

### Task 6: Documentation and verification

**Files:**
- Create: `docs/guides/MCP_SERVER.md`
- Modify: `README.md` only if a short MCP entry point is appropriate.
- Modify: `tox.ini` only if a dedicated MCP verification environment is needed.

- [ ] Document local stdio configuration, Gateway URL, tool groups, safety semantics, and a scan/remediation example.
- [ ] Run `tox -e lint`.
- [ ] Run `tox -e unit -- tests/mcp`.
- [ ] Run relevant existing Gateway tests.
- [ ] Run `tox -e openapi -- --check` to verify no Gateway contract drift.
- [ ] Review diff for secrets, direct DB/Engine access, and accidental public API changes.
