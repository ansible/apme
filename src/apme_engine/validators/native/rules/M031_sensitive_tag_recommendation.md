# M031: Sensitive Tag Recommendation

## Summary

Variables with sensitive names (password, token, secret, api_key, etc.) should use ansible-core 2.19+'s `Sensitive` data tag for automatic redaction in AAP job output.

## Rationale

AAP currently has a security gap in how it handles sensitive variable values:

- **Current state**: AAP automatically redacts survey password-type variables but does NOT redact extra vars, credential-injected variables, or playbook-defined secrets. These appear in **plaintext** in job stdout and API event data.

- **Existing mitigation**: Ansible's `no_log: true` directive suppresses the ENTIRE task output. Organizations needing both secret protection AND audit trails cannot satisfy both requirements.

- **The solution**: ansible-core 2.19+ introduces `Sensitive` data tagging that enables selective redaction of specific values while preserving non-sensitive output visibility. The tag propagates through Jinja templating and string operations, providing comprehensive protection.

This rule identifies variables that should use the `Sensitive` tag based on naming patterns that indicate sensitive data.

## What This Rule Detects

### 1. set_fact tasks defining sensitive variables

```yaml
# FLAGGED - db_password is a sensitive variable name
- name: Store database password
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_pass }}"

# RECOMMENDED - Apply Sensitive tag
- name: Store database password
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_pass | sensitive }}"
```

### 2. Tasks registering results to sensitive variable names

```yaml
# FLAGGED - api_token_result suggests sensitive content
- name: Get API token
  ansible.builtin.uri:
    url: "https://api.example.com/token"
  register: api_token_result

# RECOMMENDED - Use no_log or consider Sensitive tag for derived values
- name: Get API token
  ansible.builtin.uri:
    url: "https://api.example.com/token"
  register: api_token_result
  no_log: true
```

## Sensitive Variable Patterns

The rule flags variables containing these terms as word-bounded segments:

| Pattern | Examples |
|---------|----------|
| `password`, `passwd`, `pwd` | `db_password`, `user_passwd`, `admin_pwd` |
| `secret`, `secrets` | `app_secret`, `client_secret` |
| `token` | `auth_token`, `access_token`, `bearer_token` |
| `api_key`, `apikey` | `aws_api_key`, `github_apikey` |
| `credential`, `credentials`, `cred` | `db_credential`, `ssh_cred` |
| `private_key`, `ssh_key`, `access_key`, `client_key` | `ssl_private_key` |
| `bearer`, `jwt`, `oauth` | `jwt_token`, `oauth_token` |

Word-boundary matching prevents false positives like `secretary_name` (contains 'secret') or `tokenized_value` (contains 'token').

## When the Rule Passes

The rule does NOT fire when:

1. **no_log is set**: If `no_log: true` is set on the task or a containing block/play, the user has already addressed the concern (though `Sensitive` tag is recommended for better auditability).

2. **Non-sensitive variable names**: Variables like `hostname`, `port`, `username` do not trigger the rule.

## Relationship to Other Rules

| Rule | Focus |
|------|-------|
| **L047** | Password-like *parameter names* in task args |
| **L110** | Debug tasks logging sensitive *variables* without no_log |
| **M031** | Variables being *defined* that should use Sensitive tag |

M031 addresses the broader variable lifecycle at definition time, complementing L047 (parameter usage) and L110 (output exposure).

## Migration Path

### ansible-core 2.18 and earlier

Use `no_log: true` on tasks that handle sensitive data:

```yaml
- name: Set database credentials
  ansible.builtin.set_fact:
    db_password: "{{ vault_password }}"
  no_log: true
```

### ansible-core 2.19+

Apply the `Sensitive` tag to values at definition:

```yaml
- name: Set database credentials
  ansible.builtin.set_fact:
    db_password: "{{ vault_password | sensitive }}"
```

The `Sensitive` tag provides:
- Automatic redaction in job output
- Propagation through Jinja templating and string operations
- Preservation of non-sensitive output for auditing
- No need for `no_log: true` which suppresses all output

## Background

This rule supports **ANSTRAT-1720: Selective Redaction of Sensitive Variables in Job Output**, implementing "Approach C: Data Tagging with Sensitive Tag" from the design document.

The feature addresses a gap affecting 20+ enterprise accounts across finance, government, healthcare, and telecom sectors where compliance frameworks require demonstrable protection of secrets in all system output.

## Severity

**MEDIUM** - Exposes sensitive data in job output without redaction, but can be mitigated with `no_log: true` as an interim measure.

## Tags

- `security` - Addresses sensitive data exposure
- `coding` - Best practice for variable handling

## References

- [ANSTRAT-1720](https://issues.redhat.com/browse/ANSTRAT-1720) - Selective Redaction of Sensitive Variables
- [ansible-core Data Tagging](https://docs.ansible.com/ansible/devel/) - 2.19+ documentation
- ADR-008: Rule ID Conventions
