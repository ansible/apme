---
rule_id: L091
validator: native
description: Use | bool for bare variables in when conditions.
scope: task
---

## Bool filter (L091)

Use `| bool` for bare variables in `when` conditions to ensure consistent boolean evaluation.

### Example: violation

```yaml
- name: Run if enabled
  ansible.builtin.command: whoami
  when: my_flag
```

### Example: pass

```yaml
- name: Run if enabled
  ansible.builtin.command: whoami
  when: my_flag | bool
```
