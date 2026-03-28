# Capability Roadmap (Phases)

This directory contains the phased delivery roadmap. Each phase groups related requirements.

## Phase Index

| Phase | Name | Status | Requirements |
|-------|------|--------|--------------|
| PHASE-001 | CLI Scanner | In Progress | REQ-001 |
| PHASE-002 | Rewrite Engine | Implemented | REQ-002 |
| PHASE-003 | Enterprise Dashboard | In Progress | REQ-003, REQ-004, REQ-008, REQ-010–014 |
| PHASE-004 | AI Remediation | Implemented | DR-005 decided |

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
PHASE-001: CLI Scanner (In Progress)
└── REQ-001: Core Scanning Engine (In Progress)

PHASE-002: Rewrite Engine (Implemented)
└── REQ-002: Automated Remediation (Implemented)

PHASE-003: Enterprise Dashboard (In Progress)
├── REQ-003: Security & Compliance (Draft)
├── REQ-004: Enterprise Integration (In Progress)
├── REQ-008: ROI Dashboard (Draft)
├── REQ-010: Dependency Health Assessment (Draft)
├── REQ-011: AA Deprecated Module Reporting (Draft)
├── REQ-012: EDA Rulebook Validation (Draft)
├── REQ-013: Extended OPA Policy Inputs (Draft)
└── REQ-014: Policy Permissive Mode (Draft)

PHASE-004: AI Remediation (Implemented)
└── DR-005: AI-Assisted Remediation (Decided — Abbenay AI integration)
```

Each REQ belongs to exactly one phase. The phase's status is derived from its requirements:
- **Not Started**: All REQs are Draft
- **In Progress**: At least one REQ is In Progress
- **Complete**: All REQs are Implemented
