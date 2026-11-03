# ADR-074: Authenticated Host Upload Session Channel

## Status

Partially Implemented

## Date

2026-10-30

## Context

REQ-021 requires paste/upload in personal RHDH Local and multiuser Portal.
Browser WebSockets cannot use the authenticated Backstage FetchApi. The current
Gateway FixSession WebSocket protocol already implements upload/remediation.

## Decision

Provide an optional host backend channel that relays the unchanged Gateway
WebSocket frames over authenticated HTTP commands and a streaming HTTP response.
The existing session hook accepts an injected socket interface. Gateway/Engine
remain the sole owners of scan, remediation, approval and session state.

The host channel retains only transport connections and session ownership bindings.
Every HTTP request authenticates and authorizes the caller; resume requires a
known binding to that principal. Missing bindings fail closed. Routes use opaque
resource IDs, never bearer tokens in query strings. Bound connections, payloads,
pre-stream buffering and orphan lifetime. Clean up on disconnect and shutdown.
Multi-replica hosts require sticky routing for active channels; after a host restart
uploads must be restarted when ownership bindings are gone. Repository operations
retain their existing authoritative reconnect behavior.

Local enables this through the optional APME backend plugin. Portal injects its
own entitlement/scan authorization when mounting the same transport router. APME
releases this backend package alongside client/workflow/frontend artifacts.

This router is a host-side external client of the Gateway's existing public
upload session endpoint (ADR-029), just as the other host adapters consume the
Gateway's public REST/SSE contracts. It is outside the engine service boundary.
Gateway-to-Engine and validator traffic remains gRPC (ADR-001/039); the host
never connects directly to Engine or owns FixSession execution.

Rate-limit all channel routes before host authorization or body parsing. Parse
creation requests with an 8 KiB limit after authorization; message requests keep
the bounded 70 MiB envelope. The default quota is 6000 requests per minute per
client IP per backend process and can be lowered by the host. Hosts must configure
trusted proxy handling correctly. Dispose rate-limit storage on shutdown and
consume or cancel every HTTP response body on success, denial and cleanup paths.

## Alternatives Considered

1. Direct browser WebSocket with a bearer query parameter: rejected because it
   exposes credentials and bypasses authenticated host fetch.
2. Native WebSocket upgrade plus cookie authorization: feasible, but requires
   host server upgrade registration, origin checks and cookie policy integration
   outside the ordinary Backstage router. No such transport currently exists.
3. Authenticated HTTP channel (chosen): reuses normal host authentication and
   the existing Gateway protocol, at the cost of ephemeral transport bindings.

## Consequences

Additional HTTP requests during uploads and an optional backend package.
No parallel scan engine, durable domain store or backend API changes. Runtime
permission, reconnect and resource acceptance remain required before retirement.

## Related Decisions

ADR-028, ADR-029, ADR-039, ADR-060 and ADR-073 remain authoritative.

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-02 | Codex | Clarified the external Gateway client boundary, pre-parse authorization, request rate limits and response-body cleanup; two-host runtime acceptance remains in progress |

## Concurrent channel creation

Channel capacity is checked after the asynchronous host Gateway lookup and
immediately before synchronous socket creation and registration. No await may
separate that check from registration: concurrent requests must not exceed the
per-owner or global channel limits while host settings resolve.
