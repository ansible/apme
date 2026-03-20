---
rule_id: L041
validator: native
description: Task keys should follow canonical order (e.g. name before module).
scope: task
---

## Key order (L041)

Task keys should follow canonical order. The `name` key should appear before the action/module key. This rule requires the raw YAML source lines to be preserved on the task spec, which is only available during file-based scanning.

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
