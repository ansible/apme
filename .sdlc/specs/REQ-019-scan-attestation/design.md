# REQ-019: Scan Attestation — Design

## Status

Draft — provisional decisions recorded below; final ADR may follow during implementation.

## Design Decisions

### Keyless signer identity (Gateway workload)

Keyless Sigstore signing is performed **only by Gateway**. The OIDC identity presented to Fulcio is the **Gateway workload identity**, not an ambient identity from the caller's CI job unless that job identity is explicitly configured as the Gateway signing identity.

**Default (recommended for Helm / OpenShift)**:
- Gateway obtains a short-lived OIDC token from its platform workload identity (Kubernetes service account token projected for Fulcio, cloud workload identity, or equivalent).
- Trust policy issuer allowlist + issuer-to-identity mapping are configured to match that Gateway service account / workload subject (see AC-5).
- CI pipelines request attestation via REST or `apme check --attest`; they verify that the returned Bundle was signed by the **trusted Gateway identity**, not by the CI job itself.

**Optional CI-bound identity** (out of v1 scope):
- v1 does **not** accept client-supplied OIDC tokens. Gateway obtains its workload identity token itself.
- Any future token-accept path requires a separate ADR covering TLS-only transport, issuer/audience/subject validation, log redaction, memory-only handling, and threat model. Until then, CLI/`--attest` never sends private keys or OIDC bearer tokens.

**Forbidden ambiguity**:
- Do not document or implement "keyless with no identity path."
- Do not default trust policy to broad CI issuer wildcards that would accept any GitHub Actions / GitLab job while Gateway actually signs as a cluster SA (or vice versa).

Air-gapped deployments **must** use keyed signing (`APME_SIGNING_KEY`); keyless requires outbound OIDC, Fulcio, and Rekor.

### Signing idempotency and recovery

Rekor writes are not naturally idempotent. Gateway must enforce one logical attestation per `scan_id`:

1. Persist scan completion first.
2. Atomically claim signing (`scan_in_progress` → `pending`) for exactly one worker.
3. Confine paths and compute Subject Digest from the immutable snapshot.
4. Assemble the in-toto Statement (RFC 8785) including that digest.
5. **Durable write-ahead before any Fulcio/Rekor network write**: persist claim metadata and Statement/PAE bytes keyed by `scan_id` **before** calling Rekor.
6. After external signing succeeds, stage Bundle/envelope bytes durably before marking `ready`.
7. On retry after Fulcio/Rekor success + persist failure: flush staged bytes; never open a second Rekor entry for the same claim.
8. If staging is lost after an external write: `failed` / `SIGNING_RECOVERY_FAILED` — do not invent a second winner.
9. Retain the snapshot until attestation is terminal (`ready`/`failed`); then delete.
10. `ready` records are immutable; GET endpoints never sign.

See [contract.md](contract.md) for the normative API wording.

### Subject digest ownership

Gateway owns Subject Digest computation against an **immutable snapshot** it staged for the `scan_id`, using **owned-scope** paths from persisted `content_graph_json` after path confinement. Digest epoch is **`submitted_snapshot`** (Gateway staged bytes), with format-rewrite disabled for attest sessions so Primary analysis aligns with those bytes. Missing owned paths at signing time fail closed (`SUBJECT_TREE_INCOMPLETE`). Invalid/escaping/referenced-out-of-tree paths fail closed (`SUBJECT_PATH_INVALID`).

### Offline / air-gapped mode

Keyless Sigstore signing requires outbound OIDC, Fulcio, and Rekor access. Air-gapped deployments **must** configure keyed signing via `APME_SIGNING_KEY` (Helm Secret mount).

Verification when `APME_SIGSTORE_OFFLINE=true`:
- **Keyed attestations**: verify signature against the configured trust-set public keys only; Rekor checks are skipped.
- **Keyless attestations with persisted bundle**: verify using embedded certificate, OIDC claims, Rekor SET, and the persisted `trustedRoot` metadata — **no live OIDC, Fulcio, or Rekor network access**. If the bundle is missing or incomplete, verification returns `valid: false` with reason `REKOR_ENTRY_MISSING` or `TRUST_SERVICE_UNAVAILABLE` immediately — **without attempting network calls**.
- **Keyless without embedded bundle under offline mode**: return `valid: false` with `TRUST_SERVICE_UNAVAILABLE` immediately; do not attempt live trust-service lookup.

Live Rekor/Fulcio fetch for incomplete envelopes is allowed **only** when `APME_SIGSTORE_OFFLINE` is unset/false (explicitly online verification).

**Historical certificate validation** (retained keyless attestations):
- Fulcio signing certificates are short-lived. For attestations within the retention window, `EXPIRED_CERTIFICATE` checks use the authenticated Rekor `integratedTime` (or bundled SET timestamp), **not** the verifier's current clock.
- Gateway persists the Sigstore `trustedRoot` version used at signing alongside `verificationMaterial`. Retired Fulcio roots remain available for verification until all attestations signed under that root pass the retention window (default: 90 days).

### Key management (Helm)

Signing keys live in a Kubernetes Secret mounted read-only into the Gateway container (e.g., `/etc/apme/secrets/signing-key.pem`). Gateway reads the path from `APME_SIGNING_KEY`. Keys are never mounted into engine containers.

Each keyed signer has a stable **key ID** (`keyid` in the envelope) derived from the public key fingerprint (see contract wire-format section for exact algorithm). Gateway persists the public key material in a verifier **trust set** keyed by `keyid`.

**CLI `--signing-key <keyid-or-path>`** and **`APME_SIGNING_KEY`**: same grammar — allowlisted `keyid` or path under the configured in-container signing-key directory. Client host paths are not accepted. Resolved only on Gateway.

**Rotation procedure**:
1. Add the new private key to the Secret (or a parallel Secret) and configure Gateway to sign with the new key.
2. Register the new public key in the trust set with its `keyid`.
3. Rolling-restart Gateway; new attestations use the new key.
4. **Do not remove** the previous public key from the trust set until all attestations signed with that `keyid` have passed the configured retention window (default: 90 days — see Attestation retention below).

Retired keys remain trusted for verification only; they are not used for new signatures.

### Attestation retention

Attestations are stored alongside scan records in Gateway persistence. Retention follows the scan retention policy (default: 90 days, configurable via Gateway settings — exact setting TBD in implementation task). Purging a scan deletes its attestation.

### Wire format

- Keyless: Sigstore Bundle v0.3 with `dsseEnvelope` (not `messageSignature`).
- Keyed: DSSE envelope only.
- Statement bytes: RFC 8785 (JCS) before Base64 into DSSE `payload`.

## Open Questions

1. Whether to expose a dedicated `APME_ATTESTATION_RETENTION_DAYS` setting or inherit scan retention only
