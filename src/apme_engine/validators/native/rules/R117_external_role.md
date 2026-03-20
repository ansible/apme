---
rule_id: R117
validator: native
description: Role is from Galaxy/external source.
scope: role
---

## External role (R117)

Role is from Galaxy/external source. Requires role metadata; cannot test in playbook harness.

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  author: Example
```
