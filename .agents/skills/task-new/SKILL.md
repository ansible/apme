---
name: task-new
description: >-
  Create implementation tasks for a requirement. Use when breaking down a
  REQ, "add task to REQ-001", "create work items", or after approving a
  requirement. Do NOT use for creating requirements (use req-new instead)
  or reviewing task status (use sdlc-status instead).
argument-hint: "[REQ-NNN] [Task Name] [--from-criteria] [--batch]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---

# TASK New

Create implementation tasks for a requirement.

## Arguments

If `$ARGUMENTS` is provided, parse for:
- `REQ-NNN` → target requirement
- Task name in quotes → use as task name
- `--from-criteria` → generate tasks from acceptance criteria
- `--batch` → create multiple tasks in sequence

## Usage

```
/task-new                           # Prompt for REQ
/task-new REQ-001                   # Create task for specific REQ
/task-new REQ-001 "Task name"       # Quick mode with name
/task-new REQ-001 --from-criteria   # Generate from acceptance criteria
/task-new REQ-001 --batch           # Create multiple tasks
```

## Behavior

### 1. Identify Parent Requirement

If no REQ specified, list available:
```
| REQ | Feature | Status | Tasks |
|-----|---------|--------|-------|
| REQ-001 | Scanner | In Progress | 2 |
| REQ-002 | Rewriter | Draft | 0 |

Which REQ? (number)
```

Read parent requirement to understand context and acceptance criteria.

### 2. Offer Auto-Generation

If REQ has acceptance criteria:
```
REQ-001 has 4 acceptance criteria. Generate tasks from these? (Y/n/select)
```

If Y: Generate one task per criterion with placeholder steps.
If select: Let user pick which criteria to generate tasks for.

### 3. Determine Next TASK Number

Scan `.sdlc/specs/REQ-NNN-*/tasks/TASK-*.md` for highest number in this REQ, increment.

Task numbers are per-REQ (REQ-001 has TASK-001, TASK-002; REQ-002 has its own TASK-001).

### 4. Gather Information (Streamlined)

```
Task name and description?
```
Accept: "Name - description" or ask description separately.

```
Implementation steps (numbered list):
```
Accept freeform numbered list. Parse into steps array.

```
Files to create/modify (path: action - purpose):
```
Accept freeform. Parse into table with path, action (create/modify), purpose.

```
Prerequisites? (other TASKs, or "none")
```

```
Verification steps?
```
Accept: "tox -e lint, tox -e unit" or freeform list. Always use tox envs (ADR-047).

```
Which acceptance criteria? (numbers from REQ)
```
Show AC list from parent REQ, accept numbers.

### 5. Size Check

If task has >6 steps or >5 files:
```
This task has N steps and M files - larger than ideal (1-2 hours).
Consider splitting? (Y/n)
```

If Y: Help split into smaller tasks.

### 6. Create Task File

Create `.sdlc/specs/REQ-NNN-slug/tasks/TASK-NNN-task-slug.md` using template.

**Slug:** 2-4 words, lowercase, hyphens.

Fill:
- Parent REQ link
- Status: Pending
- Description
- Prerequisites (if any)
- Implementation steps (numbered)
- Files table
- Verification checklist
- Acceptance criteria reference

### 7. Summary & Continue

```
Done!
- Created: REQ-001-scanner/tasks/TASK-001-ari-wrapper.md
- Status: Pending
- Addresses: AC 1, 3

Create another task for REQ-001? (Y/n)
```

If Y: Loop back to step 4.

## Quick Mode

With inline parameters:

| Provided | Skip |
|----------|------|
| REQ number | REQ selection |
| Task name in quotes | Name prompt |
| `--from-criteria` | All prompts (generate from AC) |
| `--batch` | Continue prompt (auto-continue) |

## Auto-Generation Mode

With `--from-criteria`:
1. Read acceptance criteria from REQ
2. Generate one task per criterion
3. Task name = criterion summary
4. Steps = placeholder "Implement [criterion]"
5. User refines by editing files directly

## Batch Mode

With `--batch`:
1. Minimal prompts per task (name, brief steps, files)
2. Auto-continue after each task
3. Type "done" to finish

## Parsing Freeform Input

**Steps** — accept:
```
1. Create module
2. Add parse method
3. Write tests
```
or
```
- Create module
- Add parse method
- Write tests
```

**Files** — accept:
```
src/module.py: create - main implementation
tests/test_module.py: create - tests
src/existing.py: modify - add import
```
Parse into structured table.

## Edge Cases

| Situation | Handling |
|-----------|----------|
| REQ has no acceptance criteria | Skip AC reference, note in task |
| Tasks directory doesn't exist | Create it |
| Task seems too large | Offer to split |
| No prerequisites | Set to "None" |
