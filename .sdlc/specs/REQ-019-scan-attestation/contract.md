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

Gateway generates `scan_id` internally at operation creation. Clients obtain it from `GET /api/v1/projects/{project_id}/operation` (or the operation SSE stream), which includes `scan_id` in the snapshot once the operation exists. In the REST activity API, that same UUID is the `activity_id` (`GET /api/v1/activity/{activity_id}` — `activity_id` ≡ `scans.scan_id`).

The scan record stores initial attestation state `not_requested` or `scan_in_progress` when `options.attest=true`. `pending` is set only after the completed scan is persisted and Gateway claims the signing job (see Signing idempotency below).

When `options.attest=true`, Gateway assembles and signs the attestation after the scan completes. Retrieval is stable: repeated `GET .../attestation` requests for the same `activity_id` return the same persisted envelope once signing succeeds.

### GET /api/v1/activity/{activity_id}/attestation

Returns the signed attestation for a completed scan. `activity_id` is the scan UUID (`scans.scan_id`); this nests under the existing activity API rather than inventing a `/api/v1/scans/` namespace.

**Authorization**: Requires an authenticated session (same mechanism as other activity read endpoints). Caller must have read access to the scan's tenant/project.

**Responses**:

| Status | Condition |
|--------|-----------|
| `200` | Attestation ready (body below) |
| `202` | Scan still running. Include `Retry-After: 5`. Body: `{"status": "scan_in_progress"}` |
| `202` | Scan complete; Gateway signing still in progress. Include `Retry-After: 5`. Body: `{"status": "signing_pending"}` |
| `404` | Unknown `activity_id` |
| `403` | Authenticated but not authorized for this scan |
| `422` | Scan exists but attestation was not requested (`attest=false` or absent). Body: `{"status": "not_requested"}` |
| `409` | Attestation was requested but signing failed. Body: `{"status": "signing_failed", "reason": "<code>"}` (see Gateway failure reporting) |

`GET` is read-only with respect to signing: it never starts a second signing attempt. It only returns the current attestation state and, when `ready`, the immutable persisted record.

**Response** (200):

Returns a [Sigstore Bundle v0.3](https://docs.sigstore.dev/about/bundle/) for keyless attestations, or a DSSE envelope for keyed attestations.

**Keyless (Bundle v0.3)** — `Content-Type: application/vnd.dev.sigstore.bundle.v0.3+json`:

```json
{
  "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
  "verificationMaterial": {
    "certificate": { "rawBytes": "<base64-der>" },
    "tlogEntries": [
      {
        "logIndex": "<string>",
        "logId": { "keyId": "<base64>" },
        "kindVersion": { "kind": "dsse", "version": "0.0.1" },
        "integratedTime": "<string>",
        "inclusionPromise": { "signedEntryTimestamp": "<base64>" }
      }
    ]
  },
  "dsseEnvelope": {
    "payloadType": "application/vnd.in-toto+json",
    "payload": "<base64-encoded-statement>",
    "signatures": [
      {
        "keyid": "<cert-fingerprint-or-empty>",
        "sig": "<base64-signature>"
      }
    ]
  }
}
```

Gateway persists the full Bundle (including the `trustedRoot` metadata version used at signing) so later retrieval does not depend on live Rekor/Fulcio availability.

**Keyed** — DSSE envelope only (`verificationMaterial` / Bundle wrapper omitted):

```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": "<base64-encoded-statement>",
  "signatures": [
    {
      "keyid": "<trust-set-key-id>",
      "sig": "<base64-signature>"
    }
  ]
}
```

**Wire format** (DSSE v1 + Sigstore Bundle binding):

- **Statement bytes**: UTF-8 JSON of the in-toto Statement (see [requirement.md](requirement.md)). Canonicalization is **mandatory and fixed** for both signer and verifier: RFC 8785 (JSON Canonicalization Scheme) over the Statement object, then UTF-8 encode. No pretty-printing; no alternate serializer layouts.
- **DSSE envelope**: `payloadType` is always `application/vnd.in-toto+json`; `payload` is standard Base64 (no URL-safe alphabet) of the Statement bytes; exactly one entry in `signatures`.
- **PAE (Pre-Authentication Encoding)**: Signatures cover DSSE v1 PAE over `(payloadType, payload)` per [DSSE protocol v1.0.0](https://github.com/secure-systems-lab/dsse/blob/v1.0.0/protocol.md): `DSSEv1` + SP + LEN(type) + SP + type + SP + LEN(body) + SP + body, where LEN is decimal byte length and body is the raw Statement bytes (before Base64).
- **Keyless signature algorithm**: ECDSA P-256 SHA-256 over the PAE bytes; Fulcio-issued X.509 certificate binds the signature. `keyid` in the envelope is the certificate fingerprint (SHA-256 of DER, hex-encoded) or empty when implied by Bundle `verificationMaterial`.
- **Keyed signature algorithm**: ECDSA P-256 SHA-256 or Ed25519 over the PAE bytes; `keyid` is the stable public-key fingerprint registered in the Gateway trust set (see [design.md](design.md)).
- **Sigstore Bundle binding**: For keyless attestations the Bundle content is the **`dsseEnvelope`** oneof (not `messageSignature`). Bundle `dsseEnvelope.signatures[0].sig` is the DSSE signature over the PAE bytes; Bundle `verificationMaterial` (certificate + Rekor SET) validates against that same PAE input. Gateway persists the Bundle `trustedRoot` metadata version alongside the Bundle for historical verification within the retention window.

### Signing idempotency and crash recovery

Signing must be **enforceably** idempotent per `scan_id` / `activity_id`, not merely stable after success:

1. **Atomic claim**: After the completed scan is persisted, exactly one worker transitions attestation state from `scan_in_progress` to `pending` via compare-and-swap (or an equivalent unique claim). Losers observe `pending` / `ready` / `failed` and do not call Fulcio/Rekor again.
2. **Immutable record**: Once state is `ready`, the persisted attestation blob (Bundle or keyed DSSE) is immutable. Further signing attempts must no-op and return that record.
3. **Crash recovery**: If a prior attempt completed signing (Fulcio/Rekor succeeded) but failed to persist the Bundle, the claim owner must recover by persisting the in-memory / staging Bundle bytes already obtained for that claim — **not** by submitting a new Rekor entry. If recovery is impossible (claim lost with no staging bytes), mark `failed` with a stable reason (`SIGNING_RECOVERY_FAILED`) and do not silently produce a second winning envelope for the same `scan_id`.
4. **GET never signs**: Retrieval endpoints only read state; they do not claim or re-enter the signing path.

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

`envelope` is either a Sigstore Bundle v0.3 (keyless) or a DSSE envelope (keyed). `sourceManifest` is optional. When omitted, Gateway verifies signature and trust policy only (same semantics as CLI without `--path`). When present, Gateway recomputes the Subject Digest from the manifest using the same canonicalization rules as AC-1 and compares it to `subject[].digest.sha256` in the attestation.

**Response** (200):
```json
{
  "valid": true,
  "signer": "<identity>",
  "attestedAt": "<RFC3339>",
  "digestVerified": null,
  "reason": null
}
```

| `digestVerified` | Meaning |
|------------------|---------|
| `true` | `sourceManifest` supplied and recomputed Subject Digest matches |
| `false` | `sourceManifest` supplied and digest does not match (`valid` is `false`, `reason` is `SUBJECT_DIGEST_MISMATCH`) |
| `null` | `sourceManifest` omitted; digest check skipped |

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

`signer` and `attestedAt` are populated only when `valid` is `true`. `digestVerified` is populated only in the 200 verification body (`true` / `false` / `null` as above).

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
| `--signing-key <id-or-path>` | Gateway | CLI forwards an opaque key reference in scan request metadata. Gateway resolves **only** (a) a configured allowlisted key identifier (`keyid`) or (b) a path under its configured signing-key directory (in-container path, not a client host path). Reject path traversal, symlinks escaping the directory, and references outside the allowlist. |
| `APME_SIGNING_KEY` | Gateway | Default signing key path or `keyid` on Gateway only; same resolution rules as `--signing-key` |
| (none) | Gateway | Sigstore keyless signing using Gateway workload identity (see [design.md](design.md)) |

**Precedence** (highest wins): `--signing-key` → `APME_SIGNING_KEY` → keyless Sigstore.

**CLI sequence** (`apme check --attest`):

1. Start check with attestation requested (flag forwarded to Gateway via existing scan-driver metadata).
2. Wait for scan persistence, then poll attestation state until `ready` or `failed` (same semantics as `GET .../attestation` `200` / `409`).
3. Emit scan results and attestation per AC-2 output rules.

**Gateway failure reporting**:
- Missing or unreadable key path at Gateway: scan completes but attestation status is `failed`; `GET .../attestation` returns `409` with `{"status": "signing_failed", "reason": "KEY_NOT_FOUND"}` (or `KEY_UNREADABLE`)
- Sigstore unavailable with no keyed fallback: `{"status": "signing_failed", "reason": "SIGSTORE_UNAVAILABLE"}`
- Claim/recovery failure after external signing succeeded without recoverable staging bytes: `{"status": "signing_failed", "reason": "SIGNING_RECOVERY_FAILED"}`

**CLI failure reporting** (`apme check --attest`):

| Condition | Exit code | Behavior |
|-----------|-----------|----------|
| Scan succeeds; attestation signed | `0` (or scan exit code if violations) | Attestation written per AC-2 output rules |
| Scan succeeds; Gateway signing fails | `1` | No partial attestation file; stderr reports stable reason (`KEY_NOT_FOUND`, `KEY_UNREADABLE`, `SIGSTORE_UNAVAILABLE`, `SIGNING_RECOVERY_FAILED`) |
| Attestation output path unwritable | `2` | Scan results still emitted per normal rules; no partial attestation file |
| `--json` and attestation unavailable | non-zero | Single JSON document: `{"scan": <results>, "attestation": null, "attestationError": {"reason": "<code>", "message": "<human-readable>"}}` |

When attestation is unavailable, the CLI must not write a truncated or placeholder attestation file to `--attest-output`.

## Proto Extensions

**v1 is REST-only.** Attestation signing, storage, and retrieval are Gateway responsibilities; Primary and FixSession remain unchanged for v1. No new Primary RPC or FixSession fields are required in the initial release.

**Subject digest handoff (no proto change)**: Gateway — not Primary — computes the Subject Digest. After `ReportFixCompleted`, Gateway uses (a) the project working tree it already cloned for the operation and (b) the set of source file paths present in the persisted `content_graph_json` (same graph Primary already emits today). Gateway hashes those files from the clone using AC-1 canonicalization rules. Primary does **not** emit per-file SHA-256 digests over the wire. If a path in the graph is missing from the clone at signing time, signing fails with `SUBJECT_TREE_INCOMPLETE` (attestation state `failed`).

**Gateway handoff**:
1. Client sets `options.attest=true` on `POST /api/v1/projects/{project_id}/operation` (REST) or forwards the flag through the existing Gateway→Primary scan driver metadata path (CLI `--attest`).
2. Primary completes the scan and returns results to Gateway as today. Gateway persists the scan record including:
   - `scanCompletedAt` (UTC RFC 3339, from Primary completion timestamp / reporting event)
   - `content_graph_json` (existing field; supplies the file-path set for digest computation)
   - `toolVersion`, `verdict`, `violationCount`, and SARIF/results already persisted for the scan
3. The exclusive claim owner transitions to `pending`, computes the Subject Digest from clone + graph paths, assembles the in-toto Statement (RFC 8785), signs the DSSE envelope, persists the full Sigstore Bundle (keyless) or keyed DSSE plus `trustedRoot` metadata version, sets state `ready`, and exposes the record via `GET /api/v1/activity/{activity_id}/attestation`.

**Completion states** (stored on the scan record):

| State | Meaning |
|-------|---------|
| `not_requested` | Scan created without `attest=true` (`GET .../attestation` returns `422`) |
| `scan_in_progress` | Attestation requested; scan not yet complete (`GET .../attestation` returns `202` with `{"status": "scan_in_progress"}`) |
| `pending` | Scan complete and persisted; exclusive signing claim held (`GET .../attestation` returns `202` with `{"status": "signing_pending"}`) |
| `ready` | Signed attestation persisted and retrievable (immutable) |
| `failed` | Signing failed; `reason` field set per Gateway failure reporting above (`GET .../attestation` returns `409`) |

**Backward compatibility**: Existing FixSession clients that do not send `attest=true` behave unchanged. Attestation is opt-in via REST operation creation or CLI `--attest`; no proto version bump is required for v1.

A future ADR may extend FixSession with typed attestation events if inline delivery becomes necessary.
