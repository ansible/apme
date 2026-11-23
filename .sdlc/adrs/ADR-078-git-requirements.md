# ADR-078: Gateway resolution of Git requirements

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
project files. Their content hash and effective Galaxy requirements select a separate venv; only Engine writes it.
The Gateway validates source collection metadata before uploading the snapshot.
The Engine's venv installer assembles the local wheel from those bounded files
using the same builder as Galaxy Proxy. The venv resolver installs these wheels
together with Galaxy collections. Explicit Git wheels take precedence over Hub
candidates for the same identity. For Hub preparation, the Proxy receives the
Git collection identities and versions as scan-local candidates. Its shared
cache contains the Hub artifacts. For mixed installs the Proxy also relays
Python wheels referenced by Hub collections. Python requirements from Git
snapshots remain collection content. Git roles are materialized directly.
Git wheels carry the collection's Ansible Core compatibility constraint into
the shared resolver, which keeps the session's selected Core version pinned.
Gateway DB stores
the disabled-by-default singleton policy and optional project requirements token.
The upload contract carries collection files rather than opaque wheel artifacts,
so the Engine can enforce the same paths and file limits for every FixSession
client.
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
  Galaxy requirements, and isolation from warm state. Disk cache cleanup requires operational management;
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
| 2026-11-23 | Install Git collections as local wheels in the shared venv resolver. |
| 2026-11-23 | Enforce Git collection Ansible Core compatibility during wheel resolution. |

Disk cleanup follow-up: [ansible/apme#878](https://github.com/ansible/apme/issues/878).

[ADR-077](ADR-077-bounded-git-fetch.md) and prerequisite
[PR #886](https://github.com/ansible/apme/pull/886) enforce the fetch storage
budget and connection-time destination policy. The requirement resolver consumes
that shared snapshot context; there is no separate unguarded Git fetch path.
