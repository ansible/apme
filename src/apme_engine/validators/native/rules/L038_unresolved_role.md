---
rule_id: L038
validator: native
description: Role could not be resolved.
---

## Unresolved role (L038)

Role could not be resolved.

### Example: violation

```yaml
- name: Include missing role
  ansible.builtin.include_role:
    name: nonexistent_role_xyz_999
```

### Example: pass

```yaml
- name: Debug
  ansible.builtin.debug:
    msg: no role include
```
