# REQ-019: Scan Attestation — API Contract

## REST Endpoints

### GET /api/v1/scans/{scan_id}/attestation

Returns signed attestation for a completed scan.

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
  "timestamp": "<RFC3339>"
}
```

## CLI Commands

```bash
apme check --attest [--signing-key <path>]
apme verify-attestation <file>
```

## Proto Extensions

TBD — may extend FixSession response or add separate RPC.
