# REQ-019: Scan Attestation — API Contract

## REST Endpoints

### POST /api/v1/projects/{project_id}/operation (attestation request)

Scans are created through the existing project operation endpoint (ADR-052), not a separate `/api/v1/scans` route. To request attestation, set `options.attest=true` in the operation body. **v1: `action` must be `check`.** If `options.attest=true` and `action` is `remediate` (or any non-check action), Gateway returns `400` with reason `ATTEST_CHECK_ONLY`.

```json
{
  "action": "check",
  "options": {
    "attest": true,
    "signing_key": "<keyid-or-path>"
  }
}
```

`options.signing_key` is optional; when present it uses the unified `<keyid-or-path>` grammar (see Signing key resolution). Host paths from the client are rejected.
**Authorization**: Same authentication and project authorization as other `/api/v1/projects/{project_id}/operation` endpoints. Caller must have permission to start a check on the project.

**Response** (201):

```json
{
  "operation_id": "<uuid>"
}
```

Gateway generates `scan_id` internally at operation creation. Clients obtain it from `GET /api/v1/projects/{project_id}/operation` (or the operation SSE stream), which includes `scan_id` in the snapshot once the operation exists. In the REST activity API, that same UUID is the `activity_id` (`GET /api/v1/activity/{activity_id}` — `activity_id` ≡ `scans.scan_id`).

When `options.attest=true`, the scan record's attestation state is initialized to **`scan_in_progress`**. When `options.attest` is absent or `false`, attestation state is **`not_requested`**. `not_requested` must never be stored for an attestation-requested scan (otherwise `GET .../attestation` would permanently return `422`). `pending` is set only after the completed scan is persisted and Gateway claims the signing job (see Signing idempotency below).

**Pollable stub at operate-time**: Today `Scan` rows are created at `ReportFixCompleted`. For attestation, Gateway **must** insert a durable stub `scans` row (minimal columns + `attestation_state=scan_in_progress`) when the operation is accepted and `scan_id` is allocated — **before** Primary starts — so `GET /api/v1/activity/{activity_id}` and `GET .../attestation` return `202` / `scan_in_progress` instead of `404` while the scan runs. `ReportFixCompleted` updates the stub in place.

When `options.attest=true`, Gateway stages an immutable source snapshot for the `scan_id` before Primary begins, disables format-rewrite for that check session, assembles and signs the attestation after the scan completes, and defers snapshot cleanup until attestation reaches `ready` or `failed`. Retrieval is stable: repeated `GET .../attestation` requests for the same `activity_id` return the same persisted envelope once signing succeeds.

### POST /api/v1/operations/local-check (CLI / local tree)

Creates a Gateway-orchestrated check with optional attestation for a local source tree (used by `apme check --attest <dir>`).

**Authorization**: Authenticated caller; co-located daemon may additionally restrict to local clients.

**Request** (JSON, co-located path mode):

```json
{
  "path": "/allowed/root/my-project",
  "options": {
    "attest": true,
    "signing_key": "<keyid-or-path>"
  }
}
```

**Request** (multipart, remote Gateway): field `archive` = tar of the project tree; field `options` = JSON object with `attest` / `signing_key` as above.

**Rules**:
- Same `attest` state machine and check-only rule as project operate.
- Co-located `path` must resolve under a configured allowlist of roots; Gateway **copies** into an immutable snapshot (never hashes the live client directory).
- Remote mode requires the uploaded archive; path mode is rejected.
- **Response** (201): `{"operation_id": "<uuid>", "scan_id": "<uuid>"}` — `scan_id` is immediately pollable via `GET /api/v1/activity/{scan_id}/attestation`.
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

Clients **must** branch on the JSON `status` field when handling `202` responses; both pending states share HTTP `202` by design.

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

**Keyed** — DSSE envelope only (`verificationMaterial` / Bundle wrapper omitted). `Content-Type: application/vnd.in-toto.dsse+json` (or `application/json` when clients cannot negotiate; verifiers detect keyed vs keyless by presence/absence of Bundle `mediaType` / `verificationMaterial`):

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
- **Keyless signature algorithm**: ECDSA P-256 with SHA-256 over the PAE bytes; signature bytes are **ASN.1 DER** encoded (`SEQUENCE { r INTEGER, s INTEGER }`) then standard Base64 in `sig`. Fulcio-issued X.509 certificate binds the signature. `keyid` in the envelope is the certificate fingerprint — SHA-256 of the certificate DER bytes, **lowercase hex** — or empty when implied by Bundle `verificationMaterial`.
- **Keyed signature algorithms**:
  - **ECDSA P-256 SHA-256**: signature bytes are ASN.1 DER (same as keyless), then Base64 in `sig`.
  - **Ed25519**: signature bytes are the raw 64-byte PureEd25519 signature (R ‖ S), then Base64 in `sig`. No ASN.1 wrapping.
- **Keyed `keyid` fingerprint**: SHA-256 over the public key's SubjectPublicKeyInfo (SPKI) DER bytes, encoded as **lowercase hex** (64 hex chars). That value is the stable trust-set key identifier.
- **Cross-language test vector** (implementation TASK must ship golden vectors covering): RFC 8785 Statement bytes, DSSE PAE bytes, ECDSA DER `sig` Base64, Ed25519 raw `sig` Base64, and keyed `keyid` derivation from a fixed SPKI. Vectors live under `tests/fixtures/attestation/` when implementation lands.
- **Sigstore Bundle binding**: For keyless attestations the Bundle content is the **`dsseEnvelope`** oneof (not `messageSignature`). Bundle `dsseEnvelope.signatures[0].sig` is the DSSE signature over the PAE bytes; Bundle `verificationMaterial` (certificate + Rekor SET) validates against that same PAE input. Gateway persists the Bundle `trustedRoot` metadata version alongside the Bundle for historical verification within the retention window.

### Signing idempotency and crash recovery

Signing must be **enforceably** idempotent per `scan_id` / `activity_id`, not merely stable after success:

1. **Atomic claim**: After the completed scan is persisted, exactly one worker transitions attestation state from `scan_in_progress` to `pending` via compare-and-swap (or an equivalent unique claim). Losers observe `pending` / `ready` / `failed` and do not call Fulcio/Rekor again.
2. **Digest then Statement**: Claim owner confines owned-scope graph paths, hashes the immutable snapshot, then assembles the in-toto Statement (RFC 8785) including `subject[].digest` and `contentEpoch: "submitted_snapshot"`.
3. **Durable staging before external writes**: Before any Fulcio/Rekor network call, durably stage claim metadata and the assembled Statement/PAE bytes for that `scan_id`. After a successful external signing response, Bundle/envelope bytes are staged durably before marking `ready`. Recovery only flushes staged bytes — it never opens a second Rekor entry.
4. **Immutable record**: Once state is `ready`, the persisted attestation blob (Bundle or keyed DSSE) is immutable. Further signing attempts must no-op and return that record.
5. **Crash recovery**: If a prior attempt completed signing (Fulcio/Rekor succeeded) but failed to persist the Bundle, the claim owner must recover by persisting the staged Bundle bytes already obtained for that claim — **not** by submitting a new Rekor entry. If recovery is impossible (claim lost with no staging bytes), mark `failed` with a stable reason (`SIGNING_RECOVERY_FAILED`) and do not silently produce a second winning envelope for the same `scan_id`.
6. **Snapshot lifetime**: Retain the immutable snapshot until attestation is `ready` or `failed`, then delete. Do not delete after hashing alone.
7. **GET never signs**: Retrieval endpoints only read state; they do not claim or re-enter the signing path.

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

`envelope` is either a Sigstore Bundle v0.3 (keyless) or a DSSE envelope (keyed). `sourceManifest` is optional. When omitted, Gateway verifies signature and trust policy only (same semantics as CLI without `--path`). When present, Gateway recomputes the Subject Digest from the manifest using the same path-confinement and canonicalization rules as AC-1 and compares it to `subject[].digest.sha256` in the attestation. Manifest paths that fail confinement yield `valid: false` with reason `SUBJECT_PATH_INVALID`.

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
| `TRUST_SERVICE_UNAVAILABLE` | Required verification inputs missing (no persisted bundle, no local trust root). Under `APME_SIGSTORE_OFFLINE=true`, also returned immediately for incomplete envelopes without live lookup. When online, returned if live lookup is required but unavailable |
| `EXPIRED_CERTIFICATE` | **Keyless**: signing certificate outside validity window at the authenticated Rekor `integratedTime` or bundled SET timestamp (not the verifier's current clock). **Keyed**: signature verification uses the configured trust-set public keys only; certificate expiry checks do not apply (see [design.md](design.md)) |
| `SUBJECT_DIGEST_MISMATCH` | `sourceManifest` supplied but recomputed Subject Digest does not match attestation |
| `SUBJECT_PATH_INVALID` | Manifest or graph path failed confinement rules |
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
apme check --attest [--attest-output <path>] [--signing-key <keyid-or-path>]
apme verify-attestation [--path <source-tree>] <file>
```

### Signing key resolution (Gateway-owned)

Private key material is loaded **only by Gateway**. CLI and Primary never read signing keys.

**Grammar** (identical for every entry point): `<keyid-or-path>` is either (a) an allowlisted trust-set key identifier, or (b) an absolute or relative path that Gateway resolves **only** under its configured in-container signing-key directory. Client host paths are rejected.

| Source | Resolved by | Notes |
|--------|-------------|-------|
| `--signing-key <keyid-or-path>` | Gateway | CLI forwards as `options.signing_key` on the Gateway operate / local-check request. Gateway resolves per the grammar above. Reject path traversal, symlinks escaping the directory, and references outside the allowlist. |
| `APME_SIGNING_KEY=<keyid-or-path>` | Gateway | Default signing key on Gateway only; same grammar and resolution rules as `--signing-key` |
| (none) | Gateway | Sigstore keyless signing using Gateway workload identity (see [design.md](design.md)) |

**Precedence** (highest wins): `--signing-key` → `APME_SIGNING_KEY` → keyless Sigstore.

**CLI sequence** (`apme check --attest`):

1. Call Gateway: registered project → `POST /api/v1/projects/{project_id}/operation`; local tree → `POST /api/v1/operations/local-check`. Body includes `options.attest=true` and optional `options.signing_key`. Direct Primary-only FixSession cannot attest in v1.
2. Read `scan_id` from the 201 response (local-check) or operation snapshot (project operate). Stub scan row already exists with `attestation_state=scan_in_progress`.
3. Poll `GET /api/v1/activity/{activity_id}/attestation` until `ready` or `failed` (`200` / `409`). Branch on JSON `status` for `202` bodies.
4. Emit scan results and attestation per AC-2 output rules.

**Gateway failure reporting** (`GET .../attestation` → `409`):

| `reason` | Meaning |
|----------|---------|
| `KEY_NOT_FOUND` | Missing or unknown signing key reference at Gateway |
| `KEY_UNREADABLE` | Signing key path exists but cannot be read/parsed |
| `SIGSTORE_UNAVAILABLE` | Fulcio/Rekor/OIDC unreachable and no keyed fallback |
| `SIGNING_RECOVERY_FAILED` | External signing succeeded but staged bytes were lost |
| `SUBJECT_TREE_INCOMPLETE` | Owned-scope path set empty after extraction, or a confined owned path missing from the immutable snapshot |
| `SUBJECT_PATH_INVALID` | Graph path failed confinement (absolute, `..`, NUL, duplicate, symlink escape) or referenced/out-of-snapshot path included |
| `STATEMENT_INPUTS_MISSING` | Required Statement inputs absent after scan persistence (see persistence map) |
| `ATTEST_CHECK_ONLY` | Returned on operate/local-check `400` when `attest=true` with non-check action (not a `409` signing failure) |

**CLI failure reporting** (`apme check --attest`):

| Condition | Exit code | Behavior |
|-----------|-----------|----------|
| Scan succeeds; attestation signed | `0` (or scan exit code if violations) | Attestation written per AC-2 output rules |
| Scan succeeds; Gateway signing fails | `1` | No partial attestation file; stderr reports stable reason (`KEY_NOT_FOUND`, `KEY_UNREADABLE`, `SIGSTORE_UNAVAILABLE`, `SIGNING_RECOVERY_FAILED`, `SUBJECT_TREE_INCOMPLETE`, `SUBJECT_PATH_INVALID`, `STATEMENT_INPUTS_MISSING`) |
| Attestation output path unwritable | `2` | Scan results still emitted per normal rules; no partial attestation file |
| `--json` and attestation unavailable | non-zero | Single JSON document: `{"scan": <results>, "attestation": null, "attestationError": {"reason": "<code>", "message": "<human-readable>"}}` |

When attestation is unavailable, the CLI must not write a truncated or placeholder attestation file to `--attest-output`.

## Proto Extensions

**v1 is REST-only.** Attestation signing, storage, and retrieval are Gateway responsibilities; Primary and FixSession remain unchanged for v1. No new Primary RPC or FixSession fields are required in the initial release.

### Statement input persistence map

`FixCompletedEvent` today carries `content_graph_json`, violations, summary counts, and diagnostics — **not** SARIF, `toolVersion`, `verdict`, or `scanCompletedAt`. Implementers must not invent those as existing event fields. Normative sources:

| Statement / control field | Authoritative source at signing time | Notes / migration |
|---------------------------|--------------------------------------|-------------------|
| `scanId` | `scans.scan_id` | Stub row created at operate/local-check accept |
| `attestation_state` | New column `scans.attestation_state` (`not_requested` \| `scan_in_progress` \| `pending` \| `ready` \| `failed`) | Migration required |
| `attestation_reason` | New nullable column `scans.attestation_reason` | Set on `failed` |
| `attestation_blob` | New nullable column or side table `scan_attestations` holding Bundle/DSSE JSON | Immutable once `ready` |
| `attestation_trusted_root_version` | New nullable text column | Keyless only |
| Durable Statement/PAE staging | Side table or filesystem WAL keyed by `scan_id` (`attestation_staging`) | Written before Fulcio/Rekor; purged after `ready`/`failed` |
| Snapshot path | Gateway runtime/metadata (not required in Statement); retain until terminal | Implementation detail |
| `scanCompletedAt` | New nullable column `scans.completed_at` (UTC RFC 3339) set when Gateway commits `ReportFixCompleted` persistence | Migration required; do not reuse `created_at` (operate/stub time) |
| `attestedAt` | Generated at envelope assembly | Wall clock UTC |
| `toolVersion` | New nullable column `scans.tool_version` populated from Gateway-known APME package/engine version at completion | Migration required; empty → `STATEMENT_INPUTS_MISSING` |
| `contentEpoch` | Constant `"submitted_snapshot"` in Statement | Not a DB column |
| `verdict` | Derived: `"pass"` iff `scans.total_violations == 0`, else `"fail"` | Not stored separately |
| `violationCount` | `scans.total_violations` | Existing; set from `FixCompletedEvent.summary` |
| `sarif` | Assembled by Gateway from persisted **remaining** violation rows only (exclude fixed) — same selection as CLI SARIF export | Do not require a SARIF blob on `FixCompletedEvent` |
| `subject[].name` | Project display name when `project_id` set; else basename of snapshot root | Deterministic |
| `subject[].digest.sha256` | Computed from immutable snapshot + confined **owned** graph paths | See below |
| Path set | `scan_graphs.graph_json` nodes with `scope == "owned"` and non-empty `file_path` (`ContentGraph.to_dict()`) | Empty owned set or empty `content_graph_json` when `attest=true` → `SUBJECT_TREE_INCOMPLETE` (single code; do not use `STATEMENT_INPUTS_MISSING` for empty graph) |

Empty `content_graph_json` on `ReportFixCompleted` must not be treated as a successful empty path set for attestation-requested scans.

### Subject digest handoff (no proto change)

Gateway — not Primary — computes the Subject Digest:

1. Retain the immutable snapshot for `scan_id` until attestation is terminal.
2. After `ReportFixCompleted` persists `scan_graphs.graph_json`, extract `file_path` values from `ContentGraph.to_dict()` nodes where `scope == "owned"` and `file_path` is non-empty.
3. If the pre-unique multiset contains duplicate normalized paths → `SUBJECT_PATH_INVALID`. Apply path confinement (relative POSIX, NFC, no `..`/NUL; symlink targets stay under snapshot root). Failures → `SUBJECT_PATH_INVALID`.
4. Hash each surviving path's raw bytes from the snapshot; missing files → `SUBJECT_TREE_INCOMPLETE`.
5. Canonicalize per AC-1 and set `subject[].digest.sha256` (lowercase hex). Statement includes `contentEpoch: "submitted_snapshot"`.

Primary does **not** emit per-file SHA-256 digests over the wire.

### Gateway handoff

1. Client sets `options.attest=true` on `POST /api/v1/projects/{project_id}/operation` or `POST /api/v1/operations/local-check` (CLI `--attest`). Gateway rejects non-check actions (`ATTEST_CHECK_ONLY`), initializes stub `scans` row with `attestation_state=scan_in_progress`, and stages the immutable snapshot before Primary reads sources. Format-rewrite is disabled for the attest check session.
2. Primary completes the scan and emits `ReportFixCompleted` as today. Gateway updates the stub scan row using the persistence map above (including new columns via migration).
3. The exclusive claim owner transitions to `pending`, confines/hashes owned paths, assembles the Statement (RFC 8785), durably stages Statement/PAE bytes, signs the DSSE envelope, persists the Bundle/DSSE plus `trustedRoot` metadata version, sets state `ready`, deletes the snapshot, and exposes the record via `GET /api/v1/activity/{activity_id}/attestation`.
**Completion states** (stored on the scan record):

| State | Meaning |
|-------|---------|
| `not_requested` | Scan created without `attest=true` (`GET .../attestation` returns `422`) |
| `scan_in_progress` | Attestation requested; scan not yet complete (`GET .../attestation` returns `202` with `{"status": "scan_in_progress"}`) |
| `pending` | Scan complete and persisted; exclusive signing claim held (`GET .../attestation` returns `202` with `{"status": "signing_pending"}`) |
| `ready` | Signed attestation persisted and retrievable (immutable) |
| `failed` | Signing failed; `reason` field set per Gateway failure reporting above (`GET .../attestation` returns `409`) |

**Backward compatibility**: Existing FixSession clients that do not request attestation behave unchanged. Attestation is opt-in via REST operation creation or CLI `--attest` (Gateway-mediated); no proto version bump is required for v1.

A future ADR may extend FixSession with typed attestation events if inline delivery becomes necessary.
