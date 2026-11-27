# APME MCP Server Implementation Plan

> Status: implemented on `codex/mcp-server`. This plan records delivered
> scope; future transport and typed-model work remains follow-up.

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
- Create: `src/apme_mcp/server.py`

- [x] Add the official `mcp` SDK as a direct dependency and expose an `apme-mcp` console entry point.
- [x] Create a guarded server entry point that reads `APME_GATEWAY_URL` and runs stdio transport.
- [x] Keep server startup logging on stderr; stdout remains MCP transport.
- [x] Add a smoke test that imports the package and confirms server tool registration.
- [x] Run focused MCP unit tests through tox.

### Task 2: Implement Gateway HTTP client and error model

**Files:**
- Create: `src/apme_mcp/gateway_client.py`
- Create: `tests/mcp/test_gateway_client.py`

- [x] Implement async request helpers for JSON `GET`, `POST`, and `PATCH` calls.
- [x] Implement stable exception containing HTTP status, endpoint, and sanitized detail.
- [x] Redact credential-shaped fields and discard non-JSON error bodies.
- [x] Add Gateway path and request helpers used by project, operation, findings, proposal, settings, and submit tools.
- [x] Test successful request serialization, non-JSON errors, HTTP errors, path safety, and redaction.
- [x] Run focused MCP unit tests through tox.

### Task 3: Implement project, settings, scan, and result tools

**Files:**
- Modify: `src/apme_mcp/server.py`
- Modify: `tests/mcp/test_server.py`

- [x] Register tools for project listing, lookup, creation, Galaxy server listing/creation, scan start, operation snapshot, and persisted results.
- [x] Return operation IDs and status without waiting inside start tools.
- [x] Keep Galaxy credential fields write-only and exclude them from returned structures.
- [x] Test tool registration, payloads, validation, and Gateway request paths.
- [x] Run focused tests through tox.

### Task 4: Implement SSE operation waiting

**Files:**
- Modify: `src/apme_mcp/server.py`
- Modify: `tests/mcp/test_server.py`

- [x] Consume `GET /api/v1/projects/{project_id}/operation/events` as SSE.
- [x] Return latest operation snapshot on timeout or disconnect without cancelling the Gateway operation.
- [x] Return terminal result on terminal status and PR-created events.
- [x] Handle an already-terminal operation through the initial SSE snapshot.
- [x] Test event parsing, terminal completion, proposal readiness, and malformed event handling.
- [x] Run focused tests through tox.

### Task 5: Implement remediation and SCM tools

**Files:**
- Modify: `src/apme_mcp/server.py`
- Modify: `tests/mcp/test_server.py`

- [x] Register remediation start, begin-remediate, proposal retrieval, draft updates, approval, AI escalation, and cancellation tools.
- [x] Require explicit approved proposal IDs.
- [x] Require explicit AI target list.
- [x] Register submit tool with explicit branch and `create_pr` parameters.
- [x] Test request payloads and ensure no implicit approval or PR creation.
- [x] Run focused tests through tox.

### Task 6: Documentation and verification

**Files:**
- Create: `docs/guides/MCP_SERVER.md`
- Modify: `README.md` only if a short MCP entry point is appropriate.
- Modify: `tox.ini` only if a dedicated MCP verification environment is needed.

- [x] Document local stdio configuration, Gateway URL, tool groups, safety semantics, and scan/remediation workflows.
- [x] Run `tox -e lint` (Ruff, format, uv-lock, OpenAPI passed; repository mypy/pydoclint/skillmark environment checks failed).
- [x] Run focused MCP tests through tox: 15 passed.
- [ ] Run relevant existing Gateway tests with PostgreSQL available.
- [x] Review diff for secrets, direct DB/Engine access, and accidental public API changes.
