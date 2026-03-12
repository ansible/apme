# Capability Roadmap (Phases)

This directory contains the phased delivery roadmap. Each phase groups related requirements.

## Phase Index

| Phase | Name | Status | Requirements |
|-------|------|--------|--------------|
| PHASE-001 | CLI Scanner | In Progress | REQ-001 |
| PHASE-002 | Rewrite Engine | Not Started | REQ-002 |
| PHASE-003 | Enterprise Dashboard | Not Started | REQ-003, REQ-004 |
| PHASE-004 | AI Remediation | Not Started | (future) |

## Phase Lifecycle

```
Not Started → In Progress → Complete
```

## Structure

```
phases/
├── README.md           ← You are here
├── PHASE-001-cli-scanner/
│   └── phase.md
├── PHASE-002-rewrite-engine/
│   └── phase.md
├── PHASE-003-enterprise-dashboard/
│   └── phase.md
└── PHASE-004-ai-remediation/
    └── phase.md
```

## Creating Phases

Use `/prd-import` to import phases from a PRD, or `/phase-new` to create manually.

## Relationship to Requirements

```
PHASE-001: CLI Scanner
└── REQ-001: Core Scanning Engine

PHASE-002: Rewrite Engine
└── REQ-002: Automated Remediation

PHASE-003: Enterprise Dashboard
├── REQ-003: Security & Compliance
└── REQ-004: Enterprise Integration

PHASE-004: AI Remediation
└── (requirements TBD)
```

Each REQ belongs to exactly one phase. The phase's status is derived from its requirements:
- **Not Started**: All REQs are Draft
- **In Progress**: At least one REQ is In Progress or Approved
- **Complete**: All REQs are Implemented
