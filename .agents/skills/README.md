# SDLC Skills

Claude Code skills for Spec-Driven Development.

## Available Skills

| Skill | Purpose | Arguments |
|-------|---------|-----------|
| `/sdlc-status` | Show project status and blockers | `[phase or req]` |
| `/workflow` | Get workflow guidance | `[next\|blockers\|start\|resume]` |
| `/prd-import` | Import PRD, create artifacts | `[path or URL]` |
| `/phase-new` | Create delivery phase | `[Phase Name]` |
| `/req-new` | Create requirement spec | `[Feature] [--phase X]` |
| `/task-new` | Create implementation tasks | `[REQ-NNN] [Task Name]` |
| `/dr-new` | Create Decision Request | `[Question] [--priority X]` |
| `/dr-review` | Resolve Decision Request | `[DR-NNN] [--quick]` |
| `/adr-new` | Create Architecture Decision Record | `[Title] [--from-dr X]` |

## Skill Structure

```
skills/
├── README.md               ← You are here
├── resources/              # Shared resources
│   └── status-values.md
├── sdlc-status/
│   ├── SKILL.md
│   └── references/
├── workflow/
│   ├── SKILL.md
│   └── references/
├── prd-import/
│   └── SKILL.md
├── phase-new/
│   └── SKILL.md
├── req-new/
│   ├── SKILL.md
│   └── references/
├── task-new/
│   ├── SKILL.md
│   └── references/
├── dr-new/
│   ├── SKILL.md
│   └── references/
├── dr-review/
│   ├── SKILL.md
│   └── references/
└── adr-new/
    ├── SKILL.md
    └── references/
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
disable-model-invocation: true  # For skills that modify files
metadata:
  author: Author Name
  version: 1.0.0
---
```

## Usage

Invoke skills with their slash command:

```
/sdlc-status
/workflow next
/req-new "Feature Name" --phase PHASE-001
/dr-review DR-004 --quick
```

## Version

- **Version**: 1.0.0
- **Author**: APME Team
- **License**: Apache 2.0
