---
rule_id: R111
validator: native
description: Parameterized role import (annotation-based).
scope: task
---

## Parameterized import role (R111)

include_role with variable role name.

### Example: violation

```yaml
- name: Include role
  ansible.builtin.include_role:
    name: "{{ role_name }}"
```

### Example: pass

```yaml
- name: Include role
  ansible.builtin.include_role:
    name: my_role
```
