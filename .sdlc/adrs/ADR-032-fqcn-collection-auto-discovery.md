# ADR-032: FQCN-Based Collection Auto-Discovery

## Status

Accepted

## Date

2026-03-19

## Context

When a project has no `requirements.yml` or `galaxy.yml`, the scanner produces a hierarchy but no collection specs are discovered. The cache pull is skipped and validators — especially the Ansible validator's M001-M004 module introspection — run without collection content available, degrading detection accuracy.

Modern Ansible content uses Fully Qualified Collection Names (FQCNs) in tasks (e.g. `community.general.nmcli`). The required collections are already embedded in the source; we just don't extract them.

## Decision

Derive collection requirements from FQCN module usage in the hierarchy payload, supplementing explicit `requirements.yml` declarations.

### Extraction

A new `_extract_collection_set()` function in `opa_payload.py` walks all `taskcall` nodes produced by `build_hierarchy_payload()` and extracts `namespace.collection` from any module name with 3+ dot-separated parts. `ansible.builtin` is excluded as it ships with ansible-core. The resulting sorted, deduplicated list is attached to the hierarchy payload as `collection_set`.

### Merge precedence

In `_scan_pipeline()`, collection specs are merged in priority order:

1. **Request specs** (from the gRPC caller) — highest precedence
2. **requirements.yml / galaxy.yml** — potentially version-pinned
3. **Hierarchy-derived collection_set** — bare specs (latest version)

A collection already covered by a higher-priority source is not added again. This ensures explicit version pins from `requirements.yml` are never overridden by bare FQCN-derived specs.

### Blocking behavior

`_ensure_collections_cached()` continues to block before the validator fan-out. This is correct: the Ansible validator needs collections symlinked into the venv. No changes to the blocking model.

## Consequences

- Projects without `requirements.yml` will automatically have their required collections identified and cached before validation.
- Short module names (`copy`, `nmcli` without FQCN prefix) are not resolved — that requires collections to already be installed (chicken-and-egg). Only explicit FQCNs are extracted.
- The `collection_set` in the hierarchy payload is available to any consumer (CLI, web gateway, reporting) without re-parsing the project.
- No changes to the cache manager, venv builder, or dependency preparator (those are ADR-031 territory).

## Relates to

- [ADR-031](ADR-031-unified-collection-cache.md) — Unified Collection Cache (infrastructure layer; this ADR adds a discovery layer on top)
- [ADR-003](ADR-003-vendor-ari-engine.md) — Vendored ARI engine (source of the hierarchy data)
