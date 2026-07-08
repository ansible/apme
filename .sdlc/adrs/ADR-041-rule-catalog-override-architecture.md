# ADR-041: Rule Catalog & Override Architecture

## Status

Implemented

## Date

2026-03-25

## Context

APME has 100+ rules spread across four built-in validators (Native, OPA, Ansible, Gitleaks) and an extensible set of third-party plugin containers ([ADR-042](ADR-042-third-party-plugin-services.md)). Today, rules are **opaque** — nobody outside the engine knows what rules exist until violations appear in scan output. There is no catalog, no ability to enable/disable rules, and no mechanism for severity overrides.

Two proposed features — rule severity ratings (REQ-005 / PR #88) and rule enable/disable with acknowledgment (REQ-007 / PR #90) — both require the same underlying infrastructure: a rule catalog that the Gateway knows about, and a mechanism to deliver overrides to the engine at scan time. Without this architecture, both specs jump straight to UI concerns without answering where the catalog lives, how overrides reach the engine, or what happens in multi-pod deployments.

### The compound discovery problem

The rule set is not static or uniform:

- **Built-in rules** are baked into the engine image. Different engine versions have different rules (e.g., v2.1 adds L073).
- **Plugin rules** are dynamic. [ADR-042 (Third-Party Plugin Services)](ADR-042-third-party-plugin-services.md) plugins register `EXT-` prefixed rules via their `Describe` RPC. A pod with the `secteam` plugin has rules that a pod without it does not.
- **Multi-pod deployments** (ADR-012, ADR-034) mean multiple Primaries, potentially with different engine versions or plugin sets. A management UI that shows rules from one pod may not reflect the reality of another.

### Override delivery

Once a catalog exists, overrides (severity changes, enable/disable) must reach the engine at scan time. Two directions were considered:

1. **Engine pulls from Gateway** — inverts the dependency. Today the Gateway depends on the engine, not the other way around. This would create a circular dependency.
2. **Gateway pushes with scan request** — the Gateway already initiates scans. Including overrides in the request is stateless and keeps the dependency direction clean.

### The authority question

In a multi-pod deployment, which pod's rule set is canonical? If Pod A has `EXT-secteam` and Pod B doesn't, the catalog differs. Someone must be authoritative, or every pod is an island with no consistency guarantee.

## Decision

**We will establish the Gateway as the authoritative rule catalog and the Primary as the registration source, with overrides delivered at scan time via `ScanRequest`.**

### 1. Primary registers rules with the Gateway on startup

When a Primary starts, it discovers its validators and plugins (per ADR-005 and [ADR-042](ADR-042-third-party-plugin-services.md)), collects the full rule set from each, and registers the catalog with the Gateway.

Each rule registration includes:

| Field | Type | Description |
|-------|------|-------------|
| `rule_id` | string | Rule identifier (e.g., `L026`, `EXT-secteam-001`) |
| `default_severity` | enum | Critical / High / Medium / Low / Info |
| `category` | string | lint / modernize / risk / policy / secrets / ext |
| `source` | string | Validator or plugin name (e.g., `native`, `opa`, `secteam`) |
| `description` | string | Human-readable rule description |
| `scope` | string | Rule scope (per ADR-026) |
| `enabled` | bool | Default enabled state |

The registration mechanism reuses the existing engine → Gateway push channel (ADR-020 / ADR-034). A new `RegisterRules` RPC on the Gateway's Reporting service (or a dedicated management RPC) accepts the full rule set from a Primary.

Built-in rules and plugin rules use the **same registration path**. No build-time injection, no separate mechanism. The Primary is the single source that reports everything it can execute.

### 2. Gateway reconciles on re-registration

The Gateway treats each registration as the **complete current state** of the registering Primary's rule set. On re-registration (Primary restart, engine upgrade, plugin added/removed):

- **New rules** (in registration but not in DB) → added to catalog
- **Removed rules** (in DB but not in registration) → removed from catalog; orphaned overrides flagged
- **Unchanged rules** → no-op

This makes rule lifecycle automatic. Engine upgrade adds L073? Primary restarts, re-registers, Gateway catalog updated. Plugin removed? Primary restarts without those `EXT-` rules, re-registers, Gateway drops them. No manual intervention.

### 3. Rule authority model

**Single-pod** (default): the one Primary is the authority. No flag needed.

**Multi-pod**: one pod is designated as the **rule authority** via explicit configuration (e.g., `APME_RULE_AUTHORITY=true` env var). Exactly one Primary per deployment should have this flag set. That Primary's registration defines the canonical catalog. Other Primaries do not register rules — they are listeners that receive scan requests and execute.

- The Gateway only accepts rule registrations from a Primary that identifies itself as the authority. Registrations from non-authority Primaries are rejected (no-op, logged). There is no implicit "first to register wins" behavior.
- If the authority Primary goes down, other Primaries keep scanning. The catalog is in the Gateway's DB. The authority is a registration-time concept, not a runtime dependency.
- If the authority Primary comes back (or upgrades), it re-registers. The Gateway reconciles the catalog.
- For identical replicas (same image, same plugins), any Primary can be chosen as the authority since they all have the same rule set, but the choice is explicit via configuration.

### 4. Overrides ride with `ScanRequest`

The Gateway sends the **full resolved rule configuration** — not just deltas — with every scan request. This keeps the engine stateless: it executes exactly what it's told, with no need to remember what it registered or cache previous overrides.

A new `RuleConfig` message carries the complete per-rule state, and `ScanOptions` includes the full set:

```protobuf
message RuleConfig {
  string rule_id = 1;
  Severity severity = 2;       // resolved severity (override > default)
  bool enabled = 3;            // false = skip this rule entirely
  bool enforced = 4;           // true = ignore inline # apme:ignore
}

message ScanOptions {
  bool include_scandata = 1;
  string ansible_core_version = 2;
  repeated string collection_specs = 3;
  string session_id = 4;
  repeated RuleConfig rule_configs = 5;   // NEW — full resolved rule set
}
```

At ~100 bytes per rule in protobuf, a 200-rule catalog is ~20KB — negligible compared to the file payloads already in `ScanRequest`.

The Primary applies the rule configuration before fanning out to validators:

- Rules with `enabled=false` are excluded from the validation fan-out
- Severity values are applied to violations before returning results
- Rules with `enforced=true` ignore inline `# apme:ignore` annotations — the violation always counts regardless of code-level suppression. This is the compliance lever: an admin can mandate that certain rules (e.g., SEC, policy) cannot be suppressed by developers at the code level
- If the request includes a `rule_id` the Primary doesn't have → hard fail (see §5)

The CLI can also pass rule configs (from a local `.apme/rules.yml` or flags), enabling the same mechanism outside the Gateway.

### 5. Hard fail on rule mismatch

If the Gateway sends a scan request that references a rule the Primary cannot execute (e.g., plugin not deployed, engine version skew), the Primary **fails the scan** with a descriptive error — not a silent skip, not a warning.

This is the consistency enforcement mechanism:

- Rolling upgrade with new Gateway catalog but old engine → scans fail → forces completion of the upgrade
- Multi-pod deployment where one pod is missing a plugin → scans fail → operational signal to deploy the plugin or deregister the rules
- No silent degradation — you always know what you're scanning for

### 6. Plugin registration is a cluster-wide commitment

When a Primary registers plugin rules (`EXT-*`) with the Gateway, those rules become part of the canonical catalog. The Gateway includes them in scan requests to **all** Primaries. Any Primary without the corresponding plugin cannot execute those rules and will fail the scan.

Deploying a plugin to one pod means deploying it to all pods. Removing a plugin means the authority Primary re-registers without those rules, and the Gateway drops them from the catalog.

## Alternatives Considered

### Alternative 1: Build-time manifest injection

**Description**: Extract the rule catalog from engine code at build time and embed it in the Gateway image. Engine and Gateway ship together with a shared manifest.

**Pros**:
- Gateway knows the catalog immediately at startup, no registration delay
- No runtime discovery needed for built-in rules

**Cons**:
- Requires a build-time extraction step and coupling between engine and Gateway builds
- Two mechanisms needed: build-time for built-in rules, runtime registration for plugins
- Rolling upgrades become more complex — Gateway manifest must match engine version exactly

**Why not chosen**: Runtime registration handles both built-in and plugin rules uniformly. The startup delay (Primary registers before scans can run) is acceptable since the Gateway already can't scan until an engine is up.

### Alternative 2: Each pod is an island

**Description**: Each Primary registers with its own Gateway independently. No authority model. Each pod manages its own catalog and overrides.

**Pros**:
- Simplest implementation — no coordination
- Each pod is fully self-contained

**Cons**:
- No policy consistency across pods. "Disable L026 everywhere" requires manual action on each pod.
- Different pods may silently enforce different rule sets
- No central management UI — each Gateway shows its own view

**Why not chosen**: Enterprise deployments need policy consistency. The authority model provides this with minimal coordination overhead.

### Alternative 3: External configuration store

**Description**: Rule overrides stored in an external system (etcd, ConfigMap, shared DB). All pods read from the same store.

**Pros**:
- Guaranteed consistency across pods
- Decoupled from Gateway lifecycle

**Cons**:
- Contradicts ADR-005 (no service discovery / external infrastructure)
- New operational dependency
- Over-engineered for current deployment model

**Why not chosen**: The Gateway DB is already the persistence layer. Adding external infrastructure contradicts the project's operational simplicity principles.

### Alternative 4: Engine pulls overrides from Gateway

**Description**: The engine queries the Gateway for current rule configuration at startup or per-scan.

**Pros**:
- Engine always has latest overrides
- No proto change on `ScanRequest`

**Cons**:
- Inverts the dependency direction — engine would depend on Gateway
- Creates a circular dependency (Gateway → engine for scans, engine → Gateway for config)
- Engine can't scan if Gateway is down (for the config fetch)

**Why not chosen**: The current architecture has a clean dependency direction: Gateway depends on engine, not the other way. Overrides in `ScanRequest` preserve this.

## Consequences

### Positive

- **Single mechanism** for both built-in and plugin rule registration — no special cases
- **Self-healing catalog** — re-registration on Primary restart handles additions, removals, and upgrades automatically
- **Clean dependency direction** — overrides flow Gateway → engine via `ScanRequest`, no circular dependency
- **Resilient** — authority Primary going down does not affect scanning; catalog is persisted in Gateway DB
- **Consistency enforcement** — hard fail on rule mismatch prevents silent degradation
- **Enables REQ-005 and REQ-007** — severity management and rule enable/disable become Gateway UI + CRUD once this infrastructure exists

### Negative

- **Startup ordering dependency** — Gateway cannot show the rule management UI or send overrides until the authority Primary has registered. Mitigated: the Gateway already can't scan until an engine is up.
- **Single authority limitation** — in early multi-pod deployments, accidental dual-authority (two Primaries both marked as authority with different rule sets) would cause catalog thrashing. Mitigated: operational discipline and eventual conflict detection.
- **Proto change** — adding `RuleConfig` to `ScanOptions` and a registration RPC requires proto regeneration and coordinated deployment.

### Neutral

- Inline acknowledgment (`# apme:ignore`) is unaffected — it's scan-time annotation parsing in the engine, independent of the catalog.
- Existing scan behavior is unchanged when no overrides are present (all rules enabled at default severity).
- Plugin `Describe` RPC ([ADR-042](ADR-042-third-party-plugin-services.md)) provides the rule metadata that Primaries forward during registration.

## Implementation Notes

### Phase 1: Registration contract

1. Define `RegisterRulesRequest`/`RegisterRulesResponse` messages in `reporting.proto` (or a new `management.proto`)
2. Each validator exposes its rule metadata (ID, default severity, description, scope) via an internal interface
3. Primary aggregates across validators and plugins, calls `RegisterRules` on Gateway at startup
4. Gateway persists catalog in a `rules` table, reconciles on re-registration

### Phase 2: Override delivery

1. Add `RuleConfig` message and `repeated RuleConfig rule_configs` to `ScanOptions` in `primary.proto`
2. Primary applies overrides: filters disabled rules, attaches severity overrides to violations
3. Gateway sends overrides with each `ScanRequest` from its stored override config
4. CLI reads overrides from `.apme/rules.yml` and passes them in `ScanOptions`

### Phase 3: Rule mismatch enforcement

1. Primary validates incoming rule configs against its known rule set
2. If a config references a rule the Primary doesn't have → Primary aborts the `Scan` RPC with `FAILED_PRECONDITION` status, including the unknown rule IDs in the status detail
3. Gateway interprets this non-OK gRPC status as a deployment/configuration issue and surfaces it in the UI

### Phase 4: Gateway UI (REQ-005 / REQ-007)

1. CRUD endpoints for rule overrides (`GET/PUT /api/v1/rules/{id}/config`)
2. Rule catalog browsing UI with severity and enable/disable toggles
3. Audit trail for override changes
4. Severity threshold configuration for CI gating

## Related Decisions

- ADR-005: No service discovery — still correct; registration uses existing push channel
- ADR-008: Rule ID conventions (L/M/R/P/SEC) — extended by ADR-042 with EXT- prefix
- ADR-012: Scale pods, not services — multi-pod authority model
- ADR-020: Reporting service and event delivery — reused for rule registration push
- ADR-026: Rule scope metadata — rule scope included in catalog registration
- ADR-042: Third-party plugin services — plugin `Describe` RPC provides registration data
- ADR-034: Multi-pod health registration — similar registration pattern for health heartbeats

## References

- PR #88: REQ-005 Rule Rating & Severity (blocked on this ADR)
- PR #90: REQ-007 Rule Management & Issue Acknowledgment (blocked on this ADR)
- `proto/apme/v1/primary.proto` — `ScanOptions`, `ScanRequest`
- `proto/apme/v1/reporting.proto` — Reporting service (registration endpoint)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-25 | Brad (cidrblock) | Initial proposal |
| 2026-07-08 | Brad Thornton | Accepted — core decision fully implemented |
