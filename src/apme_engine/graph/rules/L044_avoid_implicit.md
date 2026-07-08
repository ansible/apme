---
rule_id: L044
validator: native
description: Set state explicitly where it matters.
scope: task
---

## Avoid implicit (L044)

Set state explicitly where it matters.

### Example: violation

```yaml
- name: Create file
  ansible.builtin.file:
    path: /tmp/foo
```

### Example: pass

```yaml
- name: Create file
  ansible.builtin.file:
    path: /tmp/foo
    state: touch
```
