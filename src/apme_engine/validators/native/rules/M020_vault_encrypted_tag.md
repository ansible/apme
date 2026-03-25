---
rule_id: M020
validator: native
description: Use !vault instead of deprecated !vault-encrypted tag (2.23)
scope: task
---

## !vault-encrypted tag (M020)

Use !vault instead of deprecated !vault-encrypted tag (2.23)

**Removal version**: 2.23
**Fix tier**: 1
**Audience**: content

### Detection

Scan YAML content for !vault-encrypted tag

### Example: violation

```yaml
secret: !vault-encrypted |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

### Example: pass

```yaml
secret: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

### Remediation

Direct tag substitution
