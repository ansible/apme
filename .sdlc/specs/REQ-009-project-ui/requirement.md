# REQ-009: Project-Centric UI & Dashboard

## Metadata

- **Phase**: PHASE-003 - Enterprise Dashboard
- **Status**: Draft
- **Created**: 2026-03-24

## Overview

Restructure the APME UI around a project-centric model. Each project is the top-level organizational unit containing all scans, violations, fixes, AI suggestions, and metrics. Includes an overall dashboard for cross-project visibility, per-project detail views with tabs for scans/violations/fixes/AI, and the ability to initiate new scans from the UI. Supports AI suggestion review and approval workflows.

## User Stories

**As a DevOps Engineer**, I want a project-level view so that I can see all scans, violations, and fixes for a specific project in one place.

**As an Automation Architect**, I want an overall dashboard so that I can see health and status across all my projects at a glance.

**As a Team Lead**, I want to initiate a new scan from the UI so that I don't need CLI access to trigger ad-hoc scans.

**As a DevOps Engineer**, I want to see top violations per project so that I can identify the most common issues and address them systematically.

**As an Automation Architect**, I want to review and approve AI-generated remediation suggestions so that automated fixes don't get applied without human oversight.

**As a Platform Admin**, I want to manage projects (create, archive, configure) from the UI so that project setup doesn't require config file editing.

## Acceptance Criteria

### Overall Dashboard
- **GIVEN** a logged-in user
- **WHEN** they access the main dashboard
- **THEN** they see a summary of all projects: name, last scan date, health score, violation count, trend indicator (improving/declining/stable)

### Project List with Health Indicators
- **GIVEN** the overall dashboard
- **WHEN** projects are listed
- **THEN** each project card shows: project name, health score (0-100), total open violations (by severity), last scan timestamp, trend arrow

### Project Detail View — Tabs
- **GIVEN** a user navigates to a specific project
- **WHEN** the project detail page loads
- **THEN** it shows tabs: Overview, Scans, Violations, Fixes, AI Suggestions, Settings

### Project Overview Tab
- **GIVEN** the project overview tab
- **WHEN** it loads
- **THEN** it shows: health score, violation summary by severity, top 10 violations (most frequent), recent scan history (last 5), fixes applied count, AI suggestions pending count

### Scans Tab
- **GIVEN** the project scans tab
- **WHEN** it loads
- **THEN** it shows a list of all scans for the project with: scan ID, date, duration, violation count, pass/fail status, trigger type (manual/CI/scheduled)

### New Scan from UI
- **GIVEN** a user with scan permissions on a project
- **WHEN** they click "New Scan" and select scan options (target version, rule profile)
- **THEN** a scan is initiated against the project's configured source
- **AND** they can monitor progress in real-time

### Violations Tab
- **GIVEN** the project violations tab
- **WHEN** it loads
- **THEN** it shows all active violations with: rule ID, severity, file, line, message, first seen, last seen
- **AND** violations are filterable by severity, rule category, file, and status

### Top Violations Ranking
- **GIVEN** the project overview tab
- **WHEN** the "Top Violations" section renders
- **THEN** it shows the most frequently occurring rule violations ranked by count, with trend over last N scans

### Fixes Tab
- **GIVEN** the project fixes tab
- **WHEN** it loads
- **THEN** it shows all applied remediations with: fix ID, rule ID, file, before/after diff, applied date, applied by (auto/human)

### AI Suggestions Tab
- **GIVEN** the project AI suggestions tab
- **WHEN** it loads
- **THEN** it shows pending AI-generated remediation suggestions with: suggestion ID, rule ID, file, proposed diff, confidence score, AI model version

### AI Suggestion Approval
- **GIVEN** a pending AI suggestion
- **WHEN** a reviewer approves it
- **THEN** the fix is queued for application
- **AND** the suggestion status changes to "approved"
- **AND WHEN** a reviewer rejects it
- **THEN** the suggestion status changes to "rejected" with optional feedback

### Project Settings Tab
- **GIVEN** the project settings tab
- **WHEN** it loads
- **THEN** it shows: source repository URL, scan schedule, target Ansible version, notification preferences, rule profile, team members

### Project Creation
- **GIVEN** a user with create-project permissions
- **WHEN** they use the "New Project" flow
- **THEN** they can configure: project name, source repository, target Ansible version, scan schedule, rule profile

## Inputs / Outputs

### Inputs

| Name | Type | Description | Required |
|------|------|-------------|----------|
| project_id | string | Unique project identifier | Yes (for detail views) |
| tab | enum | overview, scans, violations, fixes, ai_suggestions, settings | No (defaults to overview) |
| scan_options | ScanOptions | Configuration for new scan | If initiating scan |
| suggestion_action | enum | approve, reject | If reviewing AI suggestion |
| feedback | string | Rejection reason for AI suggestion | If rejecting |

### Outputs

| Name | Type | Description |
|------|------|-------------|
| dashboard_data | DashboardSummary | Cross-project health overview |
| project_detail | ProjectDetail | Full project data for selected tab |
| scan_status | ScanProgress | Real-time scan progress |

## Behavior

### Happy Path — Overall Dashboard

1. User logs in; overall dashboard loads
2. Projects listed as cards or table rows with health indicators
3. User clicks a project to drill into project detail
4. Project detail opens on Overview tab by default

### Happy Path — New Scan

1. User navigates to project → Scans tab
2. Clicks "New Scan"
3. Selects scan options (target version, rule profile, optional scope filter)
4. Confirms and submits
5. Scan progress shown in real-time (files parsed, validators running, violations found)
6. On completion, scan appears in scan list with results

### Happy Path — AI Suggestion Review

1. User navigates to project → AI Suggestions tab
2. Views pending suggestions sorted by confidence score
3. Opens a suggestion to see proposed diff with context
4. Approves or rejects with optional feedback
5. Approved fixes are queued; rejected suggestions archived with feedback

### UI Layout

```
┌─────────────────────────────────────────────────────┐
│  APME Dashboard                          [+ New Project] │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Project A│  │ Project B│  │ Project C│  ...     │
│  │ Score: 87│  │ Score: 64│  │ Score: 95│         │
│  │ ▲ 12 viol│  │ ▼ 47 viol│  │ ─  3 viol│         │
│  │ Last: 2h │  │ Last: 1d │  │ Last: 30m│         │
│  └──────────┘  └──────────┘  └──────────┘         │
│                                                     │
│  [ROI Summary] [Recent Activity] [Alerts]           │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Project A                                [New Scan] │
├─────────────────────────────────────────────────────┤
│  [Overview] [Scans] [Violations] [Fixes] [AI] [⚙]  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Health: 87/100    Violations: 12 (2H, 5M, 5L)     │
│                                                     │
│  Top Violations        Recent Scans                 │
│  ├─ L026 (×5)          ├─ 03/24 10:30 - 12 viol    │
│  ├─ M005 (×3)          ├─ 03/23 14:00 -  8 viol    │
│  └─ SEC:aws (×2)       └─ 03/22 09:15 - 15 viol    │
│                                                     │
│  Fixes Applied: 34     AI Pending: 3                │
└─────────────────────────────────────────────────────┘
```

### Edge Cases

| Case | Handling |
|------|----------|
| Project with no scans | Show "No scans yet" with prominent "Run First Scan" CTA |
| Project with no source configured | Block scan initiation; prompt to configure source |
| AI suggestion for deleted file | Mark suggestion as stale; allow dismissal |
| Scan in progress when new scan requested | Queue new scan; show "Scan queued" message |
| 100+ projects | Paginate with search/filter; show top 20 by default |

### Error Conditions

| Error | Cause | Response |
|-------|-------|----------|
| Source repository unreachable | Git clone fails | Scan fails with clear error; last successful scan preserved |
| Scan timeout | Large project exceeds time limit | Partial results shown with warning |
| AI service unavailable | Remediation backend down | AI tab shows "Service unavailable" with retry option |

## Dependencies

### Internal

- REQ-001: Core Scanning Engine (scan execution)
- REQ-002: Automated Remediation (fix data, AI suggestions)
- REQ-005: Rule Rating & Severity (severity display)
- REQ-006: Notifications (scan completion alerts)
- REQ-007: Rule Management (rule profiles per project)
- REQ-008: ROI Dashboard (embedded ROI component)

### External

- Frontend framework (React, Vue, or Streamlit — TBD via ADR)
- WebSocket or SSE for real-time scan progress
- Git integration for source repository access

## Non-Functional Requirements

- **Performance**: Dashboard must load within 2 seconds; project detail within 1 second
- **Responsiveness**: Support desktop (1920px) and tablet (768px) viewports
- **Accessibility**: WCAG 2.1 AA compliant; keyboard navigable
- **Security**: Role-based access control; project-level permissions
- **Scalability**: Support 500+ projects, 10,000+ scans per project

## Open Questions

- [ ] Frontend framework choice (React vs. Vue vs. Streamlit) — needs ADR
- [ ] How does the UI authenticate against the gRPC backend? (API gateway? REST adapter?)
- [ ] Should project source be a Git repo URL only, or also support uploaded archives?
- [ ] Should the dashboard support multi-tenancy (org-level isolation)?
- [ ] How does "New Scan from UI" map to the existing CLI-based scan flow?

## References

- REQ-004: Enterprise Integration (existing dashboard concept)
- PHASE-003: Enterprise Dashboard
- Architecture: Container topology and gRPC contracts

---

## Change History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-24 | APME Team | Initial draft |
