---
rule_id: L079
validator: native
description: Role defaults/vars should be prefixed with the role name.
scope: role
---

## Role variable prefix (L079)

Role defaults and vars should be prefixed with the role name to avoid namespace collisions.

### Example: violation

Role `myrole` with `defaults/main.yml`:

```yaml
packages:
  - nginx
```

### Example: pass

Role `myrole` with `defaults/main.yml`:

```yaml
myrole_packages:
  - nginx
```
