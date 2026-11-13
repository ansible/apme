# ADR-076: Gateway resolution of Git requirements

## Status

Implemented

## Date

2026-11-13

## Context

Galaxy installs do not support Git collection/role declarations. The Engine cannot
query external services, owns session venvs, and must keep dependency source out of
project remediation. Users need global enablement and project credential overrides.

## Decision

The Gateway resolves explicit Git requirements before opening FixSession. An
additive ScanChunk.dependency_files field streams credential-free dependency files
with the existing upload bounds. Engine session state stores them separately from
project files. Their content hash selects a separate venv; only Engine writes it.
Git collection identities are excluded from Galaxy resolution. Gateway DB stores
the disabled-by-default singleton policy and optional project requirements token.
Credentials follow requirements override, project token, deployment fallback.

## Alternatives Considered

### Fetch in Engine

Simple runtime install integration, but violates ADR-020/ADR-029 outbound boundaries
and would transmit Git credentials to the engine. Rejected.

### Add dependencies to project uploads

Reuses the current wire format, but makes dependencies project remediation targets
and may include third-party source in generated PRs. Rejected.

## Consequences

- Dependency fetching remains at the edge; validators retain read-only venv access.
- Gateway and Engine must be upgraded together to use the new dependency field.
- Different Git snapshots have separate venvs, trading disk space for reproducibility
  and isolation from warm state. Disk cache cleanup requires operational management;
  automatic safe venv reaping is a tracked follow-up.
- Tokens use the existing database secret-storage model; API responses expose presence only.
- Initial scope supports direct HTTPS Git declarations, not recursive Git manifests.

## References

[REQ-023](../specs/REQ-023-git-requirements/requirement.md), ADR-020, ADR-029,
ADR-045, ADR-060.


## Revision History

| Date | Change |
|------|--------|
| 2026-11-13 | Implemented global policy, project credential override, gateway fetching and isolated Engine dependency payloads (REQ-023). |

Disk cleanup follow-up: [ansible/apme#878](https://github.com/ansible/apme/issues/878).
