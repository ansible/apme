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

### AC-1: Signed SARIF Attestation

**Given** a completed scan with violations or clean result
**When** attestation output is requested (`--attest` CLI flag or `attest=true` on scan creation)
**Then** APME produces an in-toto attestation envelope containing:
- SARIF payload as the predicate
- `attestedAt` timestamp (RFC 3339) recording Gateway envelope creation time (UTC system clock)
- `scanCompletedAt` timestamp (RFC 3339) recording when Primary finished the scan
- Subject (scanned project content fingerprint — see Subject Digest below)
- Cosign signature (keyless via Sigstore or local key)

**Subject Digest** (`subject[].digest.sha256`):
- Covers all source files parsed into the content graph for the scan (not build artifacts or SARIF output)
- Canonicalization: relative paths sorted lexicographically (POSIX `/`, NFC Unicode), each entry contributes `path + "\0" + sha256(raw_file_bytes) + "\n"`; project digest is SHA-256 of the UTF-8 manifest string (hex-encoded in the envelope)
- Verifiers reproduce the digest from the same scanned tree to bind the attestation to scanned content

### AC-2: CLI Integration

**Given** the CLI check command
**When** `apme check --attest` is invoked
**Then** scan results and attestation use a single, unambiguous output container:
- With `--json`: one JSON document on stdout shaped as `{"scan": <scan-results>, "attestation": <signed-envelope>}`
- Without `--json`: scan results go to stdout as today; the signed attestation is written to `--attest-output <path>` (default: `attestation.json` in the working directory)
- The CLI never concatenates two independent JSON documents on stdout

### AC-3: Gateway REST Endpoint

**Given** a completed scan stored in Gateway
**When** `GET /api/v1/scans/{scan_id}/attestation` is called by an authenticated caller authorized for that scan
**Then** returns the signed attestation for that scan (see contract for error and pending states)

### AC-4: Verification Command

**Given** a signed attestation file
**When** `apme verify-attestation <file>` is invoked
**Then** validates signature and trust policy, prints verification status to stdout, and exits:
- `0` when valid
- `1` when signature or trust policy checks fail
- `2` on file read or parse errors

### AC-5: Keyless and Keyed Signing

**Given** attestation is requested
**When** no signing key is configured
**Then** use Sigstore keyless signing (OIDC identity) subject to the trust policy below

**Given** attestation is requested
**When** `APME_SIGNING_KEY` environment variable is set on Gateway (or `--signing-key <path>` forwarded as a non-secret path reference — see contract)
**Then** use the provided key for signing; CLI and Primary never load private key material

**Sigstore trust policy** (applies to keyless signing and verification):
- **OIDC issuer**: configurable allowlist; default includes CI OIDC issuers (`https://token.actions.githubusercontent.com`, GitLab, Azure Pipelines). Reject signatures whose certificate OIDC issuer is not allowlisted.
- **Workload identity**: certificate `subject` (OIDC `sub`) must match a configured identity pattern for the deployment.
- **Audience**: certificate OIDC audience must match configured value (default: `sigstore`).
- **Fulcio roots**: verify against bundled Sigstore public root certificates; roots are versioned and updatable via Gateway configuration.
- **Rekor policy**: keyless attestations must have a verifiable Rekor transparency-log entry in the configured Rekor instance (default: public Sigstore Rekor).
- **Unavailable services**: when Rekor/Fulcio/OIDC are unreachable and no keyed fallback is configured, signing fails with `SIGSTORE_UNAVAILABLE`; verification reports `valid: false` with reason `TRUST_SERVICE_UNAVAILABLE`. Air-gapped deployments use keyed signing only (see design.md).

## Technical Approach

### Architecture (Invariant 11 Compliance)

Signing requires outbound calls (Sigstore Rekor transparency log). Per ADR-020/ADR-029, this belongs in **Gateway**, not engine:

```text
┌─────────┐      ┌─────────┐      ┌─────────────┐
│  CLI    │─────▶│ Primary │─────▶│  Gateway    │
│ --attest│      │ (SARIF) │      │ (sign+store)│
└─────────┘      └─────────┘      └─────────────┘
                                        │
                                        ▼
                                  ┌───────────┐
                                  │ Sigstore  │
                                  │ (Rekor)   │
                                  └───────────┘
```

### Attestation Format

in-toto Statement (SLSA Provenance):
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

- `sigstore-python` for keyless signing
- `cosign` binary for keyed signing (optional)
- Architectural compatibility: Gateway-side signing aligns with invariant 11 (engine never queries out); open operational decisions (offline mode, key storage, retention) are recorded in [design.md](design.md)

## Out of Scope

- SLSA Build provenance (attestation of APME itself — separate concern)
- Custom attestation predicates (v1 ships with scan predicate only)
- Hardware key support (HSM/YubiKey — future enhancement)

## References

- [Industry Gap Analysis](../../research/industry-gap-analysis.md) — identifies artifact signing as high-priority gap
- [ADR-020: Event Sink Abstraction](../../adrs/ADR-020-event-sink-abstraction.md)
- [ADR-029: Gateway Persistence](../../adrs/ADR-029-gateway-persistence.md)
- [in-toto Specification](https://github.com/in-toto/attestation)
- [Sigstore](https://www.sigstore.dev/)
- [SLSA](https://slsa.dev/)
