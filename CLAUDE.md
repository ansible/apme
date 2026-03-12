# APME - Ansible Policy & Modernization Engine

## Project Constitution

This document is the authoritative source of truth for AI agents. All development must align with these principles.

## Overview

APME is a multi-service system that automates policy enforcement and modernization of Ansible content for AAP 2.5+. Services: Primary Orchestrator, Native/OPA/Ansible/Gitleaks Validators, Remediation Engine, CLI.

## Architecture

```
┌──────────────────────────────── apme-pod ─────────────────────────────┐
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Primary  │  │  Native  │  │   OPA    │  │ Ansible  │  │ Gitleaks │ │
│  │  :50051  │  │  :50055  │  │  :50054  │  │  :50053  │  │  :50056  │ │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│  ┌────┴─────────────────────────────────────┐                         │
│  │         Cache Maintainer :50052          │                         │
│  └──────────────────────────────────────────┘                         │
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

- **Follow ADRs** — no deviation without a new ADR
- **Validators are read-only** — detection only, no file modification
- **Use gRPC** — all inter-service communication
- **Async servers** — grpc.aio, not synchronous
- **Rule IDs** — L/M/R/P/SEC convention per ADR-008
- Do NOT modify files outside task scope
- Do NOT add features not in requirements
- Ask for clarification if specs are ambiguous

## Quality Gates

Before completing any task:
- [ ] All unit tests pass
- [ ] Code follows style guidelines ([conventions.md](/.sdlc/context/conventions.md))
- [ ] gRPC changes regenerated (`scripts/gen_grpc.sh`)
- [ ] TASK verification steps completed

## Security

See [SECURITY.md](/SECURITY.md) for comprehensive guidelines.

**Quick reminders:** Pre-commit hooks enforce gitleaks/bandit. Never commit `.env`. Containers run non-root. Log `[REDACTED]` not secrets.

## Container Rebuild Rules

Rebuild required after modifying: `src/**/*.py`, `validators/**/*.py`, `proto/**/*.proto`, `pyproject.toml`, `Containerfile*`

**Workflow:** `stop` → `build` → `start`

**No rebuild:** `docs/*.md`, `.sdlc/**/*.md`

## Release Process

**Update:** `pyproject.toml` version, `CHANGELOG.md`, container tags

**Checklist:** Tests pass → Security audit green → CHANGELOG updated → Version bumped → Tag `vX.Y.Z` → Images pushed

## References

- [architecture.md](/.sdlc/context/architecture.md) — Container topology, ports, concurrency
- [deployment.md](/.sdlc/context/deployment.md) — Podman pod setup
- [conventions.md](/.sdlc/context/conventions.md) — Coding standards
- [SECURITY.md](/SECURITY.md) — Security policy
- [CONTRIBUTING.md](/CONTRIBUTING.md) — Development workflow
