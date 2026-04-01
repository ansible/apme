# TASK-NNN: Task Name

## Parent Requirement

REQ-NNN: [Feature Name]

## Status

Pending | In Progress | Complete | Blocked

## Description

What this task accomplishes (1-2 sentences). Should be completable in 1-2 hours.

## Prerequisites

- [ ] TASK-XXX must be complete
- [ ] [Other prerequisite]

## Implementation Notes

Step-by-step guidance for implementation:

1. [First step with details]
2. [Second step with details]
3. [Third step with details]

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme/module/file.py` | Create | [Purpose] |
| `tests/test_file.py` | Create | [Purpose] |
| `src/apme/existing.py` | Modify | [What changes] |

## Code Snippets

If helpful, provide skeleton code:

```python
# Example structure
class ClassName:
    def method_name(self, param: Type) -> ReturnType:
        """Docstring."""
        pass
```

## Test Cases

| Test | Input | Expected Output |
|------|-------|-----------------|
| [Test name] | [Input data] | [Expected result] |

## Verification

Before marking complete:

- [ ] Unit tests pass (`tox -e unit`)
- [ ] Lint / type-check pass (`tox -e lint`)
- [ ] Integration test: [specific test description]
- [ ] Manual verification: [specific steps]

## Acceptance Criteria Reference

From REQ-NNN:
- [ ] [Criterion 1 this task addresses]
- [ ] [Criterion 2 this task addresses]

## Notes

Any additional context, gotchas, or implementation decisions.

## Blockers

If blocked, document:
- **Blocker**: [Description]
- **Waiting On**: [Who/what]
- **Workaround**: [If any]

---

## Completion Checklist

- [ ] Implementation complete
- [ ] All verification steps pass
- [ ] Status updated to Complete
- [ ] Committed with message: `Implements TASK-NNN: [description]`
