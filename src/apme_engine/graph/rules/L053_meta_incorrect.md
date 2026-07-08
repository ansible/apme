---
rule_id: L053
validator: native
description: Role meta should have valid structure.
scope: role
---

## Meta incorrect (L053)

Role meta should have valid structure (`galaxy_info` must be a dict,
`dependencies` must be a list, and scalar fields must have correct types).

Requires ROLE node context; cannot be tested in the playbook-only harness.

### Example: violation

```yaml
galaxy_info:
  role_name: 12345
  author: Example
  platforms: "not a list"
```

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  author: Example
```
