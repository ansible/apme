# Feature Specifications

This directory contains **per-feature specifications** organized by requirement.

## Purpose

Specs answer "What does this feature do?" They provide:
- Clear requirements with acceptance criteria
- Design decisions specific to a feature
- Atomic tasks for implementation
- Verification steps for completion

## Current Specifications

| REQ | Feature | Phase | Status |
|-----|---------|-------|--------|
| REQ-001 | Core Scanning Engine | PHASE-001 | In Progress |
| REQ-002 | Automated Remediation | PHASE-002 | Draft |
| REQ-003 | Security & Compliance | PHASE-003 | Draft |
| REQ-004 | Enterprise Integration | PHASE-003 | Draft |

## Directory Structure

Each feature gets its own `REQ-NNN-name/` directory:

```
specs/
├── README.md           ← You are here
├── REQ-001-scanning-engine/
│   ├── requirement.md    # What to build and acceptance criteria
│   ├── design.md         # How to build it
│   ├── contract.md       # API/interface definitions
│   └── tasks/            # Implementation tasks
├── REQ-002-automated-remediation/
├── REQ-003-security-compliance/
└── REQ-004-enterprise-integration/
```

## Phase Relationship

Requirements are grouped by delivery phase:

```
PHASE-001: CLI Scanner
└── REQ-001: Core Scanning Engine

PHASE-002: Rewrite Engine
└── REQ-002: Automated Remediation

PHASE-003: Enterprise Dashboard
├── REQ-003: Security & Compliance
└── REQ-004: Enterprise Integration
```

See [phases/README.md](../phases/README.md) for phase details.

## Creating a New Specification

```bash
/req-new "Feature Name" --phase PHASE-NNN
```

Or manually:
1. Copy template: `cp ../templates/requirement.md REQ-NNN-name/requirement.md`
2. Add phase metadata to requirement.md
3. Create design.md, contract.md
4. Break work into tasks/TASK-NNN-*.md

## Lifecycle

1. **Draft**: Initial requirements captured
2. **In Review**: Requirements under stakeholder review
3. **Approved**: Ready for implementation
4. **In Progress**: Implementation underway
5. **Implemented**: All tasks complete, acceptance criteria met
