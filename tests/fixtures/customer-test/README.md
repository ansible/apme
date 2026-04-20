# Customer Test Fixtures

Sample Ansible files for testing APME functionality. These are designed to trigger specific rules and demonstrate APME's detection capabilities.

## Files

| File | Purpose | Expected Result |
|------|---------|-----------------|
| `clean-playbook.yml` | Known-good reference | 0 violations |
| `violations-playbook.yml` | Lint and modernize violations | Multiple L/M violations |
| `secrets-test.yml` | Hardcoded credentials | SEC:* violations |
| `deprecated-modules.yml` | Deprecated/removed modules | M002/M004 violations |
| `messy-formatting.yml` | YAML style issues | Formatting changes with `apme format` |
| `vault-reference.yml` | Vault-encrypted vars | Should NOT trigger SEC:* (false positive check) |

## Usage

```bash
# Scan all fixtures
apme check tests/fixtures/customer-test/

# Scan specific file
apme check tests/fixtures/customer-test/violations-playbook.yml

# Check formatting
apme format --check tests/fixtures/customer-test/messy-formatting.yml

# Test secret detection (should find violations)
apme check tests/fixtures/customer-test/secrets-test.yml

# Test vault filtering (should NOT find violations)
apme check tests/fixtures/customer-test/vault-reference.yml
```

## Expected Violations Summary

| File | L-rules | M-rules | R-rules | SEC-rules |
|------|---------|---------|---------|-----------|
| clean-playbook.yml | 0 | 0 | 0 | 0 |
| violations-playbook.yml | 3+ | 2+ | 1+ | 0 |
| secrets-test.yml | 0 | 0 | 0 | 2+ |
| deprecated-modules.yml | 0 | 2+ | 0 | 0 |
| messy-formatting.yml | 1+ | 0 | 0 | 0 |
| vault-reference.yml | 0 | 0 | 0 | 0 |
