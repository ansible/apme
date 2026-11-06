# ADR-058: Collection Dependency Suggestion for Unresolved Modules (R501)

## Status

Proposed

## Date

2026-06-23

## Context

Rule R501 ("Suggest collection/role dependency") is a planned advisory rule that suggests which Ansible collection provides a given module when the module cannot be resolved. Today, the engine handles FQCN modules well (ADR-032 extracts `collection_set` from FQCN usage), but **short module names** (e.g. `nmcli`, `lvg`, `firewalld`) cannot be resolved without knowing which collection provides them.

When the Ansible validator encounters an unresolved module (L037), users get a "module could not be resolved" finding but no actionable guidance on which collection to install. R501 would close this gap by suggesting the providing collection.

### Forces

- **Short module names are common in legacy content**: Content written for Ansible < 2.10 uses bare module names everywhere. These are the projects most likely to need modernization guidance.
- **The engine never queries out** (invariant 11): The engine cannot call Galaxy's REST API at scan time. Any lookup data must be available locally within the pod.
- **Galaxy Proxy owns collection data** (ADR-031): Collections are installed via the Galaxy Proxy. The proxy already downloads and converts Galaxy tarballs to wheels — it has access to collection metadata.
- **Session venvs contain installed collections** (ADR-022): After `VenvSessionManager.acquire()`, the venv has all discovered collections installed. `ansible-doc --list` in that venv can enumerate available modules.
- **Performance matters**: Module resolution happens in the scanner's hot loop. Any lookup must be fast (microsecond-scale hash lookup, not subprocess or network call).

## Decision

**We will build a static module-to-collection mapping bundled with the engine image, and expose it to R501 via a lightweight lookup function.**

The mapping is a JSON file (`module_collection_map.json`) generated at image build time by querying a reference set of popular collections. R501 considers unresolved short module names on tasks or handlers (not include/import actions or FQCNs), using the same ansible-core resolution context as L037, then consults the static map. If a short name appears in the map, R501 emits an advisory finding suggesting the collection.

### Mapping structure

```json
{
  "nmcli": "community.general",
  "lvg": "community.general",
  "firewalld": "ansible.posix",
  "win_copy": "ansible.windows",
  "ec2_instance": "amazon.aws"
}
```

When a module name maps to more than one collection in the curated set, the map stores a single entry using this disambiguation order: prefer collections in the `ansible` meta-package namespace, then `community.*`, then alphabetical by `namespace.collection`. Ambiguous names that cannot be resolved to one collection are omitted from the map (R501 does not fire).

### Generation

A build-time script (`tools/generate_module_map.py`) installs a curated list of collections into a temporary venv and runs `ansible-doc --list --json` to enumerate all modules. The output is filtered to exclude `ansible.builtin` (always available) and written to a JSON file shipped with the engine image.

`ansible-doc --list` enumerates module names as exposed by installed collections; it does not fully capture `meta/runtime.yml` routing redirects or action-plugin aliases. The generator should also walk `meta/runtime.yml` in each installed collection where present to include redirect targets. Modules only reachable via routing with no direct listing may still be absent from the map.

### Rule behavior

R501 is an **advisory/suggestion** rule (severity INFO, consistent with other reporting rules such as R401):

- Fires once per PLAYBOOK node (aggregate, not per-task), walking all task and handler descendants — same pattern as R401
- Collects unresolved short module names, using L037's ansible-core resolution context, that appear in the static map
- Emits one finding per playbook listing suggested collections (e.g. `Unresolved modules may require: community.general (nmcli, lvg), ansible.posix (firewalld). Consider adding them to requirements.yml.`)
- Violation detail includes the module names and file/line references so users can locate affected tasks
- Does not fire for FQCN modules (already resolved by ADR-032)
- Does not fire for `ansible.builtin` modules
- Does not depend on a separate L037 finding — R501 can evaluate candidate short names independently, but uses the same semantic resolution context to avoid suggesting collections for resolvable modules

## Alternatives Considered

### Alternative 1: Query Galaxy Proxy at scan time

**Description**: Have the native validator call the Galaxy Proxy's API to look up module-to-collection mappings dynamically.

**Pros**:
- Always up-to-date with latest collection versions
- No static mapping to maintain

**Cons**:
- Violates invariant 11 (engine never queries out)
- Galaxy Proxy serves PEP 503 (package index), not module metadata — would need a new endpoint
- Network call in the scan path adds latency
- Creates a runtime dependency on Galaxy Proxy availability for a non-critical advisory rule

**Why not chosen**: Violates the engine's outbound-query prohibition and adds unnecessary coupling.

### Alternative 2: Query the session venv at scan time

**Description**: After `VenvSessionManager.acquire()`, run `ansible-doc --list` in the session venv to discover available modules and build the mapping dynamically.

**Pros**:
- Uses the exact collection set available to the project
- No static mapping to maintain
- Accurate for the specific scan context

**Cons**:
- Subprocess call in the scan path (`ansible-doc --list` takes 2-5 seconds)
- Only knows about collections already installed — cannot suggest collections not yet in the venv (which is R501's primary use case)
- Tight coupling to Ansible validator lifecycle

**Why not chosen**: R501's purpose is to suggest collections the user *doesn't have* — querying installed collections defeats the purpose. Also, subprocess overhead is too high for the scan path.

### Alternative 3: Embed the mapping in Rego (OPA rule)

**Description**: Implement R501 as an OPA policy rule with the module mapping embedded as Rego data.

**Pros**:
- OPA rules can consume static data natively
- No Python code needed

**Cons**:
- OPA operates on the hierarchy JSON which has already resolved FQCNs — short names may be normalized away
- Large data blob in Rego is awkward to maintain and test
- Misaligned with the Ansible validator's role (L037 is emitted by ansible-core plugin resolution; R501 should complement it)

**Why not chosen**: OPA is the wrong layer — module resolution context lives in the native validator's ContentGraph, not the hierarchy payload.

### Alternative 4: Gateway-mediated enrichment

**Description**: Have the Gateway query Galaxy's REST API for module metadata and attach it to the scan request as context enrichment (consistent with invariant 11's allowance for Gateway-side enrichment).

**Pros**:
- Architecturally clean (Gateway owns external queries)
- Always current data
- No static mapping in the engine

**Cons**:
- Gateway is not available in CLI daemon mode
- Adds Gateway as a required dependency for an advisory rule
- Galaxy's REST API doesn't have a direct "which collection provides module X" endpoint — would need to index all collection metadata

**Why not chosen**: Too heavy for an advisory INFO rule. The static mapping approach covers 95%+ of cases with zero runtime cost.

## Consequences

### Positive

- Users get actionable suggestions when legacy content uses unresolved short module names
- Zero runtime cost (hash lookup against bundled JSON)
- No architectural invariant violations
- Complements L037 (unresolved module) with a constructive suggestion
- Static mapping can be version-pinned and tested

### Negative

- Static mapping becomes stale as new collections are published — requires periodic regeneration
- Mapping only covers the curated collection set, not every collection on Galaxy
- Module name collisions across collections omit ambiguous entries rather than guessing

### Neutral

- The mapping generator script is a build-time tool, not a runtime dependency
- R501 remains an advisory rule — it suggests, not enforces

## Implementation Notes

1. **Phase 1**: Create `tools/generate_module_map.py` and generate initial mapping from top ~50 collections
2. **Phase 2**: Implement R501 GraphRule consuming the static mapping
3. **Phase 3**: Add mapping regeneration to the container build pipeline
4. **Curated collection list**: Start with collections in the `ansible` meta-package, `community.general`, `community.network`, `ansible.posix`, `ansible.windows`, and major cloud providers (`amazon.aws`, `google.cloud`, `azure.azcollection`)
5. **L037 integration**: The old graph heuristic remains disabled; L037 is now emitted by the Ansible validator using ansible-core's module and action plugin loaders with task collection context. R501 should use the same resolution context before suggesting a collection, while remaining independently reportable.
6. **Severity catalog alignment**: Update `severity_defaults.py` and `RULE_CATALOG.md` from medium to INFO when R501 is implemented (ADR-043 central table pattern).

## Related Decisions

- [ADR-031](ADR-031-unified-collection-cache.md): Galaxy Proxy as collection boundary — R501's mapping leverages the same collection ecosystem
- [ADR-032](ADR-032-fqcn-collection-auto-discovery.md): FQCN auto-discovery — R501 covers the inverse case (short names without FQCNs)
- [ADR-008](ADR-008-rule-id-conventions.md): Rule ID conventions — R501 uses the R (Risk/Reporting) prefix
- [ADR-009](ADR-009-remediation-engine.md): Validators are read-only — R501 is advisory, no file modification
- [ADR-043](ADR-043-default-severity-assignment.md): Default severity assignment — R501 severity should be registered as INFO at implementation time

## References

- [Issue #247](https://github.com/ansible/apme/issues/247): Doc-only rules requiring implementation
- [Rule R501 doc](src/apme_engine/validators/native/rules/R501_dependency_suggestion.md): Current stub documentation

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-06-23 | AI-assisted | Initial proposal |
| 2026-07-07 | Review | Renumbered ADR-056 → ADR-058 (ADR-056/057 taken upstream); clarified scope, severity, collisions, and L037 relationship |
| 2026-11-05 | Codex | Updated the L037 relationship after semantic resolution moved to the Ansible validator |
