---
name: adr-new
description: >-
  Create an Architecture Decision Record. Use when documenting an
  architecture decision, "record this choice", "we decided to use X", or
  after resolving an architectural DR. Do NOT use for capturing questions
  (use dr-new instead) or reviewing existing ADRs (use sdlc-status instead).
argument-hint: "[Decision Title] [--from-dr DR-NNN] [--status accepted]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# ADR New

Create an Architecture Decision Record.

## Arguments

If `$ARGUMENTS` is provided, parse for:
- Decision title in quotes → use as ADR title
- `--from-dr DR-NNN` → pre-fill from a decided DR
- `--status X` → set status (proposed/accepted)

## Usage

```
/adr-new                            # Interactive mode
/adr-new "Decision Title"           # Quick mode with title
/adr-new --from-dr DR-008           # Pre-fill from decided DR
/adr-new "Title" --status accepted  # With status
```

## Behavior

### 1. Check for Context

**From DR:** If `--from-dr DR-NNN` provided, read the DR and pre-populate:
- Context from DR context
- Options from DR options
- Decision from DR decision
- Rationale from DR rationale

Ask user to confirm or modify.

**From conversation:** If recent discussion contains a decision:
```
I see you decided [X] over [Y]. Create ADR from this? (Y/n)
```

### 2. Determine Next ADR Number

Scan `.sdlc/adrs/ADR-*.md` for highest number, increment by 1.

### 3. Gather Information (Streamlined)

```
Decision title?
```

```
Context — why is this decision needed?
```
Accept paragraph. Will include any constraints and drivers mentioned.

```
Constraints and drivers? (bullet list, or included above)
```
Accept freeform bullets. Parse into constraints and decision drivers.

```
Options considered (name: description, pros/cons):
```
Accept freeform. For each option, parse:
- Name and description
- Pros (lines starting with +)
- Cons (lines starting with -)

Require at least 2 options for meaningful comparison.

```
Decision — which option and why?
```
Accept option name/number and rationale together.

```
Consequences? (use +/- prefix, or describe)
```
Parse into:
- Positive (+ prefix)
- Negative (- prefix)
- Neutral (~ prefix or unmarked)

```
Status? (1=Proposed, 2=Accepted)
```

```
Implementation guidance? (or skip)
```

```
Related ADRs? (numbers, or skip)
```
Validate ADRs exist, include titles.

```
From a DR? (number, or skip)
```
If provided, link ADR to DR and update DR with ADR reference.

### 4. Consistency and Invariant Check

**Before creating the ADR file**, verify consistency with the existing
architecture.

**Step A — Read `AGENTS.md` Architectural Invariants.** Check if this ADR
would modify, supersede, or conflict with any of the invariants:

```
Invariant consistency check:
| Invariant | Status |
|-----------|--------|
| 1. Validators read-only | Consistent / MODIFIES — [explain] |
| 2. gRPC between services | Consistent / MODIFIES — [explain] |
| ... | ... |
```

**Step B — Read `.sdlc/adrs/README.md`.** Check for conflicts with existing
accepted ADRs. Specifically look for:
- ADRs this decision would contradict without superseding
- ADRs that cover the same scope (potential duplicate)
- ADRs this should explicitly reference as "Related Decisions"

```
Existing ADR consistency:
- ADR-NNN: [compatible / superseded by this ADR / contradicted — explain]
```

**If this ADR modifies an invariant:**
```
⚠ This ADR modifies architectural invariant [N]: [name]

This requires:
1. Explicit "Supersedes" or "Amends" reference to the original ADR
2. Update to AGENTS.md Architectural Invariants after acceptance
3. Human approval — invariant changes cannot be auto-accepted

Include in ADR body: "This ADR amends invariant [N] because [rationale]"
```

**If this ADR contradicts an existing accepted ADR without superseding it:**
```
⚠ This ADR contradicts ADR-NNN without superseding it.

Either:
1. Add "Supersedes: ADR-NNN" to this ADR
2. Modify this ADR to be compatible with ADR-NNN
3. Explain why both can coexist (add to Related Decisions)
```

### 5. Create ADR File

Create `.sdlc/adrs/ADR-NNN-title-slug.md` using template.

**Slug:** 3-5 words, lowercase, hyphens.

Fill:
- Title, Status, Date (today)
- Context (including constraints/drivers)
- Options with pros/cons
- Decision and rationale
- "Why not chosen" for rejected options
- Consequences (positive/negative/neutral)
- Implementation notes (if provided)
- Related decisions
- References (originating DR if applicable)
- Revision history with initial entry

### 6. Update Index

Edit `.sdlc/adrs/README.md`:
- Add a row (insert in numerical order):

```markdown
| [ADR-NNN](ADR-NNN-slug.md) | Title | Status | YYYY-MM |
```

- Add to Changelog table

### 7. Link to DR (if applicable)

If ADR originated from a DR:
- Update DR's "Related Artifacts" to reference ADR
- If DR open, note that ADR captures the decision

### 8. Summary

```
Done!
- Created: ADR-NNN-title-slug.md
- Status: [Proposed/Accepted]
- Updated: adrs/README.md

Next: [If Proposed] Share for review | [If Accepted] Implement
```

## Quick Mode

With inline parameters:

| Provided | Skip |
|----------|------|
| Title in quotes | Title prompt |
| `--status X` | Status prompt |
| `--from-dr DR-NNN` | Context, options, decision prompts |

## From-DR Mode

With `--from-dr DR-NNN`:
1. Read the decided DR
2. Extract context, options, decision, rationale
3. Show summary and ask for confirmation
4. User can modify or accept
5. Automatically link ADR ↔ DR

## Parsing Freeform Options

Accept variations:
```
Redis: In-memory cache with TTL
+ Fast, built-in expiration
- Extra container

Memcached: Distributed cache
+ Simple, lightweight
- No persistence
```

Or structured:
```
Option 1: Redis
Pros: Fast, TTL support
Cons: Extra container
```

Parse into standard format for ADR.

## Edge Cases

| Situation | Handling |
|-----------|----------|
| <2 options provided | Ask for at least one more |
| DR not found | Warn and continue without pre-fill |
| DR not decided | Warn that DR is still open |
| No consequences listed | Note "To be determined" |
