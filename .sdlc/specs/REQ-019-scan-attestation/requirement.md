# REQ-019: Scan Attestation and Evidence Generation

## Metadata

- **Phase**: Unassigned (Enterprise feature)
- **Status**: Draft
- **Created**: 2026-08-13
- **Origin**: Bank of America field engagement (Edward Quail → Richard Henshall)

## Problem Statement

Regulated enterprises require verifiable evidence that policy validation occurred before code is approved for execution. Current APME output (SARIF, JSON) provides scan results but lacks cryptographic attestation that would satisfy audit requirements for provenance and tamper-evidence.

## User Stories

**As a** platform owner in a regulated enterprise,
**I want to** generate verifiable attestation records during automated CI/CD builds,
**So that** only policy-compliant code is approved to execute and I have audit evidence for regulators.

**As a** compliance officer,
**I want to** verify that a specific scan result was produced by APME at a specific time,
**So that** I can demonstrate to auditors that the validation pipeline was not bypassed.

**As a** CI/CD pipeline operator,
**I want to** store signed attestations alongside build artifacts,
**So that** the evidence chain is preserved and queryable.

## Acceptance Criteria

### AC-1: Signed Scan Attestation

**Given** a completed scan with violations or clean result
**When** attestation output is requested (`--attest` CLI flag or `options.attest=true` on operation creation)
**Then** APME produces an in-toto Statement inside a DSSE envelope containing:
- SARIF payload as the predicate
- `attestedAt` timestamp (RFC 3339) recording Gateway envelope creation time (UTC system clock)
- `scanCompletedAt` timestamp (RFC 3339) recording when Primary finished the scan
- Subject (scanned project content fingerprint — see Subject Digest below)
- A DSSE signature over the Statement (Sigstore keyless via Gateway workload identity, or a configured Gateway signing key). Implementation may use `sigstore-python` and/or `cosign`; the wire contract is DSSE (+ Sigstore Bundle v0.3 for keyless), not a Cosign-specific blob format.

**Subject Digest** (`subject[].digest.sha256`):
- Covers all source files present in the content graph for the scan (not build artifacts or SARIF output)
- File set and digests are computed by **Gateway** from its project clone using paths from persisted `content_graph_json` (see contract — no Primary per-file hash RPC)
- Canonicalization: relative paths sorted lexicographically (POSIX `/`, NFC Unicode), each entry contributes `path + "\0" + sha256(raw_file_bytes) + "\n"`; project digest is SHA-256 of the UTF-8 manifest string (hex-encoded in the envelope)
- Statement JSON bytes for signing use RFC 8785 (JCS) canonicalization (see contract)
- Verifiers reproduce the digest from the same scanned tree to bind the attestation to scanned content (see AC-4)

### AC-2: CLI Integration

**Given** the CLI check command
**When** `apme check --attest` is invoked
**Then** scan results and attestation use a single, unambiguous output container:
- With `--json`: one JSON document on stdout shaped as `{"scan": <scan-results>, "attestation": <signed-envelope>}`
- Without `--json`: scan results go to stdout as today; the signed attestation is written to `--attest-output <path>` (default: `attestation.json` in the working directory)
- The CLI never concatenates two independent JSON documents on stdout

### AC-3: Gateway REST Endpoint

**Given** a completed scan stored in Gateway
**When** `GET /api/v1/activity/{activity_id}/attestation` is called by an authenticated caller authorized for that activity (`activity_id` ≡ `scan_id`)
**Then** returns the signed attestation for that scan (see contract for error and pending states)

### AC-4: Verification Command

**Given** a signed attestation file
**When** `apme verify-attestation [--path <source-tree>] <file>` is invoked
**Then** validates signature and trust policy, prints verification status to stdout, and exits:
- `0` when valid (including subject digest match when `--path` is provided)
- `1` when signature, trust policy, or subject digest checks fail
- `2` on file read or parse errors

**Subject digest verification**:
- With `--path <source-tree>`: recompute the Subject Digest using the same file-selection and canonicalization rules as AC-1 and compare to `subject[].digest.sha256` in the attestation. Mismatch fails with exit `1`.
- Without `--path`: verify signature and trust policy only; report the signed digest value but do not assert it matches local content (stdout includes `"digestVerified": null`).

### AC-5: Keyless and Keyed Signing

**Given** attestation is requested
**When** no signing key is configured on Gateway
**Then** use Sigstore keyless signing with **Gateway workload identity** (see design.md), subject to the trust policy below. The attested signer identity is the Gateway's configured OIDC identity — not an ambient CI job identity unless that identity is explicitly configured as the Gateway signing identity.

**Given** attestation is requested
**When** `APME_SIGNING_KEY` environment variable is set on Gateway (or `--signing-key <id-or-path>` forwarded as a non-secret Gateway key reference — see contract)
**Then** use the provided key for signing; CLI and Primary never load private key material

**Sigstore trust policy** (applies to keyless signing and verification):
- **OIDC issuer**: configurable allowlist of issuers that may appear on Gateway signing certificates (defaults are environment-specific; production Helm documents the issuer for the Gateway service account / workload identity). Reject signatures whose certificate OIDC issuer is not allowlisted.
- **Issuer-to-identity mapping**: each allowed OIDC issuer must have a **non-empty** configured identity pattern (regex or glob on certificate `subject` / OIDC `sub`) that matches the **Gateway signing identity** (for example a Kubernetes service account subject). Reject verification when an allowlisted issuer has no matching identity policy configured. Wildcards that trust every workload from an issuer (e.g., `*`) are forbidden.
- **Audience**: certificate OIDC audience must match configured value (default: `sigstore`).
- **Fulcio roots**: verify against bundled Sigstore public root certificates; roots are versioned and updatable via Gateway configuration.
- **Rekor policy**: keyless attestations must have a verifiable Rekor transparency-log entry in the configured Rekor instance (default: public Sigstore Rekor).
- **Signing availability**: when Rekor/Fulcio/OIDC are unreachable and no keyed fallback is configured, signing fails with `SIGSTORE_UNAVAILABLE`. Air-gapped deployments use keyed signing only (see design.md).
- **Verification availability**: keyless verification of a persisted Sigstore Bundle uses embedded certificate, claims, and Rekor proof with local trust roots — **independent of live OIDC, Fulcio, or Rekor reachability**. `TRUST_SERVICE_UNAVAILABLE` applies only when required verification inputs are missing (no bundle, no local trust root, or live lookup required for an incomplete envelope) — not merely because external services are unreachable.

## Technical Approach

### Architecture (Invariant 11 Compliance)

Signing requires outbound calls (Sigstore Rekor transparency log). Per ADR-020/ADR-029, this belongs in **Gateway**, not engine:

```text
┌─────────┐     REST / CLI      ┌─────────────┐     gRPC      ┌─────────┐
│  Client │────────────────────▶│   Gateway   │──────────────▶│ Primary │
│ --attest│                     │ (orchestrate│◀─────────────│ (SARIF / │
└─────────┘                     │  persist)   │  ReportFix*  │  graph)  │
                                └──────┬──────┘               └─────────┘
                                       │ sign + store
                                       ▼
                                 ┌───────────┐
                                 │ Sigstore  │
                                 │ (Rekor)   │
                                 └───────────┘
```

CLI and REST clients talk to Gateway (or a co-located daemon that includes Gateway). Primary remains emit-only; Gateway claims signing, talks to Sigstore, and stores the Bundle.

### Attestation Format

in-toto Statement (APME Scan Predicate):
```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {"name": "<project>", "digest": {"sha256": "<content-hash>"}}
  ],
  "predicateType": "https://apme.io/attestations/scan/v1",
  "predicate": {
    "scanId": "<uuid>",
    "scanCompletedAt": "<RFC3339>",
    "attestedAt": "<RFC3339>",
    "toolVersion": "<apme-version>",
    "sarif": { ... },
    "verdict": "pass|fail",
    "violationCount": <n>
  }
}
```

- `scanCompletedAt`: UTC timestamp when Primary finished the scan (from scan metadata)
- `attestedAt`: UTC timestamp when Gateway assembled and signed the envelope (authoritative audit time for the attestation record)

### Dependencies

- `sigstore-python` for keyless signing (and Bundle assembly)
- `cosign` binary optional for keyed signing helpers
- Architectural compatibility: Gateway-side signing aligns with invariant 11 (engine never queries out); operational decisions (offline mode, key storage, retention, signing identity, idempotency) are recorded in [design.md](design.md)

## Out of Scope

- SLSA Build provenance (attestation of APME itself — separate concern)
- Custom attestation predicates (v1 ships with scan predicate only)
- Hardware key support (HSM/YubiKey — future enhancement)
- Binding attestation signer identity to an arbitrary caller CI job without configuring that identity as the Gateway signing identity

## References

- [Industry Gap Analysis](../../research/industry-gap-analysis.md) — identifies artifact signing as high-priority gap
- [ADR-020: Event Sink Abstraction](../../adrs/ADR-020-event-sink-abstraction.md)
- [ADR-029: Gateway Persistence](../../adrs/ADR-029-gateway-persistence.md)
- [in-toto Specification](https://github.com/in-toto/attestation)
- [Sigstore](https://www.sigstore.dev/)
- [SLSA](https://slsa.dev/)
