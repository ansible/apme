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
**When** attestation output is requested (`--attest` flag or API parameter)
**Then** APME produces an in-toto attestation envelope containing:
- SARIF payload as the predicate
- Timestamp (RFC 3339)
- Subject (scanned content SHA-256)
- Cosign signature (keyless via Sigstore or local key)

### AC-2: CLI Integration

**Given** the CLI check command
**When** `apme check --attest` is invoked
**Then** output includes the signed attestation JSON alongside normal output

### AC-3: Gateway REST Endpoint

**Given** a completed scan stored in Gateway
**When** `GET /api/v1/scans/{scan_id}/attestation` is called
**Then** returns the signed attestation for that scan

### AC-4: Verification Command

**Given** a signed attestation file
**When** `apme verify-attestation <file>` is invoked
**Then** validates signature and outputs verification status

### AC-5: Keyless and Keyed Signing

**Given** attestation is requested
**When** no signing key is configured
**Then** use Sigstore keyless signing (OIDC identity)

**Given** attestation is requested
**When** `APME_SIGNING_KEY` environment variable is set
**Then** use the provided key for signing

## Technical Approach

### Architecture (Invariant 11 Compliance)

Signing requires outbound calls (Sigstore Rekor transparency log). Per ADR-020/ADR-029, this belongs in **Gateway**, not engine:

```
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
    "timestamp": "<RFC3339>",
    "toolVersion": "<apme-version>",
    "sarif": { ... },
    "verdict": "pass|fail",
    "violationCount": <n>
  }
}
```

### Dependencies

- `sigstore-python` for keyless signing
- `cosign` binary for keyed signing (optional)
- Architectural compatibility: Verified (signing in Gateway per invariant 11)

## Out of Scope

- SLSA Build provenance (attestation of APME itself — separate concern)
- Custom attestation predicates (v1 ships with scan predicate only)
- Hardware key support (HSM/YubiKey — future enhancement)

## References

- [Industry Gap Analysis](../../.sdlc/research/industry-gap-analysis.md) — identifies artifact signing as high-priority gap
- [ADR-020: Event Sink Abstraction](../../.sdlc/adrs/ADR-020-event-sink-abstraction.md)
- [ADR-029: Gateway Persistence](../../.sdlc/adrs/ADR-029-gateway-persistence.md)
- [in-toto Specification](https://github.com/in-toto/attestation)
- [Sigstore](https://www.sigstore.dev/)
- [SLSA](https://slsa.dev/)
