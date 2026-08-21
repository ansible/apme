# REQ-019: Scan Attestation — Design

## Status

Draft — provisional decisions recorded below; final ADR may follow during implementation.

## Design Decisions

### Offline / air-gapped mode

Keyless Sigstore signing requires outbound OIDC, Fulcio, and Rekor access. Air-gapped deployments **must** configure keyed signing via `APME_SIGNING_KEY` (Helm Secret mount). Verification in air-gapped mode validates the local signature and configured trust roots only; Rekor checks are skipped when `APME_SIGSTORE_OFFLINE=true`.

### Key management (Helm)

Signing keys live in a Kubernetes Secret mounted read-only into the Gateway container (e.g., `/etc/apme/secrets/signing-key.pem`). Gateway reads the path from `APME_SIGNING_KEY`. Keys are never mounted into engine containers. Rotation: update Secret and rolling-restart Gateway; new attestations use the new key, verifiers accept keys in the configured trust set.

### Attestation retention

Attestations are stored alongside scan records in Gateway persistence. Retention follows the scan retention policy (default: 90 days, configurable via Gateway settings — exact setting TBD in implementation task). Purging a scan deletes its attestation.

## Open Questions

1. Whether to expose a dedicated `APME_ATTESTATION_RETENTION_DAYS` setting or inherit scan retention only
2. Whether FixSession should carry attestation inline or remain REST-only for v1
