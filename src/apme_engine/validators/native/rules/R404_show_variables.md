---
rule_id: R404
validator: native
description: Expose variable_set for the task.
---

## Show variables (R404)

Expose variable_set for the task. Disabled by default.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
