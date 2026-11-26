# APME MCP Server Design

## Status

Approved for implementation on branch `codex/mcp-server`.

## Goal

Expose APME project scanning and remediation workflows through Model Context Protocol (MCP) while preserving Gateway ownership of persistence, authorization, SCM, approval gates, and Engine `FixSession` orchestration.

## Scope

The MCP server covers:

- Project creation and lookup.
- Galaxy server discovery/configuration through existing Gateway routes.
- Check scans.
- Operation status and progress waiting.
- Findings and proposal retrieval.
- Remediation start, approval, AI escalation, and cancellation.
- Patched branch and optional pull-request submission.

The MCP server does not:

- Call Engine gRPC directly.
- Access PostgreSQL directly.
- Reimplement scan or remediation logic.
- Auto-approve remediation proposals.
- Store SCM, Galaxy, or AI credentials.
- Add custom-rule execution.

## Architecture

```text
MCP client
    |
    | MCP stdio or Streamable HTTP
    v
APME MCP adapter
    |
    | REST /api/v1
    v
APME Gateway
    |
    | gRPC FixSession and reporting
    v
APME Engine and validators
```

The adapter is a stateless protocol translation layer. Gateway remains the source of truth for project and operation state. The adapter uses Gateway SSE for long-running operation waits and returns structured operation snapshots rather than maintaining a second workflow state machine.

## Tool groups

### Projects and settings

- `list_projects`
- `get_project`
- `create_project`
- `list_galaxy_servers`
- `create_galaxy_server`

### Scans and results

- `start_project_scan`
- `get_operation`
- `wait_for_operation`
- `get_project_violations`
- `get_project_activity`
- `get_project_dependencies`
- `get_project_sbom`
- `get_project_graph`

### Remediation

- `start_remediation`
- `begin_remediate`
- `get_proposals`
- `update_draft_proposals`
- `approve_proposals`
- `escalate_ai`
- `cancel_operation`

### SCM

- `submit_remediation`

Tool names and input/output schemas must be explicit and typed. Mutating tools return project ID, operation ID, status, and relevant Gateway response data.

## Safety requirements

- Proposal approval requires explicit `approved_ids`; omitted proposals remain declined.
- AI escalation requires explicit target paths and optional rule IDs.
- Branch push and PR creation require explicit `create_pr`/submission parameters.
- Project deletion is out of initial scope.
- Credentials are write-only inputs and never appear in output, logs, or error text.
- Repository URL and SCM provider validation remain Gateway responsibilities.
- MCP server must not accept arbitrary local paths or execute shell commands.
- Tool errors map HTTP status and Gateway error detail into stable MCP errors.

## Long-running operations

`start_project_scan` and `start_remediation` return immediately with an operation ID. `wait_for_operation` consumes the Gateway operation SSE stream, supports a bounded timeout, and returns the latest snapshot plus terminal result when available. Disconnects and timeouts return resumable operation identifiers; they do not cancel operations.

The adapter must tolerate terminal operations and repeated reads. It must not create duplicate operations when a caller retries after receiving an operation ID.

## Configuration

Initial configuration:

```text
APME_GATEWAY_URL=http://127.0.0.1:8080
```

Local stdio is the only supported transport. Streamable HTTP may be added for
shared deployment, but requires a separate authenticated process boundary.
Gateway authentication integration must be injectable; no credential model is
invented by the adapter.

## Testing

Tests cover:

- Tool schema validation.
- Gateway request construction.
- HTTP error conversion.
- Operation wait completion, timeout, terminal state, and disconnect behavior.
- Proposal approval without implicit approval.
- AI target serialization.
- Submit branch versus pull request options.
- Credential redaction.
- End-to-end local Gateway smoke path using existing test fixtures where available.

Quality gates use project tox environments only: `tox -e lint`, `tox -e unit`, and relevant integration/e2e environments.
