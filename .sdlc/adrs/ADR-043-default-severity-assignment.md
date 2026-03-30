# ADR-043: Default Severity Assignment for Rule Catalog

## Status

Accepted

## Date

2026-03-26

## Context

[ADR-041 (Rule Catalog & Override Architecture)](ADR-041-rule-catalog-override-architecture.md) established the infrastructure for a rule catalog with a `default_severity` field on every rule registration. It defined the mechanism — Primary registers rules with the Gateway, overrides ride with `ScanRequest` — but deliberately left the **assignment policy** undefined: how does each rule get its default severity, and what severity scale do we use?

### The vocabulary problem

Today, rules carry severity metadata in two incompatible vocabularies with no unified mapping:

- **Native rules** use the ARI engine's ansible-lint-compatible 6-level scale: `very_high`, `high`, `medium`, `low`, `very_low`, `none`. Each rule class sets `severity: str = Severity.X` on its dataclass.
- **OPA rules** hardcode `"level"` strings in Rego violation objects: `error`, `warning`, `info`, `low` — inconsistent across rules and not drawn from a shared vocabulary.
- **Ansible validator** rules hardcode `error` or `warning` per finding type.
- **Gitleaks** emits `error` for every finding regardless of the secret type.

The proto `Violation.level` field is a plain string — no enum, no validation, no normalization. Consumers (CLI, Gateway, UI) must interpret whatever string each validator happens to emit.

### The criteria problem

Even if the vocabularies were unified, there is no principled criteria for **why** a rule should be one severity vs. another. Individual rule authors made ad-hoc choices: L030 (non-builtin module) is `very_low`, R101 (command execution) is `low`, while OPA rules are a mix of `error` and `warning` with no documented reasoning. The result is that severity conveys no consistent signal to users.

### The scale problem

The native `Severity` class defines 6 levels (`very_high` through `none`). ADR-041's `default_severity` field references 5 levels (Critical, High, Medium, Low, Info). Neither scale distinguishes between security-critical findings and runtime-breaking errors — two fundamentally different categories that warrant different responses from users.

## Decision

**We will define a 6-level severity enum based on impact class, with criteria-based assignment, and normalize all violation levels to this enum.**

### 1. The severity enum

| Level | Numeric | Impact Class | Criteria |
|-------|---------|-------------|----------|
| **Critical** | 6 | **Security threat** — credentials exposed, secrets in code | Finding represents a security vulnerability that could lead to credential compromise or unauthorized access. Reserved for SEC rules and security-specific findings. |
| **Error** | 5 | **Runtime breakage** — will fail when executed | The playbook or role will fail at runtime: syntax errors, removed/tombstoned modules, invalid argument specs. Not a warning about the future — it is broken **now**. |
| **High** | 4 | **Behavioral risk** — may cause incorrect behavior or imminent breakage | Deprecated modules, insecure file permissions, missing `no_log` on secrets, features being removed in the next ansible-core release. The code runs today but is on a path to failure or produces risky behavior. |
| **Medium** | 3 | **Correctness smell** — likely a bug or anti-pattern | Undefined variables, unresolved modules/roles, missing `changed_when`, `ignore_errors` masking failures, variable shadowing. May or may not break at runtime, but is probably not what the author intended. |
| **Low** | 2 | **Best practice** — works but violates maintainability conventions | FQCN usage, naming conventions, key ordering, missing role metadata, structural recommendations. The code works correctly; this is about long-term health. |
| **Info** | 1 | **Advisory** — informational, style, or "consider this" | Reports, suggestions, metrics, style preferences. No action strictly required. |

The numeric values enable threshold-based gating (e.g., "fail CI if any finding >= Error").

### 2. Category prefix does not determine severity

The rule ID prefix (L/M/R/P/SEC per [ADR-008](ADR-008-rule-id-conventions.md)) identifies the rule's **domain** — what kind of thing it checks. Severity identifies the **consequence** of ignoring the finding. These are orthogonal:

- L057 (syntax check) is **Error** — an L-rule that breaks at runtime
- L060 (line length) is **Info** — an L-rule that is purely advisory
- M004 (removed module) is **Error** — the module is tombstoned, it will fail
- M009 (with_* loops deprecated) is **High** — deprecated but still works today
- R101 (command execution) is **Medium** — a correctness/risk smell
- SEC:* (secrets) is **Critical** — security threat

### 3. Assignment criteria

When assigning a default severity, apply these questions in order:

1. **Is it a security vulnerability?** → Critical
2. **Will it fail at runtime today?** → Error
3. **Will it break in the next ansible-core release, or does it expose the system to incorrect behavior (permissions, privilege, error masking)?** → High
4. **Is it probably a bug or anti-pattern that may cause surprises?** → Medium
5. **Does it violate a best practice or convention?** → Low
6. **Is it purely informational or stylistic?** → Info

This is a decision tree, not a point system. Each rule gets exactly one default severity based on the **first matching criterion**.

> **Note:** Service-affecting actions (§7) are flagged via a separate `service_affecting` boolean, not a severity level. A rule that detects a reboot is assigned severity by the criteria above (typically Info or Low), then additionally marked `service_affecting = true`.

### 4. Representative assignments

These examples illustrate the criteria applied to real rules:

**Critical:**
- SEC:* — Secrets/credentials detected in code

**Error:**
- L057 — Syntax check failure (ansible-playbook --syntax-check)
- L058, L059 — Argspec validation failure
- M004 — Removed/tombstoned module
- L095 — YAML does not match expected schema structure
- L098 — Duplicate YAML mapping keys (parser-level correctness)
- P001–P004 — Ansible runtime validation failures

**High:**
- M001–M003 — Deprecated/redirected module (migration path exists but running on borrowed time)
- M005, M006, M008, M009 — Breaking changes in ansible-core 2.19+
- M010, M011 — Python 2 interpreter / network module compatibility
- L004 — Deprecated module (OPA)
- L020 — File mode as integer, not string (silent permission bugs)
- L031 — Insecure file permissions
- L047 — Missing `no_log` on password parameters

**Medium:**
- L010 — `ignore_errors` masking failures
- L013 — Missing `changed_when` (idempotency broken)
- L037 — Unresolved module name
- L038 — Unresolved role
- L039 — Possibly undefined variable
- L102 — Setting read-only Ansible variables
- R101–R115 — Risk annotation findings (command exec, privilege escalation, etc.)

**Low:**
- L005, L026 — FQCN usage
- L003, L024, L025 — Naming conventions (play/task names)
- L041 — Key ordering
- L050, L074, L079–L081 — Variable/role naming conventions
- L073 — Indentation
- L087, L088, L103–L105 — Collection metadata

**Info:**
- L060 — Line length
- L042 — Complexity metric (high task count)
- L056 — Path matches ignore pattern
- L072 — Consider setting backup on template/copy
- L099 — Quote style preference
- R401, R402, R404 — Reports (inbound sources, variable sets)
- R501 — Dependency suggestion

**Service-affecting (flag, not severity):**
- R201 — Server reboot detected (`ansible.builtin.reboot`, `ansible.windows.win_reboot`)
- R202 — Service restart detected (`state: restarted`)
- R203 — Service reload detected (`state: reloaded`)
- R204 — Package upgrade detected (`state: latest`)
- R205 — Firewall/network rule change detected
- R206 — Destructive filesystem operation detected (`state: absent` with recurse)

These rules have **Info** or **Low** severity (the code is correct) but `service_affecting = true`.

### 5. Normalization of existing levels

The current `Violation.level` string field must be normalized to the new enum. This requires:

**Proto change:** Replace the plain `string level` in `Violation` (common.proto) with a `Severity` enum:

```protobuf
enum Severity {
  SEVERITY_UNSPECIFIED = 0;
  SEVERITY_INFO = 1;
  SEVERITY_LOW = 2;
  SEVERITY_MEDIUM = 3;
  SEVERITY_HIGH = 4;
  SEVERITY_ERROR = 5;
  SEVERITY_CRITICAL = 6;
}
```

**Backward-compatible mapping** from legacy strings:

| Legacy string | New enum |
|---------------|----------|
| `very_high` | `SEVERITY_HIGH` |
| `high` | `SEVERITY_HIGH` |
| `medium` | `SEVERITY_MEDIUM` |
| `low` | `SEVERITY_LOW` |
| `very_low` | `SEVERITY_INFO` |
| `none` | `SEVERITY_INFO` |
| `error` | (per-rule — see assignment table) |
| `warning` | (per-rule — see assignment table) |
| `info` | `SEVERITY_INFO` |

> **`very_high` rationale:** The legacy `very_high` level indicated behavioral risk or deprecation importance, not a guaranteed runtime failure. Mapping it to `SEVERITY_ERROR` would overstate the immediacy — Error means "will fail at runtime today." `SEVERITY_HIGH` correctly reflects that these findings are urgent but not broken-today.

The ambiguous legacy strings (`error`, `warning`) cannot be mechanically mapped because they were assigned inconsistently — OPA's `error` means different things for L004 (deprecated module → High) vs. L003 (missing play name → Low). These are resolved by the per-rule assignment table, not by string mapping.

**Retire the engine `Severity` class.** The `Severity` class and `_severity_level_mapping` in `engine/models.py` are replaced by the proto enum. Native rule classes update their `severity` field to use the new enum values. OPA rules update their `"level"` strings. The old vocabulary is removed. Note that legacy level strings are currently visible to consumers — the UI renders `Violation.level`, the CLI displays it, and the REST API exposes it. The migration to the enum is a coordinated breaking change across engine, Gateway, CLI, and UI; all components must be deployed together.

### 6. Static assignment table

The complete per-rule default severity is maintained as a **static mapping** that ships with the engine image. The authoritative source is a data file (e.g., `src/apme_engine/severity_defaults.py` or a YAML/JSON file) that maps `rule_id → Severity`. This table is:

- **Versioned** with the engine image — severity assignments can change between releases
- **Auditable** — a single file shows every rule's default severity
- **Machine-readable** — the catalog generation script (`scripts/generate_rule_catalog.py`) includes severity in the output
- **The single source** — individual rule classes and Rego policies no longer carry their own severity; they reference the table

During ADR-041 registration, Primary reads this table and includes `default_severity` for each rule in the `RegisterRulesRequest`.

### 7. Service-affecting action flag

Certain rules detect **inherently disruptive runtime actions** — tasks that, even when executed as intended, can directly cause service disruption or outages. These are not code quality issues; they are warnings about **what the code will do when it runs**.

This is modeled as a **separate boolean flag** (`service_affecting: bool`), not a severity level:

| Dimension | What it measures | Example |
|-----------|-----------------|---------|
| **Severity** | Consequence of code being *wrong* | Syntax error = Error, deprecated module = High |
| **Service-affecting** | Consequence of code being *executed correctly* | `reboot` task runs successfully = outage |

A perfectly correct `reboot` task has no code quality issues (severity may be Info), but executing it causes downtime. The `service_affecting` flag captures this orthogonal dimension.

#### Service-affecting action types

| Action | Modules | Risk |
|--------|---------|------|
| **Server reboot** (R201) | `ansible.builtin.reboot`, `ansible.windows.win_reboot` | Service downtime, potential boot failures, dependency cascade |
| **Service restart** (R202) | `ansible.builtin.service`, `ansible.builtin.systemd` with `state: restarted` | Momentary outage, connection drops, dependent service failures |
| **Service reload** (R203) | `ansible.builtin.service` with `state: reloaded` | Config errors cause service failure |
| **Package upgrade** (R204) | `ansible.builtin.yum`, `ansible.builtin.apt` with `state: latest` | Breaking changes, service restarts, compatibility issues |
| **Network/firewall changes** (R205) | `ansible.builtin.iptables`, network modules | Connectivity loss, lockout |
| **Destructive filesystem operations** (R206) | `ansible.builtin.file` with `state: absent`, recursive deletes | Data loss |

#### Rule metadata extension

The rule registration schema (ADR-041) gains an additional field:

```protobuf
message RuleInfo {
  string rule_id = 1;
  string description = 2;
  Severity default_severity = 3;
  Scope scope = 4;
  bool service_affecting = 5;  // NEW: flags runtime-disruptive actions
}
```

Rules detecting service-affecting actions set `service_affecting = true`. These rules typically have **Low** or **Info** severity (the code itself is correct), but the flag triggers special handling.

#### UI/CLI behavior for service-affecting rules

When a finding has `service_affecting = true`:

1. **Warning badge** — display a "Service-Affecting" indicator distinct from severity
2. **Guidance panel** — recommend that plays and tasks are configured so that:
   - Tasks have proper `when` conditions limiting scope
   - The containing play or deployment strategy uses rolling/serial execution (e.g., `serial: 1`)
   - Tasks are followed by health checks or `wait_for` conditions after the action
   - There are **automated tests** validating the behavior (molecule, integration tests)
3. **Review prompt** — in batch operations, prompt for confirmation even if severity is Low/Info
4. **(Future) Test coverage check** — rules could verify if service-affecting tasks have corresponding molecule/integration tests in the repository

### 8. Plugin severity

Third-party plugins ([ADR-042](ADR-042-third-party-plugin-services.md)) provide their own `default_severity` via the `Describe` RPC. The framework in this ADR (the enum and criteria) applies to plugins, but the assignment is the plugin author's responsibility. The Gateway may flag plugin rules that claim Critical severity for admin review, since Critical is reserved for security findings.

## Alternatives Considered

### Alternative 1: Inherit severity from rule category prefix

**Description**: Assign severity mechanically from the ADR-008 prefix: SEC → Critical, R → High, M → Medium, L → Low, P → Info.

**Pros**:
- Zero per-rule decisions needed
- Simple to implement and explain

**Cons**:
- Factually wrong — L057 (syntax failure) is more severe than R501 (dependency suggestion), but L < R in the prefix hierarchy
- Category is about **domain**, not **consequence**
- No way to distinguish L057 (broken) from L060 (style) within L-rules

**Why not chosen**: The rule prefix and severity are orthogonal dimensions. Conflating them produces misleading signals.

### Alternative 2: Let each validator author set severity ad-hoc

**Description**: Continue the current approach — each rule author chooses a severity string when writing the rule. No central policy.

**Pros**:
- No coordination overhead
- Rule author knows their rule best

**Cons**:
- Already proven to produce inconsistent results (current state)
- Two incompatible vocabularies emerged organically
- No way for a user to compare severities across validators
- New rule authors have no guidance

**Why not chosen**: This is the status quo and it doesn't work. The inconsistency is the problem this ADR solves.

### Alternative 3: Computed severity from rule metadata

**Description**: Derive severity algorithmically from rule properties: scope (ADR-026), category, tags, whether a fixer exists, etc. For example: `scope=playbook + category=R → High`.

**Pros**:
- No manual assignment needed for new rules
- Automatically consistent

**Cons**:
- The inputs don't predict the output — a playbook-scoped L-rule about line length is not the same severity as a playbook-scoped R-rule about privilege escalation
- Creates false precision from imprecise inputs
- Difficult to override individual rules without breaking the formula
- Obscures the reasoning — "why is this High?" requires understanding the formula

**Why not chosen**: Severity is a judgment about **consequence**, which requires human assessment of what the rule detects. No combination of existing metadata captures this.

### Alternative 4: Keep the 5-level ADR-041 enum (Critical/High/Medium/Low/Info)

**Description**: Use the severity scale as originally referenced in ADR-041 without adding Error.

**Pros**:
- Simpler scale — fewer levels to distinguish
- Aligns with common industry scales (CVSS-like)

**Cons**:
- Conflates security threats and runtime breakage under "Critical"
- A syntax error (L057) and leaked credentials (SEC:*) require fundamentally different responses — one needs a code fix, the other needs credential rotation and incident response
- Users cannot filter for "security only" vs. "fix broken code" without additional metadata

**Why not chosen**: The distinction between "this is a security incident" and "this playbook won't run" is operationally meaningful. Critical reserved for security enables security teams to set thresholds without drowning in non-security findings.

## Consequences

### Positive

- **Consistent signal** — severity means the same thing across all 136+ rules and all validators
- **Criteria-based** — new rule authors apply the decision tree; no subjective guessing
- **Security distinction** — Critical is reserved for security, enabling targeted alerting and compliance gating
- **Threshold gating** — numeric values enable CI policies like "fail if any >= Error" without enumerating specific rules
- **Single vocabulary** — eliminates the `very_high`/`error`/`warning` soup; one enum everywhere
- **Auditable** — a single file shows every rule's default severity
- **Service outage awareness** — service-affecting actions (reboots, restarts, network changes) are flagged, prompting operators to ensure proper testing and review before production deployment

### Negative

- **One-time migration** — all 136 rules need severity assignment and code changes (native rule classes, Rego policies, Ansible/Gitleaks emitters). This is a bulk change.
- **Proto breaking change** — changing `Violation.level` from string to enum requires coordinated deployment of engine + Gateway + CLI
- **Judgment calls** — some rules sit at boundaries between impact classes (e.g., is `ignore_errors` Medium or High?). The decision tree helps but doesn't eliminate all ambiguity.

### Neutral

- ADR-041's `default_severity` field and override flow are unchanged — this ADR extends the enum from 5 to 6 values (adding Error) and defines the assignment criteria. ADR-041's registration table should be updated to reference the 6-value enum when this ADR is implemented
- Override mechanism (ADR-041 §4) is unaffected — admins can still override any rule's severity regardless of the default
- Plugin authors use the same enum and criteria but own their own assignments
- The `service_affecting` flag is a new orthogonal dimension — it does not affect severity assignment or thresholds, but enables UI/CLI to provide additional warnings for runtime-dangerous actions

## Implementation Notes

### Phase 1: Proto and enum

1. Add `Severity` enum to `common.proto` (values: UNSPECIFIED, INFO, LOW, MEDIUM, HIGH, ERROR, CRITICAL)
2. Change `Violation.level` from `string` to `Severity` enum
3. Update `RuleConfig.severity` in ADR-041's `ScanOptions` to use the same enum
4. Regenerate stubs (`scripts/gen_grpc.sh`)

### Phase 2: Assignment table and native rules

1. Create `src/apme_engine/severity_defaults.py` (or `.yaml`) with the complete rule → severity mapping
2. Update native rule classes: replace `severity: str = Severity.X` with references to the new enum
3. Remove the old `Severity` class and `_severity_level_mapping` from `engine/models.py`
4. Update `native/__init__.py` to read severity from the assignment table

### Phase 3: OPA and other validators

1. Update all Rego policies: replace ad-hoc `"level"` strings with enum-compatible values
2. Update Ansible validator to emit enum values
3. Update Gitleaks scanner to emit `SEVERITY_CRITICAL`
4. Update `violation_convert.py` to handle enum ↔ dict conversion

### Phase 4: Catalog and docs

1. Update `scripts/generate_rule_catalog.py` to include a Severity column
2. Update `docs/RULE_CATALOG.md` to show default severity per rule
3. Document the criteria decision tree in contributor docs

### Phase 5: Service-affecting action rules

> **Prerequisite:** Phase 5 depends on ADR-041 Phase 1 (rule registration schema and `catalog.proto` definition).

1. Add `service_affecting: bool` field to `RuleInfo` in `catalog.proto` (ADR-041)
2. Create R2xx rules detecting service-affecting actions:
   - R201: Server reboot (`ansible.builtin.reboot`, `ansible.windows.win_reboot`)
   - R202: Service restart (`state: restarted`)
   - R203: Service reload (`state: reloaded`)
   - R204: Package upgrade (`state: latest`)
   - R205: Network/firewall changes (`ansible.builtin.iptables`, network modules)
   - R206: Destructive filesystem operations (`state: absent` with `recurse: true`)
3. Assign severity **Info** or **Low** (code is correct); set `service_affecting = true`
4. Update UI to display "Service-Affecting" warning badge with guidance panel
5. Update CLI to display warning and prompt for confirmation in batch operations
6. (Future) Implement test coverage detection — check if service-affecting tasks have corresponding molecule/integration tests

## Related Decisions

- [ADR-041](ADR-041-rule-catalog-override-architecture.md): Rule Catalog & Override Architecture — defines `default_severity` field; this ADR defines how values are assigned
- [ADR-008](ADR-008-rule-id-conventions.md): Rule ID Conventions — category prefix is orthogonal to severity
- [ADR-026](ADR-026-rule-scope-metadata.md): Rule Scope as First-Class Metadata — scope and severity are independent dimensions
- [ADR-042](ADR-042-third-party-plugin-services.md): Third-Party Plugin Services — plugin severity via `Describe` RPC
- [ADR-013](ADR-013-structured-diagnostics.md): Structured Diagnostics — `Violation` message being modified

## References

- `proto/apme/v1/common.proto` — `Violation.level` field (currently string)
- `src/apme_engine/engine/models.py` — `Severity` class, `_severity_level_mapping` (to be retired)
- `src/apme_engine/validators/native/__init__.py` — severity → level mapping in violation construction
- `docs/RULE_CATALOG.md` — auto-generated catalog (to gain severity column)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-26 | APME Team | Initial proposal |
| 2026-03-30 | APME Team | Added §7 service-affecting action flag (orthogonal to severity) with R2xx rule definitions; changed `very_high` → `SEVERITY_HIGH` mapping (was `SEVERITY_ERROR`) |
