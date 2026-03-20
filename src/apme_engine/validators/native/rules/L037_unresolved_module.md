---
rule_id: L037
validator: native
description: Module name could not be resolved.
scope: collection
---

## Unresolved module (L037)

Module name could not be resolved.

### Example: violation

```yaml
- name: Typo in module
  ansible.builtin.copyyy:
    src: a
    dest: /tmp/b
```

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
