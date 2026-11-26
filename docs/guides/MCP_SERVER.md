# APME MCP server

APME now includes `apme-mcp`, a thin Model Context Protocol adapter over the
existing Gateway REST API. It exposes project setup, Galaxy configuration,
scan monitoring, findings, remediation approvals, AI escalation, cancellation,
and optional SCM pull-request submission.

## Architecture

```text
AI agent / MCP host
        │ stdio
        ▼
apme-mcp (Python, asyncio, MCP SDK)
        │ REST /api/v1 + operation SSE
        ▼
APME Gateway (:8080)
        │ gRPC FixSession / Reporting
        ▼
Engine + Native + OPA + Ansible + Galaxy Proxy
        │
        ├── optional Gitleaks, Collection Health, Dep Audit
        └── PostgreSQL persistence and SCM operations owned by Gateway
```

MCP is protocol glue. Agent performs reasoning. Gateway owns authorization,
persistence, checkout, Engine orchestration, remediation state, and SCM work.
MCP never talks directly to PostgreSQL, Engine gRPC, SCM, or local project
files. MCP does not copy scan content locally; Gateway manages checkout and
scan workspace.

Implementation:

- Python package: `src/apme_mcp/`
- Async I/O: `asyncio` and `httpx`
- MCP transport: stdio in current version
- Gateway API: versioned REST `/api/v1`
- Long-running operations: Gateway operation SSE events
- Entry point: `apme-mcp = apme_mcp.server:main`

## Scope

The adapter does not access PostgreSQL, Engine gRPC, SCM credentials, or the
filesystem directly. Gateway remains owner of persistence, source checkout,
Engine orchestration, approval policy, and SCM operations.

Default Gateway URL:

```text
http://127.0.0.1:8080
```

Override with `APME_GATEWAY_URL`.

Client accepts loopback Gateway URLs only. Remote/shared Gateway deployment is
unsupported until an authenticated MCP process boundary exists.

## Run locally

With project dependencies installed:

```bash
APME_GATEWAY_URL=http://127.0.0.1:8080 uv run apme-mcp
```

`apme-mcp` uses stdio transport. Configure it in an MCP client as a local
command. Keep logs on stderr; stdout is reserved for MCP protocol traffic.

Example local agent configuration:

```json
{
  "mcpServers": {
    "apme": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/apme",
        "apme-mcp"
      ],
      "env": {
        "APME_GATEWAY_URL": "http://127.0.0.1:8080",
        "APME_MCP_LOG_LEVEL": "WARNING"
      }
    }
  }
}
```

Use absolute checkout path. Start rootless local APME stack first. Keep
Gateway and MCP on same user-owned machine for current supported setup.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APME_GATEWAY_URL` | `http://127.0.0.1:8080` | Gateway base URL. |
| `APME_MCP_LOG_LEVEL` | `WARNING` | stderr logging level. Never put secrets in logs. |

## Tool inventory

Project and Galaxy: `list_projects`, `get_project`, `create_project`,
`list_galaxy_servers`, `create_galaxy_server`.

Scan and evidence: `start_project_scan`, `get_operation`,
`wait_for_operation`, `get_project_violations`, `get_project_activity`,
`get_project_dependencies`, `get_project_sbom`, `get_project_graph`.

Remediation: `start_remediation`, `begin_remediate`, `get_proposals`,
`update_draft_proposals`, `approve_proposals`, `escalate_ai`,
`cancel_operation`, `submit_remediation`.

Write-capable tools require explicit agent/user intent. `submit_remediation`
creates a pull request only when `create_pr=true`.

## Scan workflow

1. `list_projects`
2. `create_project` if needed
3. `list_galaxy_servers` and `create_galaxy_server` if collection resolution
   needs configuration
4. `start_project_scan`
5. `wait_for_operation` or `get_operation`
6. `get_project_violations`, `get_project_activity`,
   `get_project_dependencies`, `get_project_sbom`, and `get_project_graph`

`wait_for_operation` listens to Gateway operation SSE and returns when Gateway
reports `completed`, `failed`, `cancelled`, or `pr_submitted`. Timeout returns
the current snapshot and never cancels the operation.

`get_project_violations` accepts `limit` (1-500, default 50) and `offset`
(default 0) for paging large result sets. Repeat calls with increasing offsets
to retrieve all findings.

## Remediation workflow

1. `start_remediation`
2. `begin_remediate`
3. `get_proposals` (waits for `ProposalsReady`; returns `proposals_pending=true` if timeout expires)
4. `update_draft_proposals` for working-set changes
5. `approve_proposals` with explicit proposal IDs and any required approval gate
6. `escalate_ai` with explicit paths and rule IDs when needed
7. `wait_for_operation`
8. `submit_remediation` with `create_pr=true` only when pull-request creation is
   explicitly intended

The adapter never infers approval, silently approves omitted proposals, or
creates a pull request by default.

## Data flow

Scan:

1. MCP calls Gateway `POST /api/v1/projects/{project_id}/operation`.
2. Gateway obtains project source and prepares scan state.
3. Gateway opens Engine `FixSession`.
4. Engine loads project and invokes validators over gRPC.
5. Gateway persists operation, findings, dependencies, and evidence.
6. MCP receives operation status through Gateway SSE and reads results through
   Gateway REST.

Remediation follows same path, with proposal review and explicit approval
before Engine applies transforms. Gateway creates branch/PR only after
`submit_remediation` requests it.

## Credential handling

Credential fields are accepted only where Gateway setup routes require them.
Gateway errors redact token-shaped fields before returning diagnostics. Do not
place credentials in tool descriptions, project names, branch names, logs, or
client configuration committed to source control.

## Current limitations

- Transport is stdio. Streamable HTTP deployment needs a separate authenticated
  process boundary.
- Current MCP layer has no per-user authentication, client inventory, central
  audit sink, rate limiter, or circuit breaker. Local process isolation is not
  equivalent to shared-service authorization.
- Tool schemas mirror current Gateway routes; Gateway remains versioned public
  contract under `/api/v1`.
- No delete-project tool is exposed in this first version.
- End-to-end verification requires a running Gateway and its configured APME
  stack. Unit tests cover client errors, redaction, path validation, and tool
  registration without requiring live services.

## Repository map

- [`src/apme_mcp/server.py`](../../src/apme_mcp/server.py): MCP tool definitions
  and stdio entry point.
- [`src/apme_mcp/gateway_client.py`](../../src/apme_mcp/gateway_client.py):
  async REST/SSE client, URL policy, error redaction.
- [`tests/mcp`](../../tests/mcp): MCP adapter tests.

Open-source upstream status does not itself grant production approval. Classify
controls by deployment: local developer test, Red Hat-managed internal,
external-facing, or hybrid. Complete required compliance review separately
before production deployment.

## Git and contribution notes

Keep MCP changes reviewable and narrow:

1. Change tool schemas and behavior in `src/apme_mcp/server.py`.
2. Change Gateway transport, URL policy, timeout, or redaction behavior in
   `src/apme_mcp/gateway_client.py`.
3. Add or update tests under `tests/mcp/` for every behavior change.
4. Update this guide when tool scope, configuration, transport, or safety
   behavior changes.
5. Record required compliance impact separately when connected systems, data
   exposure, write actions, authentication, or deployment model changes.

Never commit Gateway credentials, SCM tokens, local absolute paths, scan
outputs containing secrets, or MCP client state. Review staged diff before
push. Use normal project branch and PR controls; this documentation does not
authorize registration, deployment, or production Gateway access.

## Verification

```bash
uvx --with-requirements ci/tox-requirements.txt tox -e unit -- --no-cov tests/mcp
uvx --with-requirements ci/tox-requirements.txt tox -e lint
```

The full lint hook also reports pre-existing repository-wide dependency and
skillmark issues when its isolated environment lacks project extras. Those are
separate from MCP adapter tests.
