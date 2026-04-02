# APME - Ansible Policy & Modernization Engine

## Project Constitution

This document is the authoritative source of truth for AI agents. All development must align with these principles.

## Overview

APME is a multi-service system that automates policy enforcement and modernization of Ansible content for AAP 2.5+. Services: Primary Orchestrator, Native/OPA/Ansible/Gitleaks Validators, Galaxy Proxy, Remediation Engine, Gateway (REST + persistence), UI (React SPA), CLI.

## Architecture

```
┌──────────────────────────────── apme-pod ─────────────────────────────┐
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │ │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │ │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│  ┌────┴─────────────────────────────────────┐  ┌──────────┐          │
│  │         Galaxy Proxy :8765 (PEP 503)     │  │ Abbenay  │          │
│  └──────────────────────────────────────────┘  │  :50057  │          │
│  ┌──────────────────────┐  ┌──────────┐        └──────────┘          │
│  │ Gateway :50060/:8080 │  │ UI :8081 │                              │
│  │ REST + gRPC + DB     │  │ (nginx)  │                              │
│  └──────────────────────┘  └──────────┘                              │
└────────────────────────────────────────────────────────────────────────┘
```

Full details: [architecture.md](/.sdlc/context/architecture.md) | [deployment.md](/.sdlc/context/deployment.md)

## Key ADRs

| ADR | Decision |
|-----|----------|
| [ADR-001](/.sdlc/adrs/ADR-001-grpc-communication.md) | gRPC for all inter-service communication |
| [ADR-003](/.sdlc/adrs/ADR-003-vendor-ari-engine.md) | Vendored ARI engine (NOT a pip dependency) |
| [ADR-007](/.sdlc/adrs/ADR-007-async-grpc-servers.md) | Async gRPC (grpc.aio) for all servers |
| [ADR-008](/.sdlc/adrs/ADR-008-rule-id-conventions.md) | Rule IDs: L=Lint, M=Modernize, R=Risk, P=Policy, SEC=Secrets |
| [ADR-009](/.sdlc/adrs/ADR-009-remediation-engine.md) | Validators are read-only; remediation is separate |

Full list: `.sdlc/adrs/README.md`

## Spec-Driven Development

All features follow: **Spec First → DR for Questions → ADR for Decisions → Traceability**

| Skill | Purpose |
|-------|---------|
| `/sdlc-status` | Dashboard: REQ/DR/ADR status and blockers |
| `/req-new` | Create requirement spec |
| `/task-new` | Create implementation task |
| `/dr-new` | Capture blocking question |
| `/adr-new` | Document architectural decision |

Full workflow: [workflow.md](/.sdlc/context/workflow.md) | Getting started: [getting-started.md](/.sdlc/context/getting-started.md)

## Agent Constraints

- **Follow SOP** — [SOP.md](SOP.md) is the consolidated operating procedures reference; follow it for security, workflow, code quality, CI, git, and release processes
- **tox is the only way to run things** (ADR-047) — `tox -e lint` for quality gates, `tox -e unit` for tests, `tox -e grpc` for proto codegen, `tox -e build`, `up`, `down`, and `cli` for containers. Never run `pytest`, `ruff`, `mypy`, `prek`, or shell scripts directly. See `/tox` skill.
- **Follow ADRs** — no deviation without a new ADR
- **Validators are read-only** — detection only, no file modification; user-facing **check** is read-only, while **remediate** is a separate write path (not validator code)
- **Use gRPC** — all inter-service communication
- **Async servers** — grpc.aio, not synchronous
- **Rule IDs** — L/M/R/P/SEC convention per ADR-008
- **Engine-core services are required** — Primary, Native, OPA, Ansible, and Galaxy Proxy are all required for both the CLI daemon and pod. Their deps are core, not optional extras. Only Gitleaks is optional (external binary). Gateway, UI, and Abbenay are pod-level/enterprise services the CLI daemon does not start.
- Do NOT modify files outside task scope
- Do NOT add features not in requirements
- Ask for clarification if specs are ambiguous

## Quality Gates

Before completing any task, run via **tox only** (ADR-047):
- [ ] `tox -e lint` passes (style, types, docstrings)
- [ ] `tox -e unit` passes (tests with coverage)
- [ ] `tox -e grpc` run after any proto changes
- [ ] TASK verification steps completed

## Security

See [SECURITY.md](/SECURITY.md) for comprehensive guidelines.

**Quick reminders:** prek hooks enforce ruff/mypy/pydoclint on commit. Never commit `.env`. Containers run non-root. Log `[REDACTED]` not secrets.

## Container Rebuild Rules

Rebuild required after modifying: `src/**/*.py`, `proto/**/*.proto`, `pyproject.toml`, `containers/**`

**Workflow:** `tox -e down` → `tox -e build` → `tox -e up`

**No rebuild:** `docs/*.md`, `.sdlc/**/*.md`

## Release Process

**Update:** `pyproject.toml` version, `CHANGELOG.md`, container tags

**Checklist:** Tests pass → Security audit green → CHANGELOG updated → Version bumped → Tag `vX.Y.Z` → Images pushed

## References

- [architecture.md](/.sdlc/context/architecture.md) — Container topology, ports, concurrency
- [deployment.md](/.sdlc/context/deployment.md) — Podman pod setup
- [conventions.md](/.sdlc/context/conventions.md) — Coding standards
- [SECURITY.md](/SECURITY.md) — Security policy
- [SOP.md](/SOP.md) — Consolidated standard operating procedures
- [CONTRIBUTING.md](/CONTRIBUTING.md) — Development workflow
