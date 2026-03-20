---
rule_id: L028
validator: native
description: Tasks should have a name.
scope: task
---

## Task without name (L028)

Tasks should have a name.

### Example: violation

```yaml
- ansible.builtin.debug:
    msg: hello
```

### Example: pass

```yaml
- name: Debug message
  ansible.builtin.debug:
    msg: hello
```
