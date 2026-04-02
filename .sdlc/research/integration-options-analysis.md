# APME Integration Options Analysis

**Status**: Current
**Date**: 2026-04-01

## Objective

Document the consumption and integration patterns for APME across different deployment scenarios. This analysis covers standalone usage, CI/CD integration, AAP platform integration, and platform engineering solutions like Backstage/RHDH. It also addresses metrics and analytics requirements for tracking remediation success rates.

---

## What APME Does

APME (Ansible Policy & Modernization Engine) is a static and semi-static analysis platform for Ansible content. It parses playbooks, roles, and collections into a structured hierarchy and validates against 153 rules via four independent backends (see `docs/RULE_CATALOG.md` for the authoritative list):

| Backend | Rules | Purpose |
|---------|-------|---------|
| **OPA/Rego** | 48 rules, 17 fixers | Structural policy rules |
| **Native Python** | 97 rules, 3 fixers | Semantic analysis rules |
| **Ansible-runtime** | 7 rules, 4 fixers | Version compatibility, deprecation |
| **Gitleaks** | 1 rule (800+ secret patterns) | Secret detection |

Rule IDs use prefixes per ADR-008: L = Lint, M = Modernize, R = Risk, P = Policy, SEC = Secrets. IDs are sequential and independent of which validator implements them.

**Key questions APME answers:**
- Will this playbook parse on target ansible-core version?
- Are modules removed, deprecated, or redirected?
- Do module arguments match the argspec?
- Does code follow organizational policies?
- Are there hardcoded secrets?

**What APME is NOT:** A test framework, deployment tool, or runtime verification system. It cannot tell if a playbook achieves its desired outcome—only if it's structurally valid and meets policy gates.

---

## Integration Options

### Option 1: Standalone CLI (Developer Workstation)

**Persona**: Individual developer, contributor, automation author testing before commit

**How it works**:
- CLI auto-spawns local daemon on first use (ADR-024)
- Daemon runs Primary + Native + OPA + Ansible validators and Galaxy Proxy on localhost gRPC (only Gitleaks is optional — requires external binary)
- No containers required — the daemon provides the same validation as the pod minus Gitleaks, Gateway, UI, and Abbenay
- State persisted in `~/.apme-data/daemon.json`

**Commands**:
```bash
apme check /path/to/playbook        # Validate, show violations
apme remediate /path/to/project     # Auto-fix Tier 1, propose Tier 2
apme format --apply .               # Normalize YAML
apme daemon status                  # Check daemon health
```

**Why use this**:
- Zero infrastructure setup
- Fast feedback loop during development
- IDE integration potential (LSP server path)
- Pre-commit hook integration (`apme check --diff`)

**Gaps to address**:
- No `--skip-rules` / `--exclude-rules` flag (file-based `.apmeignore` only)
- No `[tool.apme]` config in `pyproject.toml`

---

### Option 2: Containerized CLI / Pod (CI/CD & Shared Infrastructure)

**Persona**: CI pipeline, shared validation service, team server

**How it works**:
- Full pod with 9 containers (Primary, Native, OPA, Ansible, Gitleaks, Galaxy-Proxy, Gateway, UI, Abbenay)
- CLI connects via `APME_PRIMARY_ADDRESS` env var
- Gateway provides REST API + persistence

**Deployment**:
```bash
tox -e build                        # Build all images
tox -e up                           # Start pod
APME_PRIMARY_ADDRESS=localhost:50051 apme check .
```

**Why use this**:
- Adds Gitleaks secrets scanning (optional in standalone daemon)
- Gateway persistence, REST API, and Dashboard for portfolio visibility
- Multi-user support with project management
- UI for browsing scan results, trends, and violations
- Abbenay AI provider for Tier 2 remediation

---

### Option 3: CI/CD Pipeline Integration

**Persona**: DevOps engineer, platform team enforcing policy gates

**Patterns**:

#### 3a. GitHub Actions / GitLab CI Gate
```yaml
# .github/workflows/apme-check.yml
jobs:
  apme-check:
    runs-on: ubuntu-latest
    container: ghcr.io/ansible/apme-cli:latest
    steps:
      - uses: actions/checkout@v4
      - run: apme check . --json > apme-report.json
      - name: Fail if violations found
        run: test "$(jq '.count' apme-report.json)" -eq 0
```

#### 3b. Self-Hosted APME Service (Future)
A centralized APME pod that CI pipelines call into, rather than running the CLI in each repo:
- Gateway already supports scan triggering via WebSocket (`/projects/{id}/ws/operate`)
- A REST `POST` trigger endpoint (e.g., `POST /api/v1/projects/{id}/scans`) would better suit CI/CD (planned, not yet implemented)
- Project health is available via `GET /api/v1/projects/{id}` (`health_score` field)
- Webhook callback on completion (ADR-038 planned feature)

#### 3c. Pre-commit Hook
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: apme-check
        name: APME Validation
        entry: apme check --diff
        language: system
        types: [yaml]
```

**Why use this**:
- Shift-left validation before merge
- Consistent policy enforcement across repos
- Integration with existing CI infrastructure
- PR decoration with violation comments (future enhancement)

**AAP consideration**: AAP project sync from SCM means CI validation before sync ensures clean content reaches Controller.

---

### Option 4: AAP Integration (Controller, EDA, Hub)

**Persona**: AAP administrator, automation architect

**Existing AAP infrastructure** (from AAP codebase analysis):

| Component | Location | Purpose |
|-----------|----------|---------|
| OPA policy integration | `/awx/main/tasks/policy.py` | Evaluates policies at job launch |
| Webhook system | `/awx/api/views/webhooks.py` | Receives external events |
| Prometheus metrics | `/awx/api/views/metrics.py` | Exposes operational data |
| Notification backends | `/awx/main/models/notifications.py` | 10+ channels (Slack, email, webhook) |
| Analytics collectors | `/awx/main/analytics/collectors.py` | Metrics for AA |

**Integration patterns**:

#### 4a. Pre-Flight Validation (Recommended Near-Term)
Controller/EDA queries APME before job execution:
```
Project Sync → APME check → health_score < threshold? → Block execution
```

ADR-038 defines the API surface. Today `GET /api/v1/projects/{id}` returns `health_score`; a `?repo_url=` query filter is planned but not yet implemented.

#### 4b. Policy Augmentation (Recommended)
APME provides rich parsing context to Controller's existing OPA:
- APME parses playbook → extracts modules, args, dependencies
- Sends to Controller's OPA endpoint as extended input
- Controller evaluates org-specific policies with full context

This aligns with DR-015 (Controller Policy Integration) Option B.

#### 4c. EDA Rulebook Validation
DR-014 recommends: EDA calls APME during rulebook import, marks invalid rulebooks.
- Rulebook-specific validation rules would need to be added (no EDA-specific rules exist today)
- Surfaces validation in EDA UI before activation

#### 4d. Hub Content Validation
Before publishing to Hub, validate collections via APME.
- Integrates at publish workflow
- Ensures distributed content meets standards

**Open decisions**:
| DR | Question | Recommendation |
|----|----------|----------------|
| DR-013 | How AA gets deprecated module data | Via Controller telemetry |
| DR-014 | EDA integration timing | Import-time validation |
| DR-015 | Policy engine consolidation | APME augments (near-term) → replaces (long-term) |

---

### Option 5: Platform Engineering (Backstage / RHDH Self Service Portal)

**Persona**: Platform engineer, self-service consumer

**AAP Backstage plugins** (existing in AAP codebase):

| Plugin | Purpose |
|--------|---------|
| `auth-backend-module-rhaap-provider` | OAuth 2.0 auth with AAP |
| `catalog-backend-module-rhaap` | Job templates → Software Templates sync |
| `scaffolder-backend-module-backstage-rhaap` | `rhaap:launch-job-template` action |
| `self-service` | UI components |

**APME integration patterns**:

#### 5a. Software Template Pre-Validation
Before scaffolder launches job template:
```
User selects template → APME validates → Pass? → Launch job
                                       → Fail? → Show violations, block
```

#### 5b. Catalog Health Badges
RHDH catalog displays APME health score per project:
- Badge: "Clean" (90+), "Warning" (60-89), "Critical" (<60)
- Links to APME dashboard for details

#### 5c. RHDH Plugin (Future)
ADR-030 defines: Same Gateway API, but rendered in RHDH context.
- Inherits RHDH auth, RBAC, software catalog
- No separate APME login

**Why use this**:
- Unified developer portal experience
- Self-service with guardrails
- Consistent policy enforcement across scaffolding

---

### Option 6: Additional Consumption Patterns

| Pattern | Persona | Description | Status |
|---------|---------|-------------|--------|
| **IDE Integration (LSP)** | Developer | VS Code/JetBrains real-time squiggles, quick-fix actions | Not started |
| **Ansible Navigator** | User | `ansible-navigator lint` delegating to APME | Not started |
| **EE Builder** | EE author | Validate `execution-environment.yml` before build | Not started |
| **AWX SDK clients** | Developer | Python/Go libraries calling APME before job submit | Not started |

---

## Metrics & Analytics Strategy

### Requirements

1. Track validation volume, pass/fail rates
2. Measure remediation adoption (Tier 1 auto-applied, Tier 2 accepted/rejected)
3. Show improvement trends over time
4. ROI metrics: time saved, issues prevented

### Current Architecture

ADR-020 defines best-effort event delivery:

```
Engine → ScanCompleted event → Reporting Service → Gateway SQLite
                                                 → (Future: Prometheus, Elasticsearch)
```

**Event payload includes**:
- Violation counts by rule/severity
- Proposal outcomes (approved/rejected)
- Project manifest (SBOM data)
- Pipeline milestone logs

### AAP Automation Dashboard Integration

**Current state**: Dashboard is standalone installation (assumed integrated in future AAP releases)

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A. Via Controller Telemetry** | Controller queries APME, includes in AA telemetry | Uses existing pipeline | Controller changes needed |
| **B. Direct AA Integration** | AA queries APME Gateway directly | No Controller changes | New integration surface |
| **C. Webhook Push** | APME pushes events to Dashboard ingestion | Real-time updates | Dashboard API required |

**Recommendation**: Option A (Via Controller Telemetry)

Aligns with ADR-038 "Chuck Wagon Principle": APME serves data, consumers come get it.

### Implementation Roadmap

| Phase | Scope | Metrics Path |
|-------|-------|--------------|
| **V1 (Current)** | APME Dashboard shows health, trends, rankings | Gateway SQLite |
| **AAP (future)** | Controller queries APME during project sync | Via Controller → AA |
| **Future** | Prometheus exporter, Grafana templates, ROI calculator | Multiple sinks |

---

## Integration Matrix Summary

| Option | Persona | Complexity | Metrics Path | Status |
|--------|---------|------------|--------------|--------|
| **1. Standalone CLI** | Developer | Low | Local only | Ready |
| **2. Containerized Pod** | Team/CI | Medium | Gateway SQLite | Ready |
| **3. CI/CD Gate** | DevOps | Low | CI artifacts | Ready |
| **4a. AAP Pre-flight** | Admin | Medium | Via Controller | DR-015 open |
| **4b. AAP Policy Augment** | Architect | High | Via Controller | DR-015 open |
| **4c. EDA Validation** | EDA Admin | Medium | Via Controller | DR-014 open |
| **5. Backstage Plugin** | Platform | High | Via Gateway | ADR-030 path defined |
| **6. IDE/LSP** | Developer | Medium | Local daemon | Not started |

---

## Recommended Next Steps

1. **Document consumption patterns** — Add "How to Use APME" section to README covering options 1-3
2. **Resolve open DRs** — DR-013, DR-014, DR-015 block AAP integration decisions
3. **Implement `--skip-rules`** — Enables enterprise customization (gap in Option 1)
4. **Add webhook notifications** — ADR-038 planned feature for CI/CD callbacks
5. **Prototype AAP pre-flight** — Option 4a is lowest-friction AAP integration

---

## Related Artifacts

### ADRs
| ADR | Title | Relevance |
|-----|-------|-----------|
| ADR-020 | Reporting Service | Event delivery model |
| ADR-024 | Thin CLI Daemon Mode | Standalone CLI architecture |
| ADR-029 | Web Gateway Architecture | External integration point |
| ADR-030 | Frontend Deployment Model | Standalone vs Backstage |
| ADR-037 | Project-Centric UI Model | Session hiding, project abstraction |
| ADR-038 | Public Data API | Consumer interaction patterns |
| ADR-042 | Third-Party Plugin Services | Extensibility model |

### Decision Requests
| DR | Question | Status |
|----|----------|--------|
| DR-013 | AA Integration Approach | Open |
| DR-014 | EDA Integration Approach | Open |
| DR-015 | Controller Policy Integration | Open |

### Research
| Document | Relevance |
|----------|-----------|
| [ui-capabilities-assessment.md](ui-capabilities-assessment.md) | Gateway API surface, UI capabilities |
| [rfe-coverage-mapping.md](rfe-coverage-mapping.md) | Customer RFE requirements |

---

## AAP Codebase References

These files from `~/Downloads/aap-repo-code` inform the integration analysis:

| File | Purpose |
|------|---------|
| `non-operator/tower/awx/main/tasks/policy.py` | OPA policy integration pattern |
| `non-operator/tower/awx/api/views/metrics.py` | Prometheus metrics endpoint |
| `non-operator/tower/awx/api/views/webhooks.py` | Webhook receiver pattern |
| `non-operator/tower/awx/main/analytics/collectors.py` | Metrics collector architecture |
| `non-operator/tower/awx/main/models/notifications.py` | Notification backend extensibility |
| `docs/aap-docs/downstream/modules/devtools/ref-devtools-ansible-backstage-plugins.adoc` | Backstage plugin documentation |
