# ADR-010: Gitleaks as a gRPC Validator

## Status

Accepted

## Date

2026-03

## Context

Secret detection was needed. Several tools were evaluated:
- Gitleaks
- detect-secrets
- GitHub Secret Scanning
- secrets-patterns-db

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Native Python regex rules (R502–R504) | In-process, no binary dependency | Limited patterns, maintenance burden |
| Gitleaks | Single Go binary, 800+ patterns, JSON output, maintained | External binary, container needed |
| detect-secrets | Python-native, entropy detectors | Slower, less pattern coverage |

## Decision

**Use Gitleaks as a dedicated validator in its own container, exposed via gRPC.**

The previously planned native SEC rules (R502–R504) were superseded.

## Rationale

- 800+ maintained patterns covering:
  - AWS keys
  - Private keys
  - Passwords
  - Tokens
  - Provider-specific formats
- `--no-git` mode scans raw files — compatible with the chunked filesystem pattern
- JSON report output is easy to parse and convert to `Violation` messages
- Ansible-aware filtering (vault-encrypted files, Jinja2 expressions) is added in the gRPC wrapper
- The `ValidateRequest` already carries raw file content, so Gitleaks receives exactly what it needs

> "Yes, gitleaks in a container as a validator via grpc." — user decision

## Consequences

### Positive
- 800+ maintained secret patterns
- Single maintained binary
- JSON output easy to parse
- Ansible-aware filtering in wrapper

### Negative
- External binary dependency
- Requires container
- Go binary, not Python

## Implementation Notes

### Container

```dockerfile
FROM ghcr.io/gitleaks/gitleaks:latest
# Add gRPC wrapper
COPY gitleaks_validator.py /app/
```

### gRPC Wrapper

```python
async def validate(self, request: ValidateRequest) -> ValidateResponse:
    # Write files to temp directory
    # Run gitleaks --no-git --report-format json
    # Parse JSON output
    # Filter vault-encrypted files
    # Filter Jinja2 expressions ({{ password }})
    # Convert to Violation messages
```

### Rule IDs

Gitleaks findings map to R5xx rules:
- R501: AWS credentials
- R502: Private keys
- R503: Generic secrets
- etc.

## Related Decisions

- ADR-001: gRPC communication
- ADR-008: Rule ID conventions (R5xx)
