# Project Overview: APME

## What is APME?

The **Ansible Policy & Modernization Engine (APME)** is a multi-validator static analysis platform for Ansible content. It parses playbooks, roles, collections, and task files into a structured hierarchy, then fans validation out in parallel across independent backends to produce a unified list of violations — with optional automated remediation.

## Problem Statement

Organizations with large Ansible codebases face significant challenges when upgrading across ansible-core versions:

1. **Module lifecycle**: Modules are deprecated, removed, or redirected across versions; argument specs change
2. **Syntax evolution**: Includes become imports, short-form module names require FQCNs, connection plugins change
3. **Security compliance**: Hardcoded credentials, policy violations, and supply-chain risks in dependencies
4. **Scale**: Manual audit and remediation does not scale for hundreds of roles and collections

## Solution

APME provides:

1. **Multi-backend static analysis**: Six validators (Native Python, OPA/Rego, Ansible runtime, Gitleaks secrets, Collection Health, Dependency Audit) run in parallel behind a unified gRPC contract
2. **Automated remediation**: Deterministic Tier 1 transforms with multi-pass convergence, plus optional AI-assisted Tier 2 fixes via Abbenay
3. **YAML formatting**: Comment-preserving normalization (indentation, key ordering, Jinja spacing)
4. **Web UI**: React dashboard for project management, live operations, and cross-project analytics
5. **CI/CD integration**: JSON, SARIF output; pre-commit hooks; GitHub Actions workflows
6. **Multiple deployment targets**: CLI (pip install), Podman pod, bootc VM, Helm chart

## Scope

### In Scope

- Module compatibility checking (deprecated, removed, redirected, argspec validation)
- FQCN detection and conversion
- Syntax modernization across ansible-core versions
- Secret scanning (800+ patterns via Gitleaks)
- Dependency health (collection quality + Python CVE audit)
- Organizational policy enforcement (OPA/Rego rules)
- Automated remediation (deterministic transforms + AI-assisted fixes)
- YAML formatting with comment preservation
- Web dashboard with persistent scan history
- CI/CD integration (JSON, SARIF, pre-commit, GitHub Actions)
- Multiple deployment methods (CLI, Podman, bootc, Helm)

### Out of Scope

- Custom module development
- Playbook logic changes (only structural/compatibility)
- Performance optimization of playbooks
- Runtime verification (whether playbooks achieve desired state)
- Inventory management

## Target Users

1. **Platform Engineers**: Managing AAP infrastructure upgrades across ansible-core versions
2. **DevOps Teams**: Maintaining playbook repositories with quality gates in CI
3. **Automation Architects**: Planning migration strategies with full project visibility
4. **Security Teams**: Enforcing credential hygiene and dependency health policies

## Architecture

APME runs as a set of cooperating containers. In **Podman**, **bootc**, and
**Kubernetes** (Helm Simple / ADR-069), the stack co-locates on localhost in one
pod (engine + Gateway + UI + optional Abbenay). The CLI can also run as a
standalone daemon for quick evaluation.

Key services:
- **Primary** (:50051) — orchestrator, engine, session venv manager
- **Native** (:50055) — Python rule validator
- **OPA** (:50054) — Rego rule validator (subprocess, not REST)
- **Ansible** (:50053) — runtime checks against session venvs
- **Gitleaks** (:50056) — secret scanner
- **Collection Health** (:50058) — installed collection quality checks
- **Dep Audit** (:50059) — Python CVE audit via pip-audit
- **Galaxy Proxy** (:8765) — PEP 503 collection → wheel proxy
- **Gateway** (:8080/:50060) — REST API + persistence
- **UI** (:8081) — React dashboard
- **Abbenay** (:50057) — AI provider for Tier 2 remediation

See [architecture.md](architecture.md) for the full topology and contracts.

## Related Projects

- **ansible-lint**: Complementary linting tool (different rule set and approach)
- **Abbenay**: AI provider daemon for Tier 2 remediation
- **Molecule**: Execution-time testing (complementary to APME's static analysis)
