# APME Agent Skills

Agent skills for development workflow and spec-driven development.

## Available Skills

### Pull Requests (`pr-*`)

| Skill | Purpose | Arguments |
|-------|---------|-----------|
| `pr-new` | Prepare and submit a pull request | `[branch-name] [--title 'PR title']` |
| `pr-traceability` | Verify ADR/issue links and ADR status updates for a PR | `[PR number or branch]` |
| `pr-address-feedback` | Handle PR review feedback | `<PR number>` |
| `pr-contributor-review` | Review and prepare a contributor's PR (upstream/fork) | `<PR number or URL>` |

### Spec-Driven Development (SDLC)

| Skill | Purpose | Arguments |
|-------|---------|-----------|
| `sdlc-status` | Show project status and blockers | `[phase or req]` |
| `workflow` | Get workflow guidance | `[next\|blockers\|start\|resume\|decision\|import]` |
| `prd-import` | Import PRD, create artifacts | `[path or URL]` |
| `rfe-capture` | Capture external RFE with research-first approach | `[Jira key or description]` |
| `phase-new` | Create delivery phase | `[Phase Name]` |
| `req-new` | Create requirement spec | `[Feature] [--phase X]` |
| `task-new` | Create implementation tasks | `[REQ-NNN] [Task Name]` |
| `dr-new` | Create Decision Request | `[Question] [--priority X]` |
| `dr-review` | Resolve Decision Request | `[DR-NNN] [--quick]` |
| `adr-new` | Create Architecture Decision Record | `[Title] [--from-dr X]` |

### Utilities

| Skill | Purpose | Arguments |
|-------|---------|-----------|
| `tox` | tox environment reference (lint, test, build, pod) | `[environment-name]` |
| `lean-ci` | Guide for GitHub Actions workflows | `[workflow-name]` |
| `security-scan` | Scan dependencies and CI for vulnerabilities | `[--update]` |
| `branch-align` | Rename branch to match artifact ID after renumbering | `[new-branch-name]` |

## Skill Structure

```
skills/
├── README.md               ← You are here
├── resources/              # Shared resources
│   └── status-values.md
├── pr-new/                 # PR lifecycle
│   └── SKILL.md
├── pr-traceability/
│   └── SKILL.md
├── pr-address-feedback/
│   └── SKILL.md
├── pr-contributor-review/
│   └── SKILL.md
├── adr-new/                # SDLC artifacts
│   ├── SKILL.md
│   └── references/
├── dr-new/
│   ├── SKILL.md
│   └── references/
├── dr-review/
│   ├── SKILL.md
│   └── references/
├── phase-new/
│   └── SKILL.md
├── prd-import/
│   └── SKILL.md
├── req-new/
│   ├── SKILL.md
│   └── references/
├── rfe-capture/
│   └── SKILL.md
├── sdlc-status/
│   ├── SKILL.md
│   └── references/
├── task-new/
│   ├── SKILL.md
│   └── references/
├── workflow/
│   ├── SKILL.md
│   └── references/
├── branch-align/           # Utilities
│   └── SKILL.md
├── lean-ci/
│   └── SKILL.md
├── security-scan/
│   └── SKILL.md
└── tox/
    └── SKILL.md
```

## SKILL.md Format

Each skill has YAML frontmatter:

```yaml
---
name: skill-name
description: >-
  What the skill does. When to use it. Trigger phrases.
  When NOT to use it.
argument-hint: "[expected arguments]"
user-invocable: true
metadata:
  author: APME Team
  version: 1.0.0
---
```

## Agent Invocation Policy

SDLC skills may be invoked by the agent proactively during development
when the context warrants it (e.g., creating an ADR after an architectural
decision is made). The agent informs the user when it creates an artifact.
See ADR-017 for rationale.

## Version

- **Version**: 1.0.0
- **Author**: APME Team
- **License**: Apache 2.0
