# REQ-019: Scan Attestation — Design

## Status

Draft — provisional decisions recorded below; final ADR may follow during implementation.

## Design Decisions

### Offline / air-gapped mode

Keyless Sigstore signing requires outbound OIDC, Fulcio, and Rekor access. Air-gapped deployments **must** configure keyed signing via `APME_SIGNING_KEY` (Helm Secret mount).

Verification when `APME_SIGSTORE_OFFLINE=true`:
- **Keyed attestations**: verify signature against the configured trust-set public keys only; Rekor checks are skipped.
- **Keyless attestations with persisted bundle**: verify using embedded certificate, OIDC claims, Rekor SET, and the persisted `trustedRoot` metadata — **no live OIDC, Fulcio, or Rekor network access required**. If the bundle is missing or incomplete, verification returns `valid: false` with reason `REKOR_ENTRY_MISSING` or `TRUST_SERVICE_UNAVAILABLE` — not a silent pass.
- **Live keyless verification without embedded bundle**: requires Rekor/Fulcio reachability to fetch missing proof material; returns `TRUST_SERVICE_UNAVAILABLE` when required inputs cannot be obtained.

**Historical certificate validation** (retained keyless attestations):
- Fulcio signing certificates are short-lived. For attestations within the retention window, `EXPIRED_CERTIFICATE` checks use the authenticated Rekor `integratedTime` (or bundled SET timestamp), **not** the verifier's current clock.
- Gateway persists the Sigstore `trustedRoot` version used at signing alongside `verificationMaterial`. Retired Fulcio roots remain available for verification until all attestations signed under that root pass the retention window (default: 90 days).

### Key management (Helm)

Signing keys live in a Kubernetes Secret mounted read-only into the Gateway container (e.g., `/etc/apme/secrets/signing-key.pem`). Gateway reads the path from `APME_SIGNING_KEY`. Keys are never mounted into engine containers.

Each keyed signer has a stable **key ID** (`keyid` in the envelope) derived from the public key fingerprint. Gateway persists the public key material in a verifier **trust set** keyed by `keyid`.

**Rotation procedure**:
1. Add the new private key to the Secret (or a parallel Secret) and configure Gateway to sign with the new key.
2. Register the new public key in the trust set with its `keyid`.
3. Rolling-restart Gateway; new attestations use the new key.
4. **Do not remove** the previous public key from the trust set until all attestations signed with that `keyid` have passed the configured retention window (default: 90 days — see Attestation retention below).

Retired keys remain trusted for verification only; they are not used for new signatures.

### Attestation retention

Attestations are stored alongside scan records in Gateway persistence. Retention follows the scan retention policy (default: 90 days, configurable via Gateway settings — exact setting TBD in implementation task). Purging a scan deletes its attestation.

## Open Questions

1. Whether to expose a dedicated `APME_ATTESTATION_RETENTION_DAYS` setting or inherit scan retention only
