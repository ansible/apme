---
name: req-new
description: >-
  Create a Requirement spec. Use when adding a new feature, "I want to
  spec out X", "new feature request", "create REQ for this", or when
  ready to formalize a feature idea. Do NOT use for creating tasks (use
  task-new instead) or bulk importing from PRD (use prd-import instead).
argument-hint: "[Feature Name] [--phase PHASE-NNN] [--minimal]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# REQ New

Create a Requirement spec.

## Arguments

If `$ARGUMENTS` is provided, parse for:
- Feature name in quotes → use as requirement name
- `--phase PHASE-NNN` → assign to phase
- `--status X` → set initial status
- `--minimal` → create structure only
- `--from-conversation` → extract from current discussion

## Usage

```
/req-new                              # Interactive mode
/req-new "Feature Name"               # Start with name
/req-new "Feature" --phase PHASE-001  # Assign to phase
/req-new "Feature" --status draft     # With status
/req-new --from-conversation          # Extract from discussion
/req-new "Feature" --minimal          # Just create structure
```

## Behavior

### 1. Check for Conversation Context

If recent conversation discusses a feature idea:
```
I see you've been discussing [topic]. Create REQ from this?
- Feature: [extracted]
- Purpose: [extracted]
Confirm? (Y to use, N to start fresh)
```

### 2. Determine Next REQ Number

Scan `.sdlc/specs/REQ-*` directories for highest number, increment by 1.

### 3. Assign to Phase

If phases exist (`.sdlc/phases/PHASE-*/`):
```
Which phase does this requirement belong to?

| Phase | Name | Status |
|-------|------|--------|
| PHASE-001 | CLI Scanner | In Progress |
| PHASE-002 | Rewrite Engine | Not Started |

Enter phase number (or "none" for unassigned):
```

If `--phase PHASE-NNN` provided, skip prompt.
If no phases exist, skip this step.

### 4. Gather Information (Streamlined)

```
Feature name and purpose?
```

```
User stories (one per line, "As a X, I want Y so that Z"):
```

```
Acceptance criteria (Gherkin format or plain bullets):
```

```
Dependencies? (internal REQs, external tools, or "none")
```

```
Initial status? (1=Draft, 2=In Review, 3=Approved)
```

### 5. Create Directory Structure

```
.sdlc/specs/REQ-NNN-feature-slug/
├── requirement.md    # Filled with gathered info
├── design.md         # Placeholder
├── contract.md       # Placeholder
└── tasks/            # Empty directory
```

**Slug:** 2-4 words from feature name, lowercase, hyphens.

**In requirement.md, include phase metadata:**
```markdown
# REQ-NNN: Feature Name

## Metadata

- **Phase**: PHASE-001 - CLI Scanner
- **Status**: Draft
- **Created**: YYYY-MM-DD
```

### 6. Update Indexes

**Update `.sdlc/specs/README.md`:**
```
| REQ-NNN | Feature Name | Phase | Status |
```

**Update phase file (if assigned):**
Add REQ to phase's requirements table in `.sdlc/phases/PHASE-NNN/phase.md`.

### 7. Find Related Artifacts

Search for related REQs, ADRs, open DRs. Show top 3-5.

### 8. Summary

```
Done!
- Created: .sdlc/specs/REQ-NNN-slug/
- Phase: PHASE-001 - CLI Scanner
- Status: Draft

Next: /task-new REQ-NNN to create implementation tasks
```

## Quick Mode

| Provided | Skip |
|----------|------|
| Feature name in quotes | Name prompt |
| `--phase PHASE-NNN` | Phase prompt |
| `--status X` | Status prompt |
| `--minimal` | All detail prompts |
| `--from-conversation` | Name + purpose if extractable |

## Phase Integration

### When Phases Exist

REQs should be assigned to phases. The phase provides:
- Scope context (what's in/out for this phase)
- Priority guidance (current phase = priority)
- Progress tracking (phase progress = REQ completion)

### When No Phases Exist

REQs work standalone (legacy mode). Consider running `/prd-import` to set up phases from a PRD.

### Phase Status Derivation

Phase status is derived from its REQs:
- **Not Started**: All REQs are Draft
- **In Progress**: At least one REQ is In Progress
- **Complete**: All REQs are Implemented

## Parsing Freeform Input

**User stories** — accept variations:
```
As a Platform Engineer, I want terminal output so that I get quick feedback
- Platform Engineer: view violations in terminal → quick feedback
```

**Acceptance criteria** — accept:
```
GIVEN scan with violations WHEN terminal requested THEN shows file/line/rule
- Terminal output shows file path, line number, rule ID
```

## Edge Cases

| Situation | Handling |
|-----------|----------|
| No phases exist | Skip phase assignment |
| Phase not found | Warn and list available phases |
| REQ reassignment | Update old and new phase files |
| Unassigned REQ | Mark as "Unassigned" in indexes |
