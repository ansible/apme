# APME MCP server

APME now includes `apme-mcp`, a thin Model Context Protocol adapter over the
existing Gateway REST API. It exposes project setup, Galaxy configuration,
scan monitoring, findings, remediation approvals, AI escalation, cancellation,
and optional SCM pull-request submission.

## Scope

The adapter does not access PostgreSQL, Engine gRPC, SCM credentials, or the
filesystem directly. Gateway remains owner of persistence, source checkout,
Engine orchestration, approval policy, and SCM operations.

Default Gateway URL:

```text
http://127.0.0.1:8080
```

Override with `APME_GATEWAY_URL`.

## Run locally

With project dependencies installed:

```bash
APME_GATEWAY_URL=http://127.0.0.1:8080 apme-mcp
```

`apme-mcp` uses stdio transport. Configure it in an MCP client as a local
command. Keep logs on stderr; stdout is reserved for MCP protocol traffic.

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

## Remediation workflow

1. `start_remediation`
2. `begin_remediate`
3. `get_proposals`
4. `update_draft_proposals` for working-set changes
5. `approve_proposals` with explicit proposal IDs and any required approval gate
6. `escalate_ai` with explicit paths and rule IDs when needed
7. `wait_for_operation`
8. `submit_remediation` with `create_pr=true` only when pull-request creation is
   explicitly intended

The adapter never infers approval, silently approves omitted proposals, or
creates a pull request by default.

## Credential handling

Credential fields are accepted only where Gateway setup routes require them.
Gateway errors redact token-shaped fields before returning diagnostics. Do not
place credentials in tool descriptions, project names, branch names, logs, or
client configuration committed to source control.

## Current limitations

- Transport is stdio. Streamable HTTP deployment needs a separate authenticated
  process boundary.
- Tool schemas mirror current Gateway routes; Gateway remains versioned public
  contract under `/api/v1`.
- No delete-project tool is exposed in this first version.
- End-to-end verification requires a running Gateway and its configured APME
  stack. Unit tests cover client errors, redaction, path validation, and tool
  registration without requiring live services.

## Verification

```bash
uvx --with-requirements ci/tox-requirements.txt tox -e unit -- --no-cov tests/mcp
uvx --with-requirements ci/tox-requirements.txt tox -e lint
```

The full lint hook also reports pre-existing repository-wide dependency and
skillmark issues when its isolated environment lacks project extras. Those are
separate from MCP adapter tests.
