# Architecture Decision Records

This directory contains the Architecture Decision Records (ADRs) for APME.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-001](ADR-001-grpc-communication.md) | gRPC for Inter-Service Communication | Accepted | 2026-02 |
| [ADR-002](ADR-002-opa-rego-policy.md) | OPA/Rego for Declarative Policy Rules | Accepted | 2026-02 |
| [ADR-003](ADR-003-vendor-ari-engine.md) | Vendor the ARI Engine | Accepted | 2026-02 |
| [ADR-004](ADR-004-podman-pod-deployment.md) | Podman Pod as Deployment Unit | Accepted | 2026-02 |
| [ADR-005](ADR-005-no-service-discovery.md) | No etcd/Service Discovery | Accepted | 2026-02 |
| [ADR-006](ADR-006-ephemeral-venvs.md) | Ephemeral Per-Request venvs | Accepted | 2026-03 |
| [ADR-007](ADR-007-async-grpc-servers.md) | Fully Async gRPC Servers | Accepted | 2026-03 |
| [ADR-008](ADR-008-rule-id-conventions.md) | Rule ID Conventions (L/M/R/P) | Accepted | 2026-02 |
| [ADR-009](ADR-009-remediation-engine.md) | Separate Remediation Engine | Accepted | 2026-03 |
| [ADR-010](ADR-010-gitleaks-validator.md) | Gitleaks as gRPC Validator | Accepted | 2026-03 |
| [ADR-011](ADR-011-yaml-formatter-prepass.md) | YAML Formatter as Phase 1 Pre-Pass | Accepted | 2026-03 |
| [ADR-012](ADR-012-scale-pods-not-services.md) | Scale Pods, Not Services | Accepted | 2026-02 |
| [ADR-013](ADR-013-structured-diagnostics.md) | Structured Diagnostics in gRPC | Accepted | 2026-03 |
| [ADR-014](ADR-014-ruff-prek-hooks.md) | Ruff Linter and prek Pre-commit Hooks | Accepted | 2026-03 |
| [ADR-015](ADR-015-github-actions-prek.md) | GitHub Actions CI with prek | Accepted | 2026-03 |
| [ADR-016](ADR-016-single-branch-main.md) | Single-branch `main` Strategy | Accepted | 2026-03 |
| [ADR-017](ADR-017-trust-and-verify-agent-sdlc.md) | Trust-and-verify Agent SDLC Invocation | Accepted | 2026-03 |
| [ADR-018](ADR-018-mypy-strict-type-checking.md) | mypy Strict Mode Type Checking | Accepted | 2026-03 |
| [ADR-019](ADR-019-dependency-governance.md) | Dependency Governance Policy | Accepted | 2026-03 |
| [ADR-020](ADR-020-reporting-service.md) | Reporting Service and Event Delivery Model | Proposed | 2026-03 |
| [ADR-021](ADR-021-proactive-pr-feedback.md) | Proactive PR Feedback via GitHub Actions | Accepted | 2026-03 |
| [ADR-022](ADR-022-session-scoped-venvs.md) | Session-Scoped Venvs with Lifecycle Management | Accepted | 2026-03 |
| [ADR-023](ADR-023-per-finding-classification.md) | Per-Finding Remediation Classification and Resolution | Accepted | 2026-03 |
| [ADR-024](ADR-024-thin-cli-daemon-mode.md) | Thin CLI with Local Daemon Mode | Accepted | 2026-03 |
| [ADR-025](ADR-025-ai-provider-protocol.md) | AIProvider Protocol Abstraction | Accepted | 2026-03 |
| [ADR-026](ADR-026-rule-scope-metadata.md) | Rule Scope as First-Class Metadata | Proposed | 2026-03 |
| [ADR-027](ADR-027-agentic-project-remediation.md) | Agentic Project-Level AI Remediation | Proposed | 2026-03 |
| [ADR-028](ADR-028-session-based-fix-workflow.md) | Session-Based Fix Workflow with Bidirectional Streaming | Accepted | 2026-03 |
| [ADR-029](ADR-029-web-gateway-architecture.md) | Web Gateway Architecture | Proposed | 2026-03 |
| [ADR-030](ADR-030-frontend-deployment-model.md) | Frontend Deployment Model | Proposed | 2026-03 |

## Categories

### Communication & Infrastructure
- ADR-001: gRPC communication
- ADR-004: Podman pod deployment
- ADR-005: No service discovery
- ADR-012: Scaling strategy
- ADR-020: Reporting service and event delivery model (proposed)
- ADR-024: Thin CLI with local daemon mode
- ADR-028: Session-based fix workflow with bidirectional streaming
- ADR-029: Web gateway architecture (proposed)

### Engine & Rules
- ADR-002: OPA/Rego hybrid rules
- ADR-003: Vendored ARI engine
- ADR-008: Rule ID conventions
- ADR-026: Rule scope metadata (proposed)

### Validators
- ADR-006: Ansible validator venvs
- ADR-007: Async gRPC servers
- ADR-010: Gitleaks validator
- ADR-013: Structured diagnostics
- ADR-022: Session-scoped venvs with lifecycle management

### Remediation
- ADR-009: Remediation engine architecture
- ADR-011: YAML formatter pre-pass
- ADR-023: Per-finding remediation classification and resolution
- ADR-025: AIProvider protocol abstraction
- ADR-027: Agentic project-level AI remediation (proposed)
- ADR-028: Session-based fix workflow with bidirectional streaming

### Dashboard & Presentation
- ADR-029: Web gateway architecture (proposed)
- ADR-030: Frontend deployment model (proposed)

### Tooling & CI
- ADR-014: Ruff linter and prek pre-commit hooks
- ADR-015: GitHub Actions CI with prek

### Process
- ADR-016: Single-branch `main` strategy
- ADR-017: Trust-and-verify agent SDLC invocation
- ADR-019: Dependency governance policy
- ADR-021: Proactive PR feedback via GitHub Actions

## Archived

Original planning ADRs that were superseded by implementation decisions:

| ADR | Title | Status |
|-----|-------|--------|
| [archive/ADR-001-tech-stack.md](archive/ADR-001-tech-stack.md) | Initial Tech Stack | Superseded |
| [archive/ADR-002-langgraph-agents.md](archive/ADR-002-langgraph-agents.md) | LangGraph Agents | Superseded |
| [archive/ADR-003-ari-integration.md](archive/ADR-003-ari-integration.md) | ARI Wrapper Approach | Superseded by ADR-003 |

## Creating New ADRs

1. Copy the template from `../.sdlc/templates/adr.md`
2. Use the next available number (currently ADR-031)
3. Include:
   - Status (Proposed → Accepted)
   - Date
   - Context
   - Options Considered
   - Decision
   - Rationale (include user quotes if available)
   - Consequences (positive/negative)
   - Implementation Notes
   - Related Decisions

## Changelog

| ADR | Date | Summary |
|-----|------|---------|
| 001 | 2026-02 | gRPC for inter-service communication |
| 002 | 2026-02 | OPA/Rego for declarative policy rules |
| 003 | 2026-02 | Vendor ARI engine, full integration |
| 004 | 2026-02 | Podman pod as deployment unit |
| 005 | 2026-02 | Reject etcd/service discovery |
| 006 | 2026-03 | Ephemeral per-request venvs |
| 007 | 2026-03 | Fully async gRPC servers (grpc.aio) |
| 008 | 2026-02 | Rule ID conventions (L/M/R/P) |
| 009 | 2026-03 | Separate remediation engine |
| 010 | 2026-03 | Gitleaks as gRPC validator |
| 011 | 2026-03 | YAML formatter as Phase 1 pre-pass |
| 012 | 2026-02 | Scale pods, not services |
| 013 | 2026-03 | Structured diagnostics in gRPC contract |
| 014 | 2026-03 | Ruff linter and prek pre-commit hooks |
| 015 | 2026-03 | GitHub Actions CI with prek |
| 016 | 2026-03 | Single-branch `main` strategy |
| 017 | 2026-03 | Trust-and-verify agent SDLC invocation |
| 018 | 2026-03 | mypy strict mode type checking |
| 019 | 2026-03 | Dependency governance policy |
| 020 | 2026-03 | Reporting service and event delivery model (proposed) |
| 021 | 2026-03 | Proactive PR feedback via GitHub Actions |
| 022 | 2026-03 | Session-scoped venvs with lifecycle management |
| 023 | 2026-03 | Per-finding remediation classification and resolution |
| 024 | 2026-03 | Thin CLI with local daemon mode |
| 025 | 2026-03 | AIProvider protocol abstraction |
| 026 | 2026-03 | Rule scope as first-class metadata (proposed) |
| 027 | 2026-03 | Agentic project-level AI remediation (proposed) |
| 028 | 2026-03 | Session-based fix workflow with bidirectional streaming |
| 029 | 2026-03 | Web gateway architecture (proposed) |
| 030 | 2026-03 | Frontend deployment model (proposed) |