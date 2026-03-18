---
rule_id: L045
validator: native
description: Avoid inline environment in tasks.
---

## Inline env var (L045)

Avoid inline environment in tasks.

### Example: violation

```yaml
- name: Run with env
  ansible.builtin.command:
    cmd: echo hello
  environment:
    MY_VAR: value
```

### Example: pass

```yaml
- name: Run without env
  ansible.builtin.command:
    cmd: echo hello
```
