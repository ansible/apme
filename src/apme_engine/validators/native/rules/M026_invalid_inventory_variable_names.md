---
rule_id: M026
validator: native
description: Inventory variable names must be valid Python identifiers (enforced in 2.23)
scope: task
---

## Invalid inventory variable names (M026)

Inventory variable names must be valid Python identifiers (enforced in 2.23)

**Removal version**: 2.23
**Fix tier**: 2
**Audience**: content

### Detection

Validate variable names in host/group vars against Python identifier rules

### Example: violation

```yaml
# host_vars/myhost.yml
my-var: some_value
3rd_party: other_value
```

### Example: pass

```yaml
# host_vars/myhost.yml
my_var: some_value
third_party: other_value
```

### Remediation

Rename is mechanical but may break references
