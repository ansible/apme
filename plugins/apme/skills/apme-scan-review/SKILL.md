---
name: apme-scan-review
description: Run an APME project scan and summarize findings, dependencies, SBOM, and graph evidence. Use when the user wants to assess an Ansible project with APME before deciding whether remediation is needed.
user-invocable: true
---

# APME scan review

Assess project health through the APME MCP server. Keep this workflow read-only.

## Workflow

1. Call `list_projects`.
2. If the target project is not registered, ask for its repository URL and name. Call `create_project` only after the user explicitly requests project setup. Never invent repository credentials.
3. Call `start_project_scan` for the selected project.
4. Call `wait_for_operation` until the scan completes, fails, or the wait limit is reached. If it times out, report the operation ID and continue with `get_operation`.
5. If the scan failed, report status and error details. Do not retry repeatedly.
6. For a completed scan, call:
   - `get_project_violations` with paging until all findings are retrieved.
   - `get_project_dependencies`.
   - `get_project_sbom`.
   - `get_project_graph` only when graph context helps explain a finding.
   - `get_project_activity` when history or operation context matters.
7. Summarize results by severity, rule ID, file/path, and remediation relevance. Distinguish observed findings from interpretation.
8. Recommend next steps without applying changes.

## Safety

- Do not call `start_remediation`, `begin_remediate`, `approve_proposals`, `escalate_ai`, `cancel_operation`, or `submit_remediation` in this workflow.
- Do not create a project, configure Galaxy, or submit credentials unless the user explicitly asks.
- Do not claim a clean scan until all violation pages are retrieved.
- Preserve operation IDs when a scan is still running or disconnected.
- Never expose credentials or reproduce secret-shaped values from tool output.

## Output

Return:

1. Scan status and project identity.
2. Finding totals, grouped by severity and rule ID.
3. Highest-impact findings with file/path context.
4. Dependency and SBOM highlights.
5. Evidence gaps, if any.
6. Safe next-step options: inspect, configure, remediate, or submit—without taking those actions.
