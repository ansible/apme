# REQ-004: Enterprise Integration

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: In Progress
- **Created**: 2026-03-12
- **Updated**: 2026-03-25

## Overview

Enterprise integration capabilities including CLI tooling, web dashboard, and AAP pre-flight checks.

## User Stories

**As a DevOps Engineer**, I want CLI tooling so that I can scan locally and integrate into CI/CD.

**As a Product Manager**, I want a dashboard so that I can see "Total Errors Resolved" and "Hours Saved" metrics.

**As an AAP Administrator**, I want pre-flight checks so that non-compliant code cannot run in AAP.

## Acceptance Criteria

### CLI Tooling
- [x] GIVEN a local environment
- [x] WHEN `apme check` runs
- [x] THEN results are displayed in terminal and/or saved to file

### Web Dashboard
- [x] GIVEN enterprise-wide scan data
- [x] WHEN dashboard is accessed
- [x] THEN scan results and current violations are displayed
- [ ] GIVEN aggregated scan history
- [ ] WHEN dashboard is accessed
- [ ] THEN ROI metrics are displayed (errors resolved, hours saved)

### AAP Pre-Flight Integration
- [ ] GIVEN an AAP Job Template
- [ ] WHEN pre-flight check is enabled
- [ ] THEN non-compliant playbooks are blocked from execution

## Dependencies

- REQ-001: Core Scanning Engine
- REQ-003: Security & Compliance (for policy checks)

## Implementation Notes

Significant progress by Brad (cidrblock) across multiple PRs. Current state:

**CLI (complete):**
- Thin CLI with daemon mode (ADR-024)
- `apme check`, `apme remediate`, `apme format` subcommands (argparse-based)
- JSON output for automation (`--json`)

**Web Dashboard (in progress):**
- Web gateway architecture (ADR-029)
- Frontend deployment model (ADR-030)
- Project-centric UI model (ADR-037) — projects linked to scan operations
- Sidebar navigation with Reporting, Operations, Settings groups
- Scan result summaries with current violations display
- Shared operation UI components
- Settings page with AI model picker
- Session management with approval flow
- Playwright test coverage for UI
- WebSocket-based progress streaming

**AAP Pre-Flight (not started):**
- Deferred per DR-004 — revisit after v1 CLI is complete

## Notes

Dashboard provides ROI visibility for Product Managers. AAP integration provides enforcement point.
ROI/time-saved metrics remain to be implemented.