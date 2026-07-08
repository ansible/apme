# ADR-060: REST API Versioning Contract

## Status

Accepted

## Date

2026-07-08

## Context

The Gateway REST API (`/api/v1`) was originally designed as a
backend-for-frontend (BFF) for the APME dashboard UI. ADR-038 formalized
it as the public data-sharing interface for platform consumers, with a
one-line stability clause: "Breaking changes require a new version prefix
(`/api/v2`). Additive changes are allowed under `/api/v1`."

That clause is insufficient now. A Backstage plugin team is actively
building against the API, making it a real external contract with a
separate development lifecycle. Without an enforced invariant:

- An agent refactoring Gateway routes could rename a field or change a
  response shape without realizing it breaks the Backstage plugin.
- A well-intentioned "cleanup" could remove a deprecated field that an
  external consumer still relies on.
- Response envelope changes (pagination, error format) could silently
  break client parsing.

The API is no longer an internal detail — it is a public interface with
external consumers who cannot coordinate releases with us.

### What constitutes a breaking change

- Removing or renaming a field from a response body
- Changing a field's type (e.g. string → integer, object → array)
- Changing the semantic meaning of a field
- Removing an endpoint
- Changing an endpoint's URL path
- Making a previously optional request parameter required
- Changing response status codes for existing success/error cases
- Changing the pagination or envelope structure
- Removing or renaming a query parameter

### What is NOT a breaking change

- Adding a new endpoint
- Adding a new optional field to a response body
- Adding a new optional query parameter
- Adding a new enum value to a field (when clients are expected to
  tolerate unknown values)
- Adding a new error code for a previously-unhandled case

## Decision

**We will treat the Gateway REST API under `/api/v1` as a versioned
public contract. Breaking changes to existing endpoints require a new
version prefix (`/api/v2`). This is an architectural invariant.**

Specifically:

1. **No breaking changes under `/api/v1`.** Any change that would alter
   the behavior, shape, or availability of an existing endpoint for
   existing callers requires a version bump to `/api/v2`.

2. **Additive changes are permitted.** New endpoints, new optional
   response fields, and new optional query parameters may be added under
   `/api/v1` without a version bump.

3. **Version bumps require an ADR.** Moving to `/api/v2` is an
   architectural decision that must be documented with migration
   guidance for existing consumers.

4. **Deprecation before removal using RFC 9745 and RFC 8594.** If a
   field or endpoint will be removed in a future version, the Gateway
   must signal deprecation using standard HTTP headers:
   - [`Deprecation`](https://www.rfc-editor.org/rfc/rfc9745.html)
     (RFC 9745): set to `@<unix-timestamp>` (an SF-Integer per the
     Structured Fields syntax) or `?1` (boolean true) indicating the
     endpoint is deprecated.
   - [`Sunset`](https://www.rfc-editor.org/rfc/rfc8594.html)
     (RFC 8594): set to an HTTP-date indicating when the endpoint will
     be removed.
   - A `Link` header with `rel="sunset"` pointing to migration
     documentation.

   These headers must be present for at least one release cycle before
   the endpoint is removed in the next major version. Implementation
   is via FastAPI middleware on the Gateway.

5. **This rule applies to agents.** AI agents must not modify existing
   endpoint response schemas, URL paths, or query parameter semantics
   without explicit human approval and an accompanying version bump ADR.

## Alternatives Considered

### Alternative 1: Rely on ADR-038 stability clause

**Description**: Keep the existing one-line clause in ADR-038 without
elevating it to an invariant.

**Pros**:
- No additional documentation
- Already stated

**Cons**:
- Not enforced — agents don't read ADR-038 before modifying routes
- Not an invariant in AGENTS.md, so it has no teeth
- Buried in a "Proposed" ADR alongside unrelated topics (webhooks, auth)

**Why not chosen**: A contract with external consumers needs a stronger
commitment than a single sentence in a proposed ADR.

### Alternative 2: Full OpenAPI schema validation in CI

**Description**: Generate an OpenAPI schema from FastAPI and diff it
against a checked-in baseline in CI. Any breaking diff fails the build.

**Pros**:
- Fully automated enforcement
- Catches accidental breaks before merge

**Cons**:
- Significant implementation effort
- OpenAPI diffing tools have false positives
- Premature while the API is still maturing

**Why not chosen**: Worth pursuing as a follow-up, but the invariant
provides immediate protection. Automated enforcement can be added
incrementally.

## Consequences

### Positive

- External consumers (Backstage plugin, CI/CD integrations) can depend
  on the API without fear of silent breakage
- Forces intentional API evolution — version bumps are deliberate
  decisions, not accidents
- AI agents are explicitly constrained from casual route refactoring

### Negative

- Limits agility for internal UI changes that share the same routes —
  if the UI needs a different response shape, it must either use a new
  endpoint or negotiate a version bump
- Accumulated deprecated fields may create API cruft over time

### Neutral

- The CLI is unaffected — it communicates via gRPC to Primary, not REST
- Internal gRPC interfaces between engine services are not covered by
  this ADR (they have no external consumers)

## Implementation Notes

- Add as architectural invariant 17 in `AGENTS.md`
- **RFC 9745/8594 middleware**: implement as FastAPI middleware that
  reads a deprecation registry (endpoint → deprecation date, sunset
  date, migration link) and injects `Deprecation`, `Sunset`, and
  `Link` headers into responses for registered endpoints
- Future work: OpenAPI schema diffing in CI for automated enforcement
- The Backstage plugin team should be given a link to the existing
  endpoint documentation and this ADR as the stability guarantee

## Related Decisions

- [ADR-029](ADR-029-web-gateway-architecture.md): Web Gateway
  architecture (defines the Gateway and its REST surface)
- [ADR-038](ADR-038-public-data-api.md): Public Data API for Platform
  Consumers (formalizes the API as public, contains the original
  stability clause this ADR elevates)
- [ADR-001](ADR-001-grpc-communication.md): gRPC for inter-service
  communication (internal interfaces, not covered by this ADR)

## References

- [RFC 9745](https://www.rfc-editor.org/rfc/rfc9745.html) — The
  Deprecation HTTP Header Field
- [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594.html) — The Sunset
  HTTP Header Field
- [PR #351](https://github.com/ansible/apme/pull/351) — Productization
  plan pre-read (independently identified RFC 9745/8594 as the
  deprecation mechanism)
- [PR #378](https://github.com/ansible/apme/pull/378) — Discussion
  that prompted this ADR
- Backstage plugin team (external consumer actively building against
  `/api/v1`)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-08 | Brad Thornton | Initial proposal, accepted |
