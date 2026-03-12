# Context Documents

This directory contains **stable, project-wide knowledge** that applies across all features.

## Purpose

Context documents answer "How does our system work?" They provide:
- Architectural patterns and design decisions
- Coding conventions and standards
- System-wide rules and constraints
- Background knowledge for implementation

## When to Read

- **Always**: When onboarding to the project
- **Before any task**: To understand relevant patterns
- **When making decisions**: To ensure consistency

## Current Documents

| File | Description |
|------|-------------|
| `architecture.md` | Service topology, container layout, port assignments |
| `conventions.md` | Coding standards, naming conventions |
| `data-flow.md` | Request lifecycle and serialization boundaries |
| `deployment.md` | Podman pod deployment and configuration |
| `dependencies.md` | External dependencies and version constraints |
| `project-overview.md` | High-level project goals and scope |
| `rule-catalog.md` | All 93 validation rules with fixer status |
| `rule-doc-format.md` | Standard format for rule documentation |
| `lint-rule-mapping.md` | Rule ID cross-mapping and migration guide |
| `ansiblelint-coverage.md` | Coverage comparison with ansible-lint rules |
| `ansible-core-migration.md` | Breaking changes in ansible-core 2.19/2.20 |
| `design-validators.md` | Validator abstraction and plugin rationale |
| `design-remediation.md` | Remediation engine architecture |
| `design-dashboard.md` | Web dashboard architecture |

## When to Add New Documents

Add to `context/` when the content:
- Applies to multiple features or the whole project
- Won't change when individual features are completed
- Represents a stable convention or design decision

For feature-specific documentation, use `specs/` instead.
