# TASK New Example Session

This is a reference example for the `/task-new` skill.

## Interactive Mode (Streamlined)

```
User: /task-new REQ-001

Claude: Creating task for REQ-001: Scanner

REQ-001 has 4 acceptance criteria:
1. Parse playbook YAML and detect FQCN issues
2. Parse role structure and detect deprecated modules
3. Return structured Issue objects with file, line, rule_id
4. Support AAP 2.5 target version

Generate tasks from these? (Y/n/select specific)

User: n, I'll define manually

Claude: Task name and description?

User: Implement ARI wrapper - Python wrapper around vendored ARI engine that parses Ansible content and returns structured issues

Claude: Implementation steps (numbered list):

User:
1. Create src/apme_engine/ari_wrapper.py module
2. Define AriWrapper class with parse() method
3. Implement content type detection (playbook, role, collection)
4. Map ARI findings to Issue dataclass

Claude: Files to create/modify (path: action - purpose):

User:
src/apme_engine/ari_wrapper.py: create - main implementation
tests/test_ari_wrapper.py: create - unit tests

Claude: Prerequisites? (other TASKs, or "none")

User: none

Claude: Verification steps?

User: tox -e lint, tox -e unit

Claude: Which acceptance criteria? (numbers from REQ-001)

User: 1, 3

Claude: Done!
- Created: REQ-001-scanner/tasks/TASK-001-ari-wrapper.md
- Status: Pending
- Addresses: AC 1, 3

Create another task for REQ-001? (Y/n)

User: n
```

## Quick Mode

```
User: /task-new REQ-001 "Implement ARI wrapper"

Claude: Creating TASK-001 for REQ-001...

Brief description? Wrap ARI engine for parsing Ansible content

Steps? (one-liner list or "generate")
1. Create wrapper module
2. Implement parse() method
3. Map findings to Issue objects

Files? src/apme_engine/ari_wrapper.py, tests/test_ari_wrapper.py

Done! Created TASK-001-ari-wrapper.md
```

## Auto-Generate from Acceptance Criteria

```
User: /task-new REQ-001 --from-criteria

Claude: REQ-001 has 4 acceptance criteria. Generating tasks:

| TASK | From AC | Name |
|------|---------|------|
| TASK-001 | AC 1 | FQCN issue detection |
| TASK-002 | AC 2 | Deprecated module detection |
| TASK-003 | AC 3 | Issue object structure |
| TASK-004 | AC 4 | AAP 2.5 version support |

Create all 4? (Y/n/select)

User: Y

Claude: Created 4 tasks in REQ-001-scanner/tasks/
- TASK-001-fqcn-detection.md
- TASK-002-deprecated-modules.md
- TASK-003-issue-structure.md
- TASK-004-aap-version-support.md

Each has placeholder steps - refine with: edit .sdlc/specs/REQ-001-scanner/tasks/TASK-001-*.md
```

## Batch Mode

```
User: /task-new REQ-001 --batch

Claude: Batch task creation for REQ-001: Scanner

Task 1 - name? Implement ARI wrapper
Brief steps? Create wrapper, parse method, map to Issue
Files? src/apme_engine/ari_wrapper.py

Created TASK-001. Next task? (name or "done")

User: Add reporter module
Brief steps? Format violations, support terminal/JSON output
Files? src/apme_engine/reporter.py

Created TASK-002. Next task?

User: done

Claude: Created 2 tasks for REQ-001.
```

## Size Warning

```
User: [provides 10 steps and 8 files]

Claude: This task has 10 steps and 8 files - larger than ideal (1-2 hours).

Consider splitting into:
- TASK-001: Core implementation (steps 1-5)
- TASK-002: Integration and testing (steps 6-10)

Split? (Y/n)
```
