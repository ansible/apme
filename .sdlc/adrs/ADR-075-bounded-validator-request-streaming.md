# ADR-075: Bounded Streaming for Oversized Validator Requests

## Status

Implemented

## Date

2026-11-06

## Context

The Ansible validator needs the uploaded project files, `ansible.cfg`, and local
plugin sources to resolve modules and actions with the same search context as
the engine. A unary `ValidateRequest` is limited to 50 MiB. Large projects can
exceed that limit when task files and plugin context are combined.

Filtering unknown Python files or truncating plugin context can silently change
Ansible resolution. Raising the unary limit increases per-call memory use and
still leaves a fixed message-size ceiling. The transport must preserve the
complete request while keeping each gRPC message bounded.

## Decision

**Keep unary validation for requests at or below the 50 MiB limit and use a
client-streaming gRPC method for larger Ansible requests.** The Engine streams
the serialized `ValidateRequest` in chunks of at most 8 MiB. The Ansible
validator reassembles the request and delegates to the existing unary
validation path. The receiver rejects total streamed payloads above 512 MiB.

## Alternatives Considered

### Raise the unary message limit

**Description**: Increase `grpc.max_send_message_length` and
`grpc.max_receive_message_length` for validator calls.

**Pros**:
- Requires no new RPC method.
- Preserves all files in one request.

**Cons**:
- Raises per-call memory pressure for every large unary message.
- Still fails when a project exceeds the new fixed limit.

**Why not chosen**: A higher ceiling does not bound individual transport
messages independently and does not provide a path for projects larger than the
chosen value.

### Filter or truncate plugin context to fit

**Description**: Omit selected files when the request approaches the unary
limit.

**Pros**:
- Keeps the current unary service contract.
- Reduces network and memory use for some projects.

**Cons**:
- The Engine cannot reliably infer which local files imported plugins need.
- Truncation can cause module or action resolution to differ from the uploaded
  project.

**Why not chosen**: Silently dropping source files would compromise Ansible
resolution and produce incomplete validation.

## Consequences

### Positive

- Large requests preserve the exact file and hierarchy context used by unary
  validation.
- Each gRPC message stays below the 50 MiB unary limit.
- The normal unary path remains unchanged for smaller requests.

### Negative

- The validator protocol has an additional client-streaming RPC and generated
  stubs must be regenerated when it changes.
- Both processes temporarily hold the serialized request and its parsed form.
- Requests above 512 MiB fail with a validator error rather than being
  truncated.

### Neutral

- The streaming method is used only by the Ansible validator; other validators
  continue to use `Validate`.

## Implementation Notes

- The Engine selects project configuration, configured plugin roots, standard
  role plugin directories, and collection plugin trees without including
  arbitrary Python files based only on a directory name.
- When the Ansible request exceeds the unary limit, the Engine splits its
  serialized protobuf into 8 MiB `ValidateRequestChunk` messages.
- The Ansible server enforces the 512 MiB stream limit, parses the complete
  protobuf, and calls the existing `Validate` handler.
- Both the initial scan and task rescans use the same size-aware call path.

## Related Decisions

- ADR-001: gRPC for inter-service communication
- ADR-007: Fully async gRPC servers
- ADR-022: Session-scoped venvs with lifecycle management

## References

- PR #818: https://github.com/ansible/apme/pull/818
- Issue #834: https://github.com/ansible/apme/issues/834

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-06 | AI agent | Record bounded streaming for oversized Ansible validator requests |
