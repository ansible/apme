# ADR-051: Dependency Health Scanning

## Status

Accepted

## Date

2026-04-07

## Context

APME scans Ansible content — playbooks, roles, taskfiles — but today ignores
the health of the dependencies that content relies on. A project may pass all
lint and modernization rules while depending on a collection riddled with
anti-patterns or a Python package with known CVEs. Two distinct risks are
invisible:

1. **Collection quality risk.** A project that `requirements.yml` pins
   `community.general==8.0.0` inherits whatever quality issues exist inside
   that collection's roles, modules, and plugins. Today the engine scans only
   the project's own content; it never opens the collections installed in the
   session venv. A "clean" project can still break at runtime because a
   dependency uses deprecated module options, hardcodes `command` instead of
   purpose-built modules, or ships roles with missing `argument_specs`.

2. **Python supply-chain risk.** The session venv contains `ansible-core`,
   collection Python dependencies, and transitive PyPI packages. Any of these
   can have published CVEs. The engine already enumerates installed packages
   (`list_installed_packages`) and captures them in `ProjectManifest`
   (ADR-040), but never checks them against a vulnerability database.

### What already exists

| Capability | Location | Gap |
|------------|----------|-----|
| Collection installation in session venvs | `VenvSessionManager` | Collections installed but not scanned |
| `list_installed_collections(venv)` | `venv_manager/session.py` | Returns FQCN + version + supplier + license; no quality data |
| `list_installed_packages(venv)` | `venv_manager/session.py` | Returns name + version + supplier + license; no CVE data |
| `get_dependency_tree(venv)` | `venv_manager/session.py` | `uv pip tree` text; informational only |
| `ProjectManifest` in `FixCompletedEvent` | ADR-040, `engine_server.py` | Inventory without health assessment |
| Gateway persistence of manifest | ADR-040, `grpc_reporting` | Stores packages but no vulnerability metadata |
| Gitleaks optional validator pattern | `gitleaks_validator_server.py` | Template for external-binary validator |
| Engine scan pipeline (`FixSession`) | `engine_server.py` | Can scan any project root — collections are structurally similar |
| `RuleScope.COLLECTION` in proto | `common.proto` | Already defined for collection-scoped findings |
| `SEC:` rule ID prefix | ADR-008 | Reserved for secrets; supply-chain risk needs convention |

### Forces

- Collections are immutable at a given version — scan once, cache forever
- Python CVE databases (OSV, PyPI advisory) are free and well-maintained
- `pip-audit` is already referenced in `SOP.md` and `SECURITY.md` as the
  recommended tool for manual security checks
- REQ-010 designs a sidecar for the enterprise pod; this ADR covers the
  engine-level mechanics that both standalone CLI and sidecar consume
- The engine is stateless (ADR-020) — it should emit findings, not persist them
- Validators are read-only (ADR-009) — scanning collection content and auditing
  packages are both detection-only operations

## Decision

**We will add two new optional validator services — a Collection Health
Validator and a Python Dependency Auditor — that run during the normal
scan fan-out and emit findings alongside existing validators.**

Both follow the established Gitleaks pattern: optional gRPC validators,
env-gated addresses, subprocess execution in `run_in_executor()`, graceful
degradation when unavailable.

### Part 1: Collection Health Validator

A new validator that scans the Ansible collections installed in the session
venv by running a curated subset of APME's own rules against each collection's
content.

**Mechanism:**

1. Receives `ValidateRequest` with `venv_path` and `collection_specs`.
2. Discovers installed collection paths under `{venv}/lib/python*/site-packages/
   ansible_collections/{namespace}/{name}/`.
3. For each collection, builds a `ContentGraph` of the collection's roles,
   modules, plugins, and metadata — the same loader pipeline the engine uses
   for project content.
4. Runs a **curated rule subset** against the collection graph. The scope is
   limited to **security/runtime risks** and **deprecated patterns** — not
   authoring-quality lint (galaxy metadata, role structure, type hints, FQCN
   hygiene). Findings in dependencies should be actionable signals, not noise
   from code the user does not own. The curated set:
   - **Deprecated patterns:** M005 (Jinja data tagging 2.19+), M010 (Python 2
     interpreter dropped 2.18+)
   - **Risk / security indicators:** R101, R103-R109, R111-R115, R117, R401
     (command execution, transfers, privilege escalation, package install risks,
     file changes, external roles)
   - **Deferred:** M004 (removed modules) requires Ansible introspection, not
     available in the native GraphRule scanner. M006, M008, M009 are OPA rules.
     These will be added when cross-validator wiring is implemented.
5. Emits `Violation` messages scoped to `RuleScope.COLLECTION` with the
   collection FQCN in `metadata["collection_fqcn"]` and
   `metadata["collection_version"]`. The `file` field is relative to the
   collection root, not the project root.

**Rule IDs:** Collection health findings reuse existing rule IDs (M005, R108,
etc.) — the rule logic is the same. The `RuleScope.COLLECTION` and metadata
distinguish "this R108 is in your project" from "this R108 is in a dependency."

**Caching:** Collection content is immutable at a given FQCN+version, but
findings also depend on the scan schema: the engine version, the curated rule
subset, and rule implementation changes. The validator maintains an in-process
LRU cache keyed on `(fqcn, version, cache_schema)` → `list[Violation]`, where
`cache_schema` identifies the current scan semantics (engine version + curated
rule-set hash). Across sessions, a persistent JSON cache under
`~/.apme-data/collection-health/` stores `cache_schema` alongside findings
and reuses an entry only when it matches the current scanner. Cache entries
have no TTL because results are deterministic for a given
`(fqcn, version, cache_schema)` tuple; a `--rescan-deps` flag still forces a
cache bust.

**Scope of scan:** Only collections actually installed in the session venv for
this project. Transitive collection dependencies are included (they are
installed by Galaxy Proxy). The validator does not download anything — it reads
what `VenvSessionManager` already installed.

### Part 2: Python Dependency Auditor

A new validator that checks Python packages in the session venv against
vulnerability databases.

**Mechanism:**

1. Receives `ValidateRequest` with `venv_path`.
2. Runs `pip-audit --json --strict -l --path {venv_site_packages}` in
   `run_in_executor()` to audit installed packages against OSV.dev.
3. Parses JSON output; maps each vulnerability to a `Violation`:
   - `rule_id`: `R200` (supply-chain risk category per ADR-008 `R` prefix)
   - `severity`: Mapped from CVSS score — Critical ≥ 9.0, High ≥ 7.0,
     Medium ≥ 4.0, Low < 4.0
   - `message`: `"{package}=={version} has known vulnerability {CVE-ID}: {description}"`
   - `metadata`: `{"cve_id": "...", "package": "...", "installed_version": "...",
     "fix_versions": "...", "cvss_score": "..."}`
   - `file`: The `requirements.yml` or `requirements.txt` that introduced the
     dependency (if discoverable), otherwise empty
4. `Health` RPC checks that `pip-audit` is on `PATH`, similar to Gitleaks
   checking the `gitleaks` binary.

**Offline mode:** By default, `pip-audit` uses `--vulnerability-source osv`
and queries osv.dev. In the command above, `-l` / `--local` means "audit the
locally installed environment" (not an offline database mode). For offline or
air-gapped environments, vulnerability data distribution is a separate
concern: a pre-populated `pip-audit` cache (`--cache-dir`) and/or a mirrored
vulnerability data source. A future APME enhancement could standardize how
that cache or mirrored vulnerability data is packaged and distributed for
fully air-gapped deployments.

**Rule IDs:** `R200` for known CVE in Python dependency. `R201` reserved for
"package has no maintained release" (future). These are risk findings, not lint
or modernization — the `R` prefix is correct per ADR-008.

### Service registration

Both validators register in `launcher.py` as optional services (alongside
Gitleaks). `_OPTIONAL_SERVICES` is a `dict[str, int]` mapping service names
to default ports:

```python
_OPTIONAL_SERVICES = {
    "gitleaks": 50056,
    "collection_health": 50058,
    "dep_audit": 50059,
}
```

Environment variables:
- `COLLECTION_HEALTH_GRPC_ADDRESS` — address of collection health validator
- `DEP_AUDIT_GRPC_ADDRESS` — address of Python dependency auditor

Engine fans out to these validators in the same `asyncio.gather()` as
existing validators. If the address env var is unset or the service is
unhealthy, the validator is skipped — same as Gitleaks today.

### Wire protocol

Both validators implement the existing `Validator.Validate` +
`Validator.Health` contract (`validate.proto`). No proto changes are needed.
`ValidateRequest` already carries `venv_path`, `collection_specs`,
`content_graph_data`, and `session_id` — sufficient for both validators.

## Alternatives Considered

### Alternative 1: Sidecar-Only (REQ-010 as-is, no engine validators)

**Description**: Implement collection scanning and Python CVE checking
exclusively as a Gateway sidecar (the architecture in REQ-010). The sidecar
queries the Gateway API for dependency lists, runs analysis asynchronously,
and posts results back.

**Pros**:
- Clean separation — engine stays focused on content scanning
- Asynchronous — doesn't add latency to the scan path
- Works against persisted data — can scan historical projects

**Cons**:
- Invisible to standalone CLI users — requires the full pod (or ADR-049
  Gateway-in-daemon) plus the sidecar
- Findings are not part of the scan result — they arrive later, in a separate
  API response, not inline with `apme check` output
- Cannot gate CI on dependency health without polling the sidecar
- Duplicates venv introspection — the engine already has the venv; the sidecar
  would need to recreate or access it

**Why not chosen**: The sidecar model (REQ-010) is valuable for enterprise
periodic scanning and cross-project correlation, but it doesn't serve the
primary UX: `apme check .` should tell you about dependency risks *during the
scan*, not as an afterthought. This ADR adds engine-level validators that
surface findings inline; REQ-010's sidecar can consume and aggregate those
same findings from the Gateway for fleet-wide views.

### Alternative 2: Extend Engine Directly (no new services)

**Description**: Add collection scanning and pip-audit as steps inside
Engine's `_scan_pipeline`, not as separate validator services.

**Pros**:
- No new gRPC services or ports
- Simpler deployment

**Cons**:
- Violates the validator contract pattern — Engine orchestrates, validators
  detect
- Bloats Engine with domain logic (rule execution, pip-audit parsing)
- Not independently scalable or disableable
- Breaks the fan-out model where validators run in parallel

**Why not chosen**: The existing architecture is intentionally decomposed.
Adding detection logic to Engine regresses that design.

### Alternative 3: Native Validator Graph Extension

**Description**: Extend `ContentGraph` to include collection content as
sub-graphs, then run native graph rules across the entire graph (project +
dependencies).

**Pros**:
- Single unified graph — rules see everything
- No new services

**Cons**:
- Massively increases graph size (a single collection can have hundreds of
  modules)
- Rules that are project-scoped would fire on dependency content, creating
  noise
- Performance regression for the common case (most users don't want full
  dependency scanning)
- Opt-in behavior is harder to implement in the graph model

**Why not chosen**: The graph should represent the project under analysis.
Dependencies are a separate concern with different ownership and different
expected quality levels. Mixing them creates more problems than it solves.

## Consequences

### Positive

- **`apme check .` surfaces dependency risks inline.** A single command now
  covers project quality, collection health, and Python supply-chain risk.
- **CI gating on dependency health.** The same exit-code semantics apply —
  if a collection dependency has Critical findings or a Python package has a
  Critical CVE, the check fails.
- **Reuses existing infrastructure.** No new proto messages, no new RPCs.
  Both validators implement the standard `Validator` contract. Collection
  scanning reuses the same loader and rules the engine already has.
- **Follows established patterns.** Gitleaks proved that optional,
  external-binary validators work. This adds two more in the same mold.
- **Caching makes it cheap.** Collection scans are cached by FQCN+version.
  pip-audit queries are fast (~2-5s for a typical venv). Neither adds
  significant latency after the first run.
- **Complements REQ-010.** The sidecar can aggregate per-project dependency
  findings (emitted by these validators via `FixCompletedEvent`) for
  fleet-wide dashboards without duplicating scan logic.

### Negative

- **Two new optional services.** More ports, more processes in the daemon,
  more containers in the pod. Mitigated by being fully optional (env-gated).
- **pip-audit is a new external dependency.** Must be installed in the
  container image. If missing, the validator gracefully skips (like Gitleaks
  without the binary).
- **Collection scanning adds latency on first run.** Scanning 5-10
  collections with a curated rule set may add 10-30 seconds. Mitigated by
  persistent caching — subsequent scans for the same collection+version are
  instant.
- **Rule noise.** Collection maintainers may not follow the same standards
  as project authors. Findings in dependencies may be numerous and
  un-actionable. Mitigated by separate `RuleScope.COLLECTION` allowing
  UI/CLI filtering, and by the curated rule subset (not all rules run
  against collections).

### Neutral

- REQ-010's sidecar design is not superseded — it adds fleet-wide
  aggregation, historical scanning, and cross-project correlation. This ADR
  provides the per-scan inline mechanism; REQ-010 provides the enterprise
  view.
- The `ProjectManifest` (ADR-040) is unchanged. Dependency health findings
  flow as normal `Violation` messages, not manifest fields. Health scores
  remain a Gateway/sidecar concern.
- Remediation for dependency findings is out of scope. A finding that says
  "community.general has deprecated module usage" is informational — the user
  can't fix it (the collection maintainer can). A finding that says
  "jmespath==1.0.0 has CVE-2025-XXXXX" may suggest a version bump, but
  automated remediation of transitive dependencies is a separate problem.

## Implementation Notes

### New source layout

```
src/apme_engine/
├── daemon/
│   ├── collection_health_server.py    # gRPC servicer
│   └── dep_audit_server.py            # gRPC servicer
└── validators/
    ├── collection_health/
    │   ├── __init__.py
    │   ├── scanner.py                 # Collection graph build + rule execution
    │   └── cache.py                   # FQCN+version → findings cache
    └── dep_audit/
        ├── __init__.py
        └── auditor.py                 # pip-audit subprocess + output mapping
```

### Collection Health Validator — key design points

- The scanner creates a **temporary `ContentGraph`** per collection, using the
  same `model_loader` + `ContentGraph` pipeline the engine uses. The collection
  root under `site-packages` is treated as a mini-project.
- Rule selection is **static and curated** — hardcoded list of rule IDs that
  apply to collection internals. This avoids configuration complexity and
  ensures predictable behavior. The list can be expanded in future ADRs.
- The validator runs rules **in-process** (like the Native validator), not by
  calling out to separate validator services. This avoids circular fan-out.
- Findings are annotated with `collection_fqcn` and `collection_version` in
  `metadata` so consumers can group and filter.

### Python Dependency Auditor — key design points

- `pip-audit` is invoked as a subprocess with `--json` for machine-readable
  output and `--path` to target the session venv's site-packages directly.
- CVSS → severity mapping follows the standard scale (Critical ≥ 9.0,
  High ≥ 7.0, Medium ≥ 4.0, Low < 4.0).
- If `pip-audit` is not installed, `Health` returns `NOT_SERVING` and Engine
  skips the validator (identical to Gitleaks without binary).
- Air-gapped operation requires a pre-populated `pip-audit` cache
  (`--cache-dir`) or a mirrored vulnerability data source; `-l` / `--local`
  in the audit command refers to auditing the local environment, not an
  offline database mode.

### Launcher changes

New entries in the existing `_OPTIONAL_SERVICES` dict (ports live here, not
in `_DEFAULT_PORTS`):

```python
_OPTIONAL_SERVICES = {
    "gitleaks": 50056,
    "collection_health": 50058,
    "dep_audit": 50059,
}
```

### Rule ID allocation

| ID | Category | Description |
|----|----------|-------------|
| R200 | Risk | Known CVE in Python dependency |
| R201 | Risk | Unmaintained Python dependency (reserved, future) |

Collection health findings reuse existing rule IDs (M0xx deprecated patterns,
R1xx risk/security) with `RuleScope.COLLECTION` differentiation. Authoring-
quality lint rules (galaxy metadata L095/L103-L105, module quality L089/L090,
role quality L027/L053/L077/L079, FQCN hygiene M001-M003) are excluded —
dependency scanning targets security and runtime risks only.

### CLI integration

`apme check` output groups dependency findings in a separate section:

```
── Dependency Health ──────────────────────────────────
  community.general 8.0.0  3 findings (1 high, 2 medium)
  ansible.netcommon 6.1.0  1 finding  (1 low)

── Python Dependencies ────────────────────────────────
  R200 [critical] jmespath==1.0.0 — CVE-2025-12345: ...
  R200 [high]     paramiko==3.4.0 — CVE-2025-67890: ...
```

Flags:
- `--skip-dep-scan` — disable both dependency validators
- `--skip-collection-scan` — disable collection health only
- `--skip-python-audit` — disable Python CVE audit only
- `--rescan-deps` — bust the collection health cache

### Container image changes

- `pip-audit` added to the engine container's Python dependencies
- No other external binaries required (collection scanning is pure Python)

## Related Decisions

- ADR-008: Rule ID Conventions — `R` prefix for risk findings
- ADR-009: Remediation Engine — validators are read-only (both new validators
  are detection-only)
- ADR-010: Gitleaks Validator — architectural template for optional validators
- ADR-019: Dependency Governance — pip-audit is a new runtime dependency
- ADR-022: Session-Scoped Venvs — provides the venv these validators inspect
- ADR-040: Scan Metadata Enrichment — provides `ProjectManifest` inventory;
  this ADR adds health assessment on top
- REQ-010: Dependency Health Assessment — enterprise sidecar that aggregates
  findings from these validators across projects

## References

- [pip-audit](https://github.com/pypa/pip-audit) — PyPA tool for auditing
  Python environments against OSV
- [OSV.dev](https://osv.dev/) — Open Source Vulnerability database
- REQ-010: Dependency Health Assessment (sidecar design)
- ADR-040: Scan Metadata Enrichment (manifest contract)
- SOP.md: References pip-audit for manual security practice

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-04-07 | Roger Lopez | Initial proposal |
