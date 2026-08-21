# REQ-019: Scan Attestation — API Contract

## REST Endpoints

### POST /api/v1/projects/{project_id}/operation (attestation request)

Scans are created through the existing project operation endpoint (ADR-052), not a separate `/api/v1/scans` route. To request attestation, set `options.attest=true` in the operation body:

```json
{
  "action": "check",
  "options": {
    "attest": true
  }
}
```

**Authorization**: Same authentication and project authorization as other `/api/v1/projects/{project_id}/operation` endpoints. Caller must have permission to start a check on the project.

**Response** (201):

```json
{
  "operation_id": "<uuid>"
}
```

Gateway generates `scan_id` internally at operation creation. Clients obtain it from `GET /api/v1/projects/{project_id}/operation` (or the operation SSE stream), which includes `scan_id` in the snapshot once the operation exists. The scan record stores initial attestation state `not_requested` or `scan_in_progress` when `options.attest=true`. `pending` is set only after the completed scan is persisted and Gateway begins signing.

When `options.attest=true`, Gateway assembles and signs the attestation after the scan completes. Signing is idempotent: repeated `GET .../attestation` requests for the same `scan_id` return the same envelope once signing succeeds.

### GET /api/v1/scans/{scan_id}/attestation

Returns signed attestation for a completed scan.

**Authorization**: Requires an authenticated session (same mechanism as other scan read endpoints). Caller must have read access to the scan's tenant/project.

**Responses**:

| Status | Condition |
|--------|-----------|
| `200` | Attestation ready (body below) |
| `202` | Scan still running. Include `Retry-After: 5`. Body: `{"status": "scan_in_progress"}` |
| `202` | Scan complete; Gateway signing still in progress. Include `Retry-After: 5`. Body: `{"status": "signing_pending"}` |
| `404` | Unknown `scan_id` |
| `403` | Authenticated but not authorized for this scan |
| `409` | Scan exists but attestation was not requested (`attest=false` or absent), or signing failed (see Gateway failure reporting) |

**Response** (200):

Returns the in-toto DSSE envelope plus Sigstore verification material required by AC-5. For keyless attestations, Gateway persists and returns a [Sigstore Bundle v0.3](https://docs.sigstore.dev/about/bundle/) (`mediaType`: `application/vnd.dev.sigstore.bundle.v0.3+json`) containing the DSSE envelope, Fulcio certificate chain, and Rekor Signed Entry Timestamp (SET). For keyed attestations, the envelope includes the configured `keyid` and signature only; `verificationMaterial` is omitted.

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

**Wire format** (DSSE v1 + Sigstore Bundle binding):

- **Statement bytes**: UTF-8 JSON of the in-toto Statement (see [requirement.md](requirement.md)); no pretty-printing; keys sorted lexicographically where the serializer supports canonical ordering.
- **DSSE envelope**: `payloadType` is always `application/vnd.in-toto+json`; `payload` is standard Base64 (no URL-safe alphabet) of the Statement bytes; exactly one entry in `signatures`.
- **PAE (Pre-Authentication Encoding)**: Signatures cover DSSE v1 PAE over `(payloadType, payload)` per [DSSE protocol v1.0.0](https://github.com/secure-systems-lab/dsse/blob/v1.0.0/protocol.md): `DSSEv1` + SP + LEN(type) + SP + type + SP + LEN(body) + SP + body, where LEN is decimal byte length and body is the raw Statement bytes (before Base64).
- **Keyless signature algorithm**: ECDSA P-256 SHA-256 over the PAE bytes; Fulcio-issued X.509 certificate binds the signature. `keyid` in the envelope is the certificate fingerprint (SHA-256 of DER, hex-encoded) or empty when implied by `verificationMaterial`.
- **Keyed signature algorithm**: ECDSA P-256 SHA-256 or Ed25519 over the PAE bytes; `keyid` is the stable public-key fingerprint registered in the Gateway trust set (see [design.md](design.md)).
- **Sigstore Bundle binding**: Bundle `messageSignature` matches the DSSE `signatures[0].sig`; bundle `verificationMaterial` (certificate chain + Rekor SET) validates against the same PAE input. Gateway persists the bundle `trustedRoot` metadata version alongside `verificationMaterial` for historical verification within the retention window.

### POST /api/v1/attestations/verify

Verify a signed attestation.

**Request**:
```json
{
  "envelope": { ... },
  "sourceManifest": {
    "files": [
      {"path": "playbooks/site.yml", "sha256": "<hex>"},
      {"path": "roles/common/tasks/main.yml", "sha256": "<hex>"}
    ]
  }
}
```

`sourceManifest` is optional. When omitted, Gateway verifies signature and trust policy only (same semantics as CLI without `--path`). When present, Gateway recomputes the Subject Digest from the manifest using the same canonicalization rules as AC-1 and compares it to `subject[].digest.sha256` in the attestation.

**Response** (200):
```json
{
  "valid": true,
  "signer": "<identity>",
  "attestedAt": "<RFC3339>",
  "digestVerified": false,
  "reason": null
}
```

`digestVerified` is `true` when `sourceManifest` was supplied and the recomputed Subject Digest matches the attestation; `false` when `sourceManifest` was omitted or the digest does not match. When `sourceManifest` is supplied and the digest mismatches, `valid` is `false` and `reason` is `SUBJECT_DIGEST_MISMATCH`.

When verification fails, `valid` is `false` and `reason` is a stable machine-readable code:

| `reason` | Meaning |
|----------|---------|
| `INVALID_SIGNATURE` | Signature does not match payload |
| `UNTRUSTED_ISSUER` | OIDC issuer not in allowlist |
| `UNTRUSTED_IDENTITY` | Certificate subject does not match configured pattern |
| `INVALID_AUDIENCE` | OIDC audience mismatch |
| `REKOR_ENTRY_MISSING` | No matching Rekor transparency-log entry |
| `TRUST_SERVICE_UNAVAILABLE` | Required verification inputs missing (no persisted bundle, no local trust root, or live lookup required but unavailable) |
| `EXPIRED_CERTIFICATE` | **Keyless**: signing certificate outside validity window at the authenticated Rekor `integratedTime` or bundled SET timestamp (not the verifier's current clock). **Keyed**: signature verification uses the configured trust-set public keys only; certificate expiry checks do not apply (see [design.md](design.md)) |
| `SUBJECT_DIGEST_MISMATCH` | `sourceManifest` supplied but recomputed Subject Digest does not match attestation |
| `UNTRUSTED_KEYID` | Keyed attestation: `keyid` not in configured trust set |
| `MALFORMED_ENVELOPE` | Envelope structure, Base64, or JSON invalid |

**HTTP errors** (malformed request — distinct from verification failure):

| Status | Condition |
|--------|-----------|
| `400` | Missing `envelope`, invalid JSON, or envelope fails structural validation before cryptographic checks |
| `401` | Unauthenticated |
| `403` | Authenticated but not authorized to invoke verification |

Malformed requests return `400` with a stable machine-readable reason and human-readable detail:

```json
{
  "reason": "MALFORMED_ENVELOPE",
  "detail": "<human-readable>"
}
```

They do not return the 200 verification body above.

`signer` and `attestedAt` are populated only when `valid` is `true`. `digestVerified` is populated only in the 200 verification body.

Verification applies the Sigstore trust policy defined in AC-5 of [requirement.md](requirement.md). **Keyed attestations**: signature is valid only when `keyid` resolves to a public key in the Gateway trust set; unknown `keyid` returns `valid: false` with reason `UNTRUSTED_KEYID`.

## CLI Commands

```bash
apme check --attest [--attest-output <path>] [--signing-key <path>]
apme verify-attestation [--path <source-tree>] <file>
```

### Signing key resolution (Gateway-owned)

Private key material is loaded **only by Gateway**. CLI and Primary never read signing keys.

| Source | Resolved by | Notes |
|--------|-------------|-------|
| `--signing-key <id-or-path>` | Gateway | CLI forwards an opaque key reference in scan request metadata. Gateway resolves **only** (a) a configured allowlisted key identifier (`keyid`) or (b) a path under its configured signing-key directory. Reject path traversal, symlinks escaping the directory, and references outside the allowlist. |
| `APME_SIGNING_KEY` | Gateway | Default signing key path or `keyid` on Gateway only; same resolution rules as `--signing-key` |
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
1. Client sets `options.attest=true` on `POST /api/v1/projects/{project_id}/operation` (REST) or forwards the flag through the existing Gateway→Primary scan driver metadata path (CLI `--attest`).
2. Primary completes the scan and returns SARIF/results to Gateway as today, plus attestation inputs persisted on the scan record:
   - `scanCompletedAt` (UTC RFC 3339, from Primary completion timestamp)
   - Subject digest manifest inputs: relative paths and per-file SHA-256 of all content-graph source files (same rules as AC-1 Subject Digest)
   - `toolVersion`, `verdict`, `violationCount`
3. Gateway computes the Subject Digest from the manifest, assembles the in-toto Statement, signs the DSSE envelope, persists the full Sigstore bundle (including `trustedRoot` metadata version), and exposes completion via `GET /api/v1/scans/{scan_id}/attestation`.

**Completion states** (stored on the scan record):

| State | Meaning |
|-------|---------|
| `not_requested` | Scan created without `attest=true` |
| `scan_in_progress` | Attestation requested; scan not yet complete (`GET .../attestation` returns `202` with `{"status": "scan_in_progress"}`) |
| `pending` | Scan complete and persisted; Gateway signing in progress (`GET .../attestation` returns `202` with `{"status": "signing_pending"}`) |
| `ready` | Signed attestation persisted and retrievable |
| `failed` | Signing failed; `reason` field set per Gateway failure reporting above |

**Backward compatibility**: Existing FixSession clients that do not send `attest=true` behave unchanged. Attestation is opt-in via REST scan creation or CLI `--attest`; no proto version bump is required for v1.

A future ADR may extend FixSession with typed attestation events if inline delivery becomes necessary.
