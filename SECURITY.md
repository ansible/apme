# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in APME, please report it responsibly.

### How to Report

**DO NOT** open a public GitHub issue for security vulnerabilities.

Instead, please report security issues via:

1. **GitHub Security Advisories**: Use the "Report a vulnerability" button in the Security tab

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 7 days
- **Resolution Timeline**: Communicated after assessment

### Safe Harbor

We consider security research conducted in accordance with this policy to be:
- Authorized under anti-hacking laws
- Exempt from DMCA restrictions
- Conducted in good faith

We will not pursue legal action against researchers who follow this policy.

---

## Security Best Practices for Contributors

### Secrets Management

**NEVER commit secrets to the repository.**

```bash
# Check for secrets before committing
gitleaks detect --source . --verbose

# prek hooks run automatically on commit (ruff, mypy, pydoclint)
# Install with: uv tool install prek && prek install
```

#### What NOT to commit:
- API keys, tokens, passwords
- Private keys (*.pem, *.key, id_rsa)
- Environment files (.env, .env.local)
- Ansible Vault passwords
- Cloud credentials (AWS, GCP, Azure)
- Kubeconfig files
- Database connection strings

#### Safe alternatives:
- Use environment variables
- Use Ansible Vault for encrypted secrets
- Use `.env.example` with placeholder values
- Document required secrets in README

### Code Security

#### Input Validation
```python
# Always validate external input
def scan_path(path: Path) -> ScanResult:
    resolved = path.resolve()
    if not resolved.is_relative_to(allowed_root):
        raise SecurityError("Path traversal detected")
```

#### Subprocess Safety
```python
# NEVER use shell=True with user input
# BAD:
subprocess.run(f"scan {user_input}", shell=True)

# GOOD:
subprocess.run(["scan", user_input], shell=False)
```

#### YAML Safety
```python
# Use safe loaders
from ruamel.yaml import YAML
yaml = YAML(typ='safe')  # Prevents arbitrary code execution
```

### Container Security

#### Dockerfile Best Practices
```dockerfile
# Use specific versions, not :latest
FROM python:3.12-slim-bookworm

# Run as non-root user
RUN useradd -r -s /bin/false apme
USER apme

# Don't store secrets in ENV
# BAD: ENV API_KEY=secret123
# GOOD: Use runtime secrets

# Minimize attack surface
RUN apt-get update && apt-get install -y --no-install-recommends \
    required-package \
    && rm -rf /var/lib/apt/lists/*
```

#### Image Scanning
```bash
# Scan images for vulnerabilities
trivy image apme-primary:latest
grype apme-primary:latest
```

### Dependency Security

#### Regular Updates
```bash
# Check for vulnerable dependencies
pip-audit

# Dependencies are managed via pyproject.toml and uv.lock
# Review dependency changes in PRs
```

#### Lock Files
- Always commit lock files (`uv.lock`)
- Review dependency changes in PRs

### gRPC Security

#### Transport Security
```python
# Production: Use TLS
credentials = grpc.ssl_channel_credentials()
channel = grpc.secure_channel('server:50051', credentials)

# Development only: Insecure channel
# channel = grpc.insecure_channel('localhost:50051')
```

#### Input Validation
- Validate all protobuf message fields
- Set maximum message sizes
- Implement rate limiting

### Logging Security

```python
# NEVER log secrets
logger.info("Connecting to database")  # GOOD
logger.info(f"Password: {password}")   # BAD

# Sanitize user input in logs
logger.info("Scanning path", path=sanitize(user_path))
```

---

## Security Checklist for PRs

Before submitting a PR, verify:

- [ ] No secrets in code or comments
- [ ] No hardcoded credentials
- [ ] Input validation for all external data
- [ ] Safe subprocess calls (no shell=True with user input)
- [ ] Dependencies updated and scanned
- [ ] Container runs as non-root user
- [ ] Sensitive data not logged
- [ ] gitleaks passes locally

---

## Security Tools

### Recommended

```bash
# Secret detection (manual scan)
gitleaks detect --source . --verbose

# Python security linting
bandit -r src/

# Dependency vulnerabilities
pip-audit

# Container scanning
trivy image apme-primary:latest
```

---

## Incident Response

If you believe the project has been compromised:

1. **Rotate all credentials** immediately
2. **Audit git history** for leaked secrets
3. **Notify maintainers** via GitHub Security Advisory
4. **Document the incident** for post-mortem

### Git History Cleanup

If secrets are committed:
```bash
# Use git-filter-repo (preferred) or BFG Repo-Cleaner
git filter-repo --path secrets.txt --invert-paths

# Force push (coordinate with team)
git push --force-with-lease
```

**Note**: Once pushed to a public repo, assume the secret is compromised and rotate it.
