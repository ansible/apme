# REQ New Example Session

This is a reference example for the `/req-new` skill.

## Interactive Mode with Phase Assignment

```
User: /req-new

Claude: Let's create a new Requirement specification.

Which phase does this requirement belong to?

| Phase | Name | Status |
|-------|------|--------|
| PHASE-001 | CLI Scanner | In Progress |
| PHASE-002 | Rewrite Engine | Not Started |

Enter phase number (or "none" for unassigned):

User: PHASE-001

Claude: Feature name and purpose?

User: Policy Violation Reporter - Generate human-readable reports of policy violations for CI integration

Claude: User stories (one per line, "As a X, I want Y so that Z"):

User: As a Platform Engineer, I want to view violations in my terminal so that I get quick feedback
As a CI Pipeline, I want SARIF format output so that I can integrate with GitHub code scanning

Claude: Acceptance criteria (Gherkin or plain bullets):

User:
GIVEN a scan with 3 violations
WHEN terminal output is requested
THEN violations show file path, line number, rule ID, description

Claude: Dependencies? (internal REQs, external tools, or "none")

User: REQ-001 Scanner, SARIF library

Claude: Initial status? (1=Draft, 2=In Review, 3=Approved)

User: 1

Claude: Creating REQ-005-policy-violation-reporter/...

Done!
- Created: .sdlc/specs/REQ-005-policy-violation-reporter/
- Phase: PHASE-001 - CLI Scanner
- Status: Draft

Next: /task-new REQ-005 to create implementation tasks
```

## Quick Mode with Phase

```
User: /req-new "Violation Reporter" --phase PHASE-001 --status draft

Claude: Creating REQ-005-violation-reporter for PHASE-001...

I'll need a bit more info:

Purpose?

User: Generate reports for CI integration

Done!
- Created: REQ-005-violation-reporter/
- Phase: PHASE-001 - CLI Scanner
- Status: Draft

Next: /task-new REQ-005
```

## Interactive Mode (No Phases)

When no phases exist, phase assignment is skipped:

```
User: /req-new

Claude: Let's create a new Requirement specification.

Feature name and purpose?

User: FQCN Detector - Detect non-FQCN module references

Claude: User stories...
...

Done!
- Created: .sdlc/specs/REQ-001-fqcn-detector/
- Status: Draft

Tip: Run /prd-import to set up phases from a PRD.
```

## From Conversation

```
User: I'm thinking we need a way to export scan results in different formats...

[discussion continues about SARIF, JUnit, terminal output]

User: /req-new

Claude: I see you've been discussing report export formats.

Create REQ from this conversation?
- Feature: Scan Result Exporter
- Purpose: Export violations in multiple formats (SARIF, JUnit, terminal)
- Phase: PHASE-001 (matches current work context)

Confirm? (Y to use, N to start fresh)

User: Y

Claude: Status? (1=Draft, 2=In Review, 3=Approved)

User: 2

Done!
- Created: REQ-005-scan-result-exporter/
- Phase: PHASE-001 - CLI Scanner
- Status: In Review
```

## Minimal Mode

For quick capture when details aren't ready:

```
User: /req-new "Dashboard UI" --phase PHASE-003 --minimal

Claude: Created REQ-006-dashboard-ui/ with:
- requirement.md (title + phase only)
- Empty placeholders for design.md, contract.md, tasks/
- Phase: PHASE-003 - Enterprise Dashboard

Fill in details later: edit .sdlc/specs/REQ-006-dashboard-ui/requirement.md
```
