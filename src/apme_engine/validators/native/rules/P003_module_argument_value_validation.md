---
rule_id: P003
validator: native
description: Validate module argument values (Ansible required).
scope: task
---

## Module argument value (P003)

Validate module argument values (Ansible required).

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
