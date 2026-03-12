---
name: dr-review
description: >-
  Resolve open Decision Requests. Use when deciding DRs, resolving
  blockers, "what should we do about DR-X", "decide the blocking question",
  or when `/sdlc-status` shows blocking DRs. Do NOT use for creating new
  DRs (use dr-new instead) or just viewing status (use sdlc-status instead).
argument-hint: "[DR-NNN] [--quick]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# DR Review

Resolve open Decision Requests.

## Arguments

If `$ARGUMENTS` is provided, parse for:
- `DR-NNN` → review that specific DR directly
- `--quick` → use recommendations with minimal prompts

If no argument, list open DRs and prompt for selection.

## Usage

```
/dr-review              # List open DRs, select one
/dr-review DR-001       # Review specific DR
/dr-review --quick      # Use recommendations, minimal prompts
```

## Behavior

### 1. List Open DRs

Scan `.sdlc/decisions/open/DR-*.md` and present sorted by priority (Blocking > High > Medium > Low):

```
Found N open Decision Requests:

| Priority | DR | Question |
|----------|-----|----------|
| Blocking | DR-004 | AAP Pre-Flight Integration |
...

Which DR to review? (number or "next" for highest priority)
```

**Empty state:** "No open Decision Requests. All caught up!"

**If >10 DRs:** Show Blocking/High only, summarize: "...plus N medium/low DRs"

### 2. Present DR Summary

Read the selected DR file and present:
- **Question**: Core decision needed
- **Context**: Why it matters (2-3 sentences max)
- **Options**: Each with pros/cons
- **Recommendation**: Highlight if present

**If no recommendation:** Note "No recommendation provided" and present options neutrally.

### 3. Facilitate Decision

```
What is your decision?
1. [Option A description]
2. [Option B description]
...
D. Defer for later
M. Need more information
```

**On option selected:**
- Ask for rationale (offer recommendation text if available)
- Ask for action items (optional)

**On defer:**
- Ask when to revisit
- Ask for deferral reason

**On "need more info":**
- Ask what information is needed
- Add to DR as open question
- Keep in `open/`

### 4. Record Decision

Update the DR file:
- `Status:` → Decided / Deferred
- `Decision:` → chosen option
- `Rationale:` → user's rationale
- `Action Items:` → if any
- `Decided:` → today's date

Preserve existing file format — only update relevant sections.

### 5. Move to Closed

```
.sdlc/decisions/
├── open/           # Remove from here
└── closed/
    ├── decided/    # Most decisions
    ├── deferred/   # Revisit later
    └── superseded/ # Replaced by another DR
```

### 6. Update README Index

Edit `.sdlc/decisions/README.md`:
- Remove from "Open" table
- Add to appropriate "Closed" table with decision summary

### 7. Offer ADR (If Architectural)

If decision affects architecture, patterns, or technology choices:
```
This decision affects architecture. Create an ADR? (Y/n)
```

If yes: Copy template to `.sdlc/adrs/ADR-NNN-title.md`, pre-fill from DR, set status "Proposed".

Use next available ADR number (scan existing ADRs).

### 8. Summary & Continue

```
Done! DR-001 decided: [brief summary]
- Moved to closed/decided/
- README updated
- ADR-014 created (if applicable)

Action items:
- [ ] [any recorded items]

Review another DR? (Y/n or DR number)
```

**If "Y" or number:** Loop back to step 2/1
**If "n":** Show remaining count: "N open DRs remaining (X blocking)"

## Quick Mode

With `--quick` flag or when user says "just use the recommendation":
1. Show DR question and recommended option
2. Ask for confirmation only
3. Use recommendation text as rationale
4. Skip action items prompt
5. Skip ADR prompt (can always create later)

## Edge Cases

| Situation | Handling |
|-----------|----------|
| DR has no options section | Ask user to state the options or defer |
| User wants hybrid of options | Record as custom decision, note which options it combines |
| Priority should change | Update priority in file before moving to closed |
| DR blocks other work | Note in summary what gets unblocked |
