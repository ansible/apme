# Decision Requests (DRs)

This directory tracks open questions, decisions under review, and resolved decisions for the APME project.

## Directory Structure

```
.sdlc/decisions/
├── README.md              # This file - index and workflow documentation
├── open/                  # DRs requiring attention
│   ├── DR-001-version-specific-analysis.md
│   ├── DR-002-sbom-format.md
│   └── ...
└── closed/                # Resolved DRs
    ├── decided/           # Decisions made and acted upon
    ├── deferred/          # Parked for future consideration
    └── superseded/        # Replaced by another DR
```

## Purpose

Decision Requests (DRs) provide a formal mechanism for:
- Capturing questions that need team input
- Documenting options and trade-offs
- Recording decisions and rationale
- Tracking action items that follow from decisions

## Workflow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│    Open     │────►│ Under Review │────►│   Decided   │────►│    ADR /     │
│  (raised)   │     │  (discussed) │     │ (resolved)  │     │  REQ Update  │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────────┘
       │                                         │
       │                                         ▼
       │                                  ┌─────────────┐
       └─────────────────────────────────►│   Deferred  │
                                          │  (parked)   │
                                          └─────────────┘
```

### Triage Checklist (for each open DR)

1. **Read the DR** — understand the question, context, and options
2. **Assess completeness** — are options well-defined? Is there a recommendation?
3. **Identify stakeholders** — who needs to weigh in?
4. **Set priority** — Blocking > High > Medium > Low
5. **Schedule review** — add to standup/meeting agenda if blocking

### Decision Process

1. **Discuss options** — capture input in Discussion Log
2. **Choose option** — document in Decision section
3. **Record rationale** — why this option over others
4. **Assign action items** — ADR to create, REQ to update, etc.
5. **Update status** — change to "Decided" or "Deferred"
6. **Move file** — from `open/` to `closed/{decided|deferred|superseded}/`
7. **Update README index** — move entry to appropriate table

### Resolution Types

| Status | Action | Destination |
|--------|--------|-------------|
| **Decided** | Implement the decision; create follow-up ADR/REQ | `closed/decided/` |
| **Deferred** | Park for later; document when to revisit | `closed/deferred/` |
| **Superseded** | Link to replacement DR | `closed/superseded/` |

### Status Definitions

| Status | Meaning |
|--------|---------|
| **Open** | Question raised, awaiting discussion |
| **Under Review** | Actively being discussed by team |
| **Decided** | Decision made, action items assigned |
| **Deferred** | Parked for future consideration |
| **Superseded** | Replaced by another DR |

## Categories

| Category | Description | Examples |
|----------|-------------|----------|
| **Product** | Requirements, acceptance criteria, scope | DR-001 (Version-Specific Analysis) |
| **Architecture** | System design, data flows, integrations | DR-004 (AAP Integration) |
| **Strategy** | Business model, competitive positioning | DR-007 (Target Persona), DR-009 (Licensing) |
| **Technical** | Implementation details, formats, APIs | DR-002 (SBOM Format) |
| **Process** | Development workflow, release process | |

## Priority Levels

| Priority | Meaning |
|----------|---------|
| **Blocking** | Cannot proceed with dependent work until resolved |
| **High** | Should be resolved this sprint/phase |
| **Medium** | Should be resolved this release |
| **Low** | Nice to have clarity, not urgent |

## Index

### Open

| DR | Title | Category | Priority | Raised |
|----|-------|----------|----------|--------|
| [DR-004](open/DR-004-aap-integration.md) | AAP Pre-Flight Integration | Architecture | Blocking | 2026-03-11 |
| [DR-008](open/DR-008-data-persistence.md) | Scan Result Persistence | Architecture | Blocking | 2026-03-11 |
| [DR-001](open/DR-001-version-specific-analysis.md) | Version-Specific Analysis Behavior | Product | High | 2026-03-11 |
| [DR-007](open/DR-007-target-persona.md) | Target Persona Priority | Strategy | High | 2026-03-11 |
| [DR-009](open/DR-009-licensing-model.md) | Licensing Model (OSS vs Open Core) | Strategy | High | 2026-03-11 |
| [DR-002](open/DR-002-sbom-format.md) | SBOM Format and Scope | Technical | Medium | 2026-03-11 |
| [DR-003](open/DR-003-dashboard-architecture.md) | Dashboard Architecture | Architecture | Medium | 2026-03-11 |
| [DR-005](open/DR-005-ai-remediation.md) | AI-Assisted Remediation Execution | Technical | Medium | 2026-03-11 |
| [DR-010](open/DR-010-version-coverage.md) | Ansible Version Coverage Range | Technical | Medium | 2026-03-11 |
| [DR-006](open/DR-006-success-metrics.md) | Success Metrics Baselines | Product | Low | 2026-03-11 |

### Closed: Decided

| DR | Title | Decision | Date |
|----|-------|----------|------|
| [DR-011](closed/decided/DR-011-repository-location.md) | Repository Location and Visibility | GitHub public (ansible/apme) | 2026-03-12 |
| [DR-012](closed/decided/DR-012-test-dr-process.md) | Just Testing the DR Process | Keep Current Approach | 2026-03-11 |

### Closed: Deferred

| DR | Title | Reason | Revisit |
|----|-------|--------|---------|
| *No deferrals yet* | | | |

### Closed: Superseded

| DR | Title | Replaced By | Date |
|----|-------|-------------|------|
| *No superseded DRs* | | | |

## Tools

### `/dr-new` Skill

Use `/dr-new` to interactively create a new Decision Request:

```
/dr-new    # Start the interactive DR creation wizard
```

The skill will:
1. Guide you through formulating the question and context
2. Help structure options with pros/cons
3. Auto-assign the next DR number
4. Create the file in `open/`
5. Add the entry to this README index

### `/dr-review` Skill

Use `/dr-review` to interactively triage and resolve Decision Requests:

```
/dr-review           # List all open DRs, select one to review
/dr-review DR-001    # Review a specific DR
```

The skill will:
1. Present the DR summary, options, and recommendation
2. Guide you through the decision process
3. Record the decision and rationale in the DR file
4. Move the file to the appropriate closed directory
5. Update this README index
6. Optionally scaffold an ADR if the decision is architectural

## Creating a New DR

Use `/dr-new` to interactively create a new DR, or manually:

1. Copy the template: `cp ../templates/decision-request.md open/DR-NNN-short-name.md`
2. Fill in the question, context, and options
3. Add to the Open index above
4. Notify stakeholders for review
5. Update status as discussion progresses
6. When decided, use `/dr-review` to process the resolution

## Review Cadence

- **Blocking DRs**: Review in next standup/sync
- **High DRs**: Review weekly
- **Medium/Low DRs**: Review bi-weekly or at milestone boundaries
