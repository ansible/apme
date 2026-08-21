# REQ-019: Scan Attestation — API Contract

## REST Endpoints

### POST /api/v1/scans (attestation request)

Scan creation accepts an optional boolean field to request attestation:

```json
{
  "project_id": "<uuid>",
  "path": "<scan-path>",
  "attest": true
}
```

When `attest=true`, Gateway assembles and signs the attestation after the scan completes. Signing is idempotent: repeated requests for the same `scan_id` return the same envelope once signing succeeds.

### GET /api/v1/scans/{scan_id}/attestation

Returns signed attestation for a completed scan.

**Authorization**: Requires an authenticated session (same mechanism as other `/api/v1/scans` endpoints). Caller must have read access to the scan's tenant/project.

**Responses**:

| Status | Condition |
|--------|-----------|
| `200` | Attestation ready (body below) |
| `202` | Scan complete; Gateway signing still in progress. Include `Retry-After` header. Body: `{"status": "pending"}` |
| `404` | Unknown `scan_id` |
| `403` | Authenticated but not authorized for this scan |
| `409` | Scan exists but attestation was not requested (`attest=false` or absent) |

**Response** (200):

Returns the in-toto envelope plus Sigstore verification material required by AC-5. For keyless attestations, Gateway persists and returns a [Sigstore Bundle](https://docs.sigstore.dev/about/bundle/) (or equivalent) containing the Fulcio certificate chain, OIDC claims, and Rekor inclusion proof alongside the signed statement. For keyed attestations, the envelope includes the configured `keyid` and signature only.

```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": "<base64-encoded-statement>",
  "signatures": [
    {
      "keyid": "<key-id>",
      "sig": "<base64-signature>"
    }
  ],
  "verificationMaterial": {
    "x509CertificateChain": ["<base64-cert>"],
    "tlogEntries": ["<rekor-entry>"]
  }
}
```

`verificationMaterial` is present for keyless attestations and omitted for keyed attestations signed with a configured trust-set key. Gateway stores the full bundle at signing time so later retrieval does not depend on live Rekor/Fulcio availability.

### POST /api/v1/attestations/verify

Verify a signed attestation.

**Request**:
```json
{
  "envelope": { ... }
}
```

**Response** (200):
```json
{
  "valid": true,
  "signer": "<identity>",
  "attestedAt": "<RFC3339>",
  "reason": null
}
```

When verification fails, `valid` is `false` and `reason` is a stable machine-readable code:

| `reason` | Meaning |
|----------|---------|
| `INVALID_SIGNATURE` | Signature does not match payload |
| `UNTRUSTED_ISSUER` | OIDC issuer not in allowlist |
| `UNTRUSTED_IDENTITY` | Certificate subject does not match configured pattern |
| `INVALID_AUDIENCE` | OIDC audience mismatch |
| `REKOR_ENTRY_MISSING` | No matching Rekor transparency-log entry |
| `TRUST_SERVICE_UNAVAILABLE` | Fulcio/Rekor/OIDC unreachable |
| `EXPIRED_CERTIFICATE` | Signing certificate outside validity window |

`signer` and `attestedAt` are populated only when `valid` is `true`.

Verification applies the Sigstore trust policy defined in AC-5 of [requirement.md](requirement.md).

## CLI Commands

```bash
apme check --attest [--attest-output <path>] [--signing-key <path>]
apme verify-attestation [--path <source-tree>] <file>
```

### Signing key resolution (Gateway-owned)

Private key material is loaded **only by Gateway**. CLI and Primary never read signing keys.

| Source | Resolved by | Notes |
|--------|-------------|-------|
| `--signing-key <path>` | Gateway | CLI forwards the path as an opaque reference in the scan request metadata |
| `APME_SIGNING_KEY` | Gateway | Filesystem path to PEM key; evaluated on Gateway only |
| (none) | Gateway | Sigstore keyless signing per AC-5 trust policy |

**Precedence** (highest wins): `--signing-key` → `APME_SIGNING_KEY` → keyless Sigstore.

**Gateway failure reporting**:
- Missing or unreadable key path at Gateway: scan completes but attestation status is `failed`; `GET .../attestation` returns `409` with `{"status": "signing_failed", "reason": "KEY_NOT_FOUND"}` (or `KEY_UNREADABLE`)
- Sigstore unavailable with no keyed fallback: `{"status": "signing_failed", "reason": "SIGSTORE_UNAVAILABLE"}`

**CLI failure reporting** (`apme check --attest`):

| Condition | Exit code | Behavior |
|-----------|-----------|----------|
| Scan succeeds; attestation signed | `0` (or scan exit code if violations) | Attestation written per AC-2 output rules |
| Scan succeeds; Gateway signing fails | `1` | No partial attestation file; stderr reports stable reason (`KEY_NOT_FOUND`, `KEY_UNREADABLE`, `SIGSTORE_UNAVAILABLE`) |
| Attestation output path unwritable | `2` | Scan results still emitted per normal rules; no partial attestation file |
| `--json` and attestation unavailable | non-zero | Single JSON document: `{"scan": <results>, "attestation": null, "attestationError": {"reason": "<code>", "message": "<human-readable>"}}` |

When attestation is unavailable, the CLI must not write a truncated or placeholder attestation file to `--attest-output`.

## Proto Extensions

**v1 is REST-only.** Attestation signing, storage, and retrieval are Gateway responsibilities; Primary and FixSession remain unchanged for v1. No new Primary RPC or FixSession fields are required in the initial release.

**Gateway handoff**:
1. Client sets `attest=true` on scan creation (REST) or forwards the flag through the existing Gateway→Primary scan driver metadata path.
2. Primary completes the scan and returns SARIF/results to Gateway as today.
3. Gateway assembles the in-toto statement, signs it, persists the full Sigstore bundle, and exposes completion via `GET /api/v1/scans/{scan_id}/attestation`.

**Completion states** (stored on the scan record):

| State | Meaning |
|-------|---------|
| `not_requested` | Scan created without `attest=true` |
| `pending` | Scan complete; Gateway signing in progress |
| `ready` | Signed attestation persisted and retrievable |
| `failed` | Signing failed; `reason` field set per Gateway failure reporting above |

**Backward compatibility**: Existing FixSession clients that do not send `attest=true` behave unchanged. Attestation is opt-in via REST scan creation or CLI `--attest`; no proto version bump is required for v1.

A future ADR may extend FixSession with typed attestation events if inline delivery becomes necessary.
