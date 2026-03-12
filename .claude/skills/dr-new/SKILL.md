---
name: dr-new
description: >-
  Create a Decision Request. Use when you have a question that needs
  deciding, "we need to make a decision about X", "capture this as a DR",
  "log this blocking question", or after discussing something that needs
  formal resolution. Do NOT use for resolving DRs (use dr-review instead)
  or documenting decided architecture (use adr-new instead).
argument-hint: "[Question] [--priority blocking|high|medium|low]"
user-invocable: true
disable-model-invocation: true
metadata:
  author: APME Team
  version: 1.0.0
---

# DR New

Create a Decision Request.

## Arguments

If `$ARGUMENTS` is provided, parse for:
- Question text in quotes → use as DR question
- `--priority X` → set priority (blocking/high/medium/low)
- `--category X` → set category
- `--from-conversation` → extract from current discussion

## Usage

```
/dr-new                                    # Interactive mode
/dr-new "Question here"                    # Start with question
/dr-new "Question" --priority blocking     # With priority
/dr-new --from-conversation                # Extract from current discussion
```

## Behavior

### 1. Check for Conversation Context

If recent conversation contains a question or debate:
```
I see you've been discussing [topic]. Create DR from this conversation?
- Question: [extracted]
- Context: [extracted]
Confirm? (Y to use, N to start fresh)
```

Extract question, context, and any options mentioned. Skip to priority if confirmed.

### 2. Determine Next DR Number

Scan `.sdlc/decisions/open/` and `.sdlc/decisions/closed/*/` for highest DR-NNN, increment by 1.

### 3. Check for Duplicates

Search existing open DRs for similar topics:
```
Found existing DR-008 "Data Persistence" which may overlap.
Continue with new DR? (Y/n/view)
```

### 4. Gather Information

**Streamlined prompts** — combine where possible:

```
What question needs to be decided?
```

```
Context — why is this decision needed now?
```

```
Category and Priority?
1. Architecture + Blocking    5. Technical + High
2. Architecture + High        6. Process + Medium
3. Product + Blocking         7. Other (specify)
4. Product + High
```

```
What's blocked or at risk if we don't decide?
```

**Options** — accept freeform, then structure:
```
Describe the options (one per paragraph, include pros/cons if known):
```

Parse the response into structured options with:
- Description
- Pros (extracted or ask briefly)
- Cons (extracted or ask briefly)
- Effort: Low/Medium/High (infer or ask once)

```
Recommendation? (or "none" to leave open)
```

### 5. Find Related Artifacts

Search for related REQs, ADRs, and DRs. Show top 3-5:
```
Related artifacts:
- DR-008: Data Persistence (related)
- ADR-004: Podman Pod Deployment
- REQ-003: Dashboard
```

### 6. Create DR File

Create `.sdlc/decisions/open/DR-NNN-short-slug.md` using template from `.sdlc/templates/decision-request.md`.

**Slug:** 3-5 words, lowercase, hyphens (generate from question).

Fill all sections:
- Title, Status: Open
- Raised By: User — today's date
- Category, Priority
- Question, Context, Impact
- Options with pros/cons/effort
- Recommendation (if any)
- Related artifacts
- Empty Discussion Log and Decision sections

### 7. Update README Index

Edit `.sdlc/decisions/README.md`:
- Add row to "Open" table
- Insert in priority order (Blocking > High > Medium > Low)

### 8. Summary

```
Done!
- Created: open/DR-NNN-short-slug.md
- Added to README.md (Priority: X)

Next: /dr-review DR-NNN when ready to decide
```

## Quick Mode

With inline parameters, minimize prompts:

| Provided | Skip |
|----------|------|
| Question in quotes | Question prompt |
| `--priority X` | Priority prompt |
| `--category X` | Category prompt |
| `--from-conversation` | Question + context prompts |

Still ask for: options (if not in conversation), impact, recommendation.

## Edge Cases

| Situation | Handling |
|-----------|----------|
| No template file | Create with default structure |
| User unsure of options | "That's okay — options can be added later during review" |
| Very long question | Summarize for title, keep full in body |
| Duplicate detected | Offer to view existing DR or continue |
