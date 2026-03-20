---
rule_id: L030
validator: native
description: Prefer ansible.builtin modules when available.
scope: task
---

## Non-builtin use (L030)

Prefer ansible.builtin modules when available. Triggers when a task uses a module whose resolved FQCN is outside the `ansible.builtin` namespace (e.g. `community.general.*`). Requires the target collection to be installed for resolution.

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
