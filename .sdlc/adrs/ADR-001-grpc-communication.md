# ADR-001: gRPC for Inter-Service Communication

## Status

Accepted

## Date

2026-02

## Context

APME is a multi-service system. The Primary orchestrator fans out to multiple validator backends, each in its own container. We needed a protocol for this inter-service communication.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| gRPC (protobuf) | Binary encoding, streaming, bidirectional, typed contracts, code generation | Requires proto compilation, less browser-friendly |
| REST/JSON | Universal, browser-friendly, no compilation | Verbose, no streaming, no type safety, manual validation |
| JSON-RPC | Lightweight, language-agnostic | No streaming, no code generation, limited tooling |

## Decision

**Use gRPC for all inter-service communication** (Primary ↔ Validators, CLI ↔ Primary).

HTTP/REST is used only for:
- OPA REST API internally (OPA's native interface)
- Reserved for future presentation layers (web UI)

## Rationale

- Typed `.proto` contracts generate client and server stubs — adding a validator means implementing one RPC
- Binary encoding is efficient for the hierarchy payload (large nested JSON structures)
- Bidirectional streaming is available if needed for future large-project scanning
- The penalty (proto compilation step) is minor and automated via `scripts/gen_grpc.sh`

> "I find grpc fast and bidirectional when needed" — user decision

## Consequences

### Positive
- Strong type safety across service boundaries
- Efficient binary serialization
- Generated code reduces boilerplate
- Streaming support for large payloads

### Negative
- Requires proto compilation step in build
- Less browser-friendly for future web UI
- Team needs familiarity with protobuf

## Implementation Notes

- Proto files live in `proto/` directory
- Generate stubs via `scripts/gen_grpc.sh`
- All validators implement the same `Validate()` RPC interface

## Related Decisions

- ADR-004: Podman pod deployment
- ADR-005: Fixed ports vs service discovery
- ADR-007: Async gRPC servers
