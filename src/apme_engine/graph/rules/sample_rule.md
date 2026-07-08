---
rule_id: Sample101
validator: native
description: Example rule that returns task block.
scope: task
---

## Sample rule (Sample101)

Example rule that returns task block. Disabled by default.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
