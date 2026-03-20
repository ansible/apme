---
rule_id: P004
validator: native
description: Validate variables (Ansible required).
scope: inventory
---

## Variable validation (P004)

Validate variables (Ansible required).

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
