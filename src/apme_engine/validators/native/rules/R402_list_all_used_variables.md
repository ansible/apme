---
rule_id: R402
validator: native
description: Report variables used at end of sequence.
scope: task
---

## List used variables (R402)

Report variables used at end of sequence.

### Example: violation

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Bad
      ansible.builtin.shell: whoami
```

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks: []
```
