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

**Authorization**: Requires authenticated session (same mechanism as other `/api/v1/scans` endpoints). Caller must have read access to the scan's tenant/project.

**Responses**:

| Status | Condition |
|--------|-----------|
| `200` | Attestation ready (body below) |
| `202` | Scan complete; Gateway signing still in progress. Include `Retry-After` header. Body: `{"status": "pending"}` |
| `404` | Unknown `scan_id` |
| `403` | Authenticated but not authorized for this scan |
| `409` | Scan exists but attestation was not requested (`attest=false` or absent) |

**Response** (200):
```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": "<base64-encoded-statement>",
  "signatures": [
    {
      "keyid": "<key-id>",
      "sig": "<base64-signature>"
    }
  ]
}
```

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
apme verify-attestation <file>
```

### Signing key resolution (Gateway-owned)

Private key material is loaded **only by Gateway**. CLI and Primary never read signing keys.

| Source | Resolved by | Notes |
|--------|-------------|-------|
| `--signing-key <path>` | Gateway | CLI forwards the path as an opaque reference in the scan request metadata |
| `APME_SIGNING_KEY` | Gateway | Filesystem path to PEM key; evaluated on Gateway only |
| (none) | Gateway | Sigstore keyless signing per AC-5 trust policy |

**Precedence** (highest wins): `--signing-key` → `APME_SIGNING_KEY` → keyless Sigstore.

**Failure reporting**:
- Missing or unreadable key path at Gateway: scan completes but attestation status is `failed`; `GET .../attestation` returns `409` with `{"status": "signing_failed", "reason": "KEY_NOT_FOUND"}` (or `KEY_UNREADABLE`)
- Sigstore unavailable with no keyed fallback: `{"status": "signing_failed", "reason": "SIGSTORE_UNAVAILABLE"}`

## Proto Extensions

TBD — may extend FixSession response or add separate RPC.
