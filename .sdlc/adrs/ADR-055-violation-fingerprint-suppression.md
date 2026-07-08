# ADR-055: Content-Based Violation Fingerprinting and Suppression

## Status

Accepted

## Date

2026-05-21

## Context

APME violations are currently identified by positional keys: `(rule_id, file, line)` for deduplication in the Primary and CLI output, and `(node_id, rule_id)` in the ContentGraph violation ledger (ADR-044). Neither identity is stable across projects, and both break when line numbers shift or tasks are reordered within the same project.

Users need the ability to **permanently accept or ignore** a specific violation so that:

1. The same violation in the same task does not reappear on the next scan after being reviewed and accepted.
2. The same violation on the same task content, copied or shared across projects, is automatically recognized as already accepted.
3. Editing the task content invalidates the acceptance, forcing re-review — because changed content may carry different risk.

Today, the only suppression mechanism is inline `# apme:ignore` annotations in the source file. This requires modifying the Ansible content itself, which is not always desirable (shared roles, upstream content, policy against inline annotations). ADR-041 provides rule-level enable/disable, but that is all-or-nothing — it cannot distinguish between two instances of the same rule where one is acceptable and the other is not.

### What makes two violations "the same"?

Consider two tasks that both trigger `L046` (no free-form):

```yaml
- name: Install nginx
  ansible.builtin.shell: apt install nginx

- name: Remove all data
  ansible.builtin.shell: rm -rf /data
```

Same rule, same module, same structure — but accepting one should absolutely not accept the other. The variable content (the shell command) is the runtime value being evaluated. Any fingerprint that collapses these into the same identity is wrong.

Conversely, the same task appearing in two different files at different line numbers IS the same content. The accept decision is about what the task does, not where it lives.

### Decision drivers

- Accept/ignore decisions must survive line-number changes, reformatting, and file moves
- Accept/ignore decisions must transfer across projects when the same content appears
- Different variable values in the same task structure must produce different fingerprints
- Whitespace and comments are not runtime values — cosmetic changes should not invalidate acceptances
- The engine is stateless (ADR-020, ADR-029) — fingerprinting and suppression are consumer-side concerns
- Rule IDs must be permanent to prevent old suppressions from silently applying to the wrong rule

## Decision

**We will compute a content-based SHA-256 fingerprint for each violation from the rule ID and the full normalized task YAML, with suppression evaluated at the consumer layer (Gateway or CLI), not in the engine.**

### 1. Fingerprint formula

```
SHA-256(rule_id + "\x00" + normalize(original_yaml))
```

Where:
- `rule_id` is the violation's **canonical** rule identifier (e.g. `L046`) after stripping any legacy namespace prefixes (e.g. `native:L046` → `L046`). The canonicalization step ensures fingerprint stability across engine versions that may have emitted prefixed IDs for backward compatibility.
- `original_yaml` is the full node YAML as originally written (already carried on the `Violation` proto, field 16)
- `normalize()` strips non-runtime content before hashing:
  - Parse the YAML through a structure-aware normalizer (e.g. `ruamel.yaml` round-trip) that distinguishes scalar content from structural formatting
  - Remove YAML comments that are outside scalar values (block scalar content like `|` and `>` bodies is preserved verbatim — lines inside block scalars that look like comments are runtime content, not metadata)
  - Strip leading/trailing whitespace on structural lines only (not within block scalar bodies, where indentation is significant)
  - Collapse runs of blank lines between mappings/sequences
  - Do NOT re-order keys, change quoting style, or alter values

**Canonical encoding for hash input**: All components are encoded as UTF-8, normalized to Unicode NFC form, with LF (`\n`) line endings (CRLF is converted to LF before hashing). This ensures consumers on different platforms produce identical fingerprints for the same logical content.

Whitespace and comments have zero effect on what Ansible executes. Stripping them means `apme format`, re-indentation, and comment edits do not invalidate acceptances. Any change to runtime content (variable values, module arguments, conditions, task name) changes the fingerprint and forces re-review.

### 2. The engine is not involved

The engine already emits everything needed on the `Violation` proto:

- `rule_id` (field 1)
- `original_yaml` (field 16)
- `metadata` map with `resolved_fqcn`, `original_module`, etc. (field 10)

The engine does not compute fingerprints, store them, or filter by them. Fingerprinting is a derived computation performed by the consumer (Gateway or CLI) from fields already on the wire. This preserves:

- Invariant 5: Stateless engine, persistence at the edge (ADR-020, ADR-029)
- Invariant 11: The engine never queries out; it only emits

### 3. Advanced user-tunable granularity

Power users can opt into looser fingerprints by choosing which components to include. This is an advanced option, not the default:

All modes use the `\x00` null byte as a field separator to prevent collisions between rule IDs and content that could otherwise concatenate ambiguously:

| Mode | Hash input | Use case |
|------|-----------|----------|
| `full` (default) | `SHA-256(rule_id + "\x00" + normalize(original_yaml))` | "I accept this exact content for this rule" |
| `rule_module` | `SHA-256(rule_id + "\x00" + module_fqcn)` | "I accept all tasks using this module for this rule" (see below for FQCN resolution) |
| `rule_only` | `SHA-256(rule_id + "\x00")` | "I never care about this rule" (overlaps ADR-041 disable, useful in CLI-file workflows) |

The suppression record stores which mode was used, so the matching logic knows how to compare. The default is maximally specific (safest); users explicitly broaden scope when they understand the implications.

**`module_fqcn` resolution for `rule_module` mode**: Use `metadata.resolved_fqcn` (the fully-qualified collection name after redirect resolution) as the canonical module identifier. If `resolved_fqcn` is not available (e.g. the module could not be resolved), fall back to `metadata.original_module`. This ensures fingerprints remain stable across collection redirect changes (e.g. `community.general.docker_container` → `community.docker.docker_container`).

### 4. Rule ID permanence

**Rule IDs are permanent. Retired rules are reserved, never reused.** If `L046` means "no free-form" today, it means that forever, even if the rule is removed from a future version. Without this guarantee, old suppressions silently apply to the wrong rule after an ID is reassigned.

This extends the convention established in ADR-008. The rule catalog (ADR-041) should track retired rule IDs and reject registration of any rule that reuses a previously registered ID.

### 5. Suppression storage

Suppressions are stored at the consumer layer, in two complementary locations:

**Gateway DB (enterprise):**

A `suppressions` table with:

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Auto-increment PK |
| `fingerprint_hash` | text | SHA-256 hex digest |
| `fingerprint_mode` | text | `"full"`, `"rule_module"`, or `"rule_only"` |
| `rule_id` | text | Denormalized for fast lookup and human readability |
| `scope` | text | `"global"` or `"project:<uuid>"` |
| `reason` | text | Human-provided justification |
| `created_by` | text | User or system that created the suppression |
| `created_at` | text | ISO 8601 timestamp |

Query: given violation fingerprints from a scan, `WHERE fingerprint_hash IN (...) AND (scope = 'global' OR scope = 'project:<current_project_uuid>')` — scoped lookup prevents suppressions from unrelated projects from applying. An index on `(fingerprint_hash, scope)` keeps this efficient.

REST endpoints: `POST /api/v1/suppressions`, `GET /api/v1/suppressions`, `DELETE /api/v1/suppressions/{id}`.

**CLI file (standalone / CI):**

`.apme/suppressions.yml` checked into the repo — version-controlled, team-shared:

```yaml
version: 1  # Algorithm/normalization version — enables deterministic migration if the fingerprint algorithm changes
suppressions:
  - fingerprint: "a1b2c3d4..."
    rule_id: L046
    mode: full  # Optional; defaults to "full" when omitted (backward-compatible)
    reason: "Accepted: legacy module usage in bootstrap role"
    created: "2026-05-21"
  - fingerprint: "e5f6a7b8..."
    rule_id: M014
    mode: rule_module
    reason: "Deferred: migrating ansible_hostname in Phase 3"
    created: "2026-05-21"
```

### 6. Suppression flow

```
Engine produces violations (with rule_id + original_yaml on each)
  → Gateway / CLI receives violations
  → Compute fingerprint = SHA-256(rule_id + "\x00" + normalize(original_yaml))
  → Check fingerprint against suppression store
  → Mark suppressed violations (never silently dropped)
```

Suppressed violations are always available for audit. The default display hides them; `--show-suppressed` (CLI) or a UI toggle (Gateway) reveals them.

### 7. Interaction with existing mechanisms

- **ADR-041 rule disable** (`enabled=false`): coarser — no violations generated at all. Fingerprint suppression is finer: violations are generated but filtered at display.
- **ADR-041 `enforced=true`**: an admin can mandate that fingerprint suppressions are ignored for certain rules (compliance lever). Enforced rules bypass fingerprint suppression.
- **Inline `# apme:ignore`**: code-level, per-instance. Fingerprint suppression is an out-of-band equivalent that does not require modifying source files.
- **Feedback endpoint**: false-positive reports could auto-create a suppression entry (future enhancement).

## Alternatives Considered

### Alternative 1: Message-based fingerprint

**Description**: Hash `(rule_id, violation_message)` instead of the full task YAML.

**Pros**:
- Simple — messages are already strings, no normalization needed
- Cross-project portable for static messages

**Cons**:
- Many messages contain variable content (module names, variable names, counts) that would need canonicalization
- The message describes the problem; the YAML is the thing being evaluated. Hashing the description instead of the content conflates the two.
- Message templates are an implementation detail that can change between versions, silently breaking suppressions

**Why not chosen**: The violation message is the rule's prose output, not the identity of the content being judged. Hashing the full task YAML directly captures what the user is actually evaluating.

### Alternative 2: Positional fingerprint (rule_id + file + YAML path)

**Description**: Hash `(rule_id, file_path, yaml_path)` for a location-stable but content-independent identity.

**Pros**:
- Survives content edits within the same task (fingerprint stays stable)
- Simple to compute

**Cons**:
- Moving a task to a different file breaks the fingerprint
- A task that changes from safe to dangerous retains its suppression — the opposite of the desired behavior
- Not portable across projects

**Why not chosen**: Accepting a violation at a position rather than for specific content is fundamentally unsafe. If the task changes, the accept decision should be re-evaluated.

### Alternative 3: Engine-computed fingerprint (new proto field)

**Description**: Add a `fingerprint` field to the `Violation` proto and have the engine compute it.

**Pros**:
- All consumers get the fingerprint for free
- Guaranteed consistency — one computation point

**Cons**:
- Violates invariant 5 (stateless engine) — the engine would need to know about the normalization algorithm
- Fingerprinting is a consumer concern; different consumers could want different normalization strategies
- Engine changes are heavier-weight than consumer changes (proto regen, coordinated deployment)

**Why not chosen**: The engine already emits `rule_id` and `original_yaml`. Computing a hash from two existing fields is trivial for any consumer. Pushing this into the engine adds coupling for no benefit and violates the architectural boundary between engine (stateless emitter) and persistence layer (Gateway/CLI).

## Consequences

### Positive

- Accept/ignore decisions survive line-number changes, reformatting, file moves, and project boundaries
- Different content always produces different fingerprints — no accidental over-suppression
- No engine changes required — fingerprinting uses existing proto fields
- Clean separation: engine emits data, consumers derive and act on fingerprints
- Two storage options (Gateway DB, CLI file) serve both enterprise and standalone use cases
- User-tunable granularity allows power users to broaden suppression scope when appropriate

### Negative

- Normalization algorithm is a versioned contract — changing it invalidates all existing fingerprints. Mitigation: include an algorithm version byte in the hash input if the algorithm ever needs to change.
- Rule ID permanence is a governance constraint that must be enforced operationally. Mitigation: the rule catalog (ADR-041) rejects registration of previously used IDs.
- The CLI file (`.apme/suppressions.yml`) contains opaque hashes that are not human-readable on their own. Mitigation: each entry includes the `rule_id` and a `reason` field for human context.

### Neutral

- Existing `# apme:ignore` inline annotations are unaffected — they remain a valid suppression mechanism at the code level
- The `_deduplicate_violations` logic in the Primary and CLI output is unaffected — deduplication and suppression are orthogonal concerns
- SARIF export can include fingerprints as `partialFingerprints`, enabling integration with external baseline tools

## Implementation Notes

### Phase 1: Fingerprint computation library

1. Create a shared `fingerprint` module (usable by both Gateway and CLI) with:
   - `normalize_yaml(text: str) -> str` — strip comments, normalize whitespace
   - `compute_fingerprint(rule_id: str, original_yaml: str, mode: str = "full") -> str` — SHA-256 hex digest
2. Unit tests covering normalization edge cases (empty YAML, comment-only, block scalars with `|`/`>`, multiline values, Jinja2 expressions, lines that resemble comments inside scalar content)
3. `canonicalize_rule_id(raw_id: str) -> str` — strips legacy prefixes (e.g. `native:`, `opa:`) to produce the bare rule ID for hashing

### Phase 2: CLI suppression file

1. `apme suppress <fingerprint-or-interactive>` command to add entries to `.apme/suppressions.yml`
2. `apme check --show-suppressed` to include suppressed violations in output
3. During `apme check`, compute fingerprints and filter against the suppression file

### Phase 3: Gateway suppression table

1. Add `suppressions` table to the Gateway DB schema
2. REST endpoints for CRUD on suppressions
3. UI toggle to suppress/unsuppress individual violations
4. UI toggle to show/hide suppressed violations

### Phase 4: Cross-project suppression sharing

1. Gateway API to export/import suppressions as YAML (same format as CLI file)
2. Global vs project-scoped suppressions in the Gateway UI

## Related Decisions

- [ADR-008](ADR-008-rule-id-conventions.md): Rule ID conventions — extended by the permanence guarantee
- [ADR-009](ADR-009-remediation-engine.md): Separate remediation engine — suppression is orthogonal to remediation
- [ADR-020](ADR-020-reporting-service.md): Reporting service — suppressions are a consumer-side concern, consistent with the engine-emits/Gateway-persists model
- [ADR-029](ADR-029-web-gateway-architecture.md): Web Gateway architecture — the Gateway is the natural home for suppression persistence
- [ADR-041](ADR-041-rule-catalog-override-architecture.md): Rule catalog — rule disable is coarser than fingerprint suppression; `enforced` flag overrides suppressions
- [ADR-044](ADR-044-node-identity-progression-model.md): Node identity — `original_yaml` on the Violation proto is the content input for fingerprinting

## References

- `proto/apme/v1/common.proto` — `Violation` message with `rule_id` (field 1) and `original_yaml` (field 16)
- `src/apme_engine/daemon/primary_server.py` — current `_deduplicate_violations` using `(rule_id, file, line)`
- `src/apme_engine/engine/content_graph.py` — `ViolationKey = (node_id, rule_id)` in the violation ledger
- SARIF specification — `partialFingerprints` property for baseline matching

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-05-21 | Brad (cidrblock) | Initial proposal |
| 2026-05-21 | David (djdanielsson) | Address review: YAML-aware normalization for block scalars, rule_id canonicalization, explicit delimiter in all modes |
| 2026-07-08 | Brad Thornton | Accepted — CLI suppress, fingerprint lib, Gateway CRUD, and UI implemented; rule_module mode and export/import remain |
