# ADR-077: Gateway Git fetch network and storage boundaries

## Status

Implemented

## Date

2026-11-16

## Context

Git dependency URLs are project-controlled. DNS checks before launching Git do
not constrain later resolution or redirects. Upload limits after checkout do not
bound the bytes and metadata Git can write while fetching and expanding objects.
Gateway must remain the fetch owner (ADR-020/029) and run without mount privilege.

## Decision

Provide a Gateway-local async snapshot context that pins an approved resolved
address with Git's `http.curloptResolve` (numeric URLs already select their
validated address), disables redirects and ambient Git/proxy
configuration, and only allows HTTPS. Require all DNS answers to satisfy policy;
private unicast addresses require deployment CIDRs. Always deny loopback,
link-local, multicast and unspecified addresses, plus standard NAT64, 6to4 and
Teredo ranges that can conceal restricted IPv4 targets. Preserve TLS verification.

Require a pre-provisioned tmpfs mount root bounded to at most 512 MiB and 65536
inodes. Verify its filesystem and capacities before each fetch, then revalidate
and retain an open mount descriptor for allocation and child Git access. All Git files,
including TMPDIR, live under that mount, sharing its hard kernel quotas across
concurrent operations. Context exit removes the checkout. Canceled and timed-out
Git process groups are stopped and reaped before deleting their files, including
cancellation while subprocess creation is pending. Classify bounded stderr into
fixed diagnostic categories without exposing raw server messages. Drain stderr
through EOF with a finite cleanup deadline before classifying failures. Missing
or malformed mount identity fails closed as a storage error. A required CI
gate exercises concurrent production HTTPS snapshots with real kernel quotas
for packed objects, checkout bytes and loose-object inodes.

## Alternatives Considered

### Validate hostname, then let Git resolve

Easy to implement, but a changed DNS response, proxy or redirect can bypass the
check. Rejected because it does not enforce the connected destination.

### Poll directory size while Git runs

Portable and useful for telemetry, but checks race writes and cannot impose a
hard aggregate bound on object expansion or concurrent writers. Rejected as a
security boundary; post-fetch upload limits remain a separate validation layer.

## Consequences

The Gateway needs a Linux tmpfs mount provisioned by its deployment owner and a
Git build supporting `http.curloptResolve`. Missing enforcement fails closed.
Deployments must budget memory and storage for concurrent fetches; one operation
may fill the shared quota and cause another to fail. No new backend service or
engine outbound connection is introduced. Existing project clone operations are
not changed by this prerequisite; PR #879 consumes the helper for requirements.

## References

- [#884](https://github.com/ansible/apme/issues/884): network destinations
- [#885](https://github.com/ansible/apme/issues/885): fetch storage quota
- [Usage](../../docs/guides/SAFE_GIT_FETCH.md)
- ADR-007, ADR-020, ADR-029; ADR-076 in feature PR #879

## Revision History

| Date | Change |
|------|--------|
| 2026-11-16 | Added Gateway-local fetch boundary and operator requirements. |
| 2026-11-16 | Addressed review: translated-address policy, held mount descriptors, process-creation cancellation, bounded diagnostics and mandatory production-path quota CI. |
| 2026-11-16 | Final review: fail closed on absent/malformed mount identity and drain buffered diagnostics through bounded EOF cleanup. |
