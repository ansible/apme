---
name: phase-new
description: >-
  Create a delivery phase for grouping requirements. Use when defining
  roadmap phases, "add a phase", "create Phase 2", or organizing
  requirements into delivery milestones. Do NOT use for importing phases
  from a PRD (use prd-import instead) or creating requirements (use
  req-new instead).
argument-hint: "[Phase Name]"
user-invocable: true
disable-model-invocation: true
metadata:
  author: APME Team
  version: 1.0.0
---

# Phase New

Create a delivery phase for grouping requirements.

## Arguments

If `$ARGUMENTS` is provided (e.g., "Enterprise Dashboard"), use it as the
phase name and enter quick mode. Otherwise, run interactive mode.

## Usage

```
/phase-new                    # Interactive mode
/phase-new "Phase Name"       # Quick mode with name
```

## Behavior

### 1. Determine Next Phase Number

Scan `.sdlc/phases/PHASE-*` directories for highest number, increment by 1.

### 2. Gather Information

```
Phase name? (e.g., "CLI Scanner", "Enterprise Dashboard")
```

```
Overview - what does this phase deliver? (1-2 sentences)
```

```
Goals (one per line):
```

```
Success criteria (one per line):
```

```
Dependencies? (previous phases or external, or "none")
```

### 3. Create Phase Directory

```
.sdlc/phases/PHASE-NNN-slug/
└── phase.md
```

**Slug:** 2-4 words, lowercase, hyphens.

### 4. Fill Phase Template

```markdown
# PHASE-NNN: Phase Name

## Status

Not Started

## Overview

[overview text]

## Goals

- Goal 1
- Goal 2

## Success Criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Requirements

| REQ | Name | Status |
|-----|------|--------|
| — | No requirements yet | — |

## Dependencies

- [dependencies]

## Timeline

- **Target Start**: TBD
- **Target Complete**: TBD
```

### 5. Update Index

Edit `.sdlc/phases/README.md`:
- Add row to phase table
- Insert in numerical order

### 6. Summary

```
Done!
- Created: .sdlc/phases/PHASE-NNN-slug/
- Status: Not Started

Next steps:
- Create requirements: /req-new "Feature" --phase PHASE-NNN
- Or import from PRD: /prd-import
```

## Quick Mode

With name provided:
```
/phase-new "Enterprise Dashboard"
```
Ask only for overview, use name for slug, skip optional fields.

## Phase Lifecycle

```
Not Started → In Progress → Complete
```

Status is derived from requirements:
- **Not Started**: No REQs or all Draft
- **In Progress**: At least one REQ is In Progress/Approved
- **Complete**: All REQs are Implemented

## Edge Cases

| Situation | Handling |
|-----------|----------|
| No overview provided | Use "Phase NNN delivery" |
| Duplicate name | Warn, suggest unique name |
| Phase has no REQs | Valid - REQs added later |
