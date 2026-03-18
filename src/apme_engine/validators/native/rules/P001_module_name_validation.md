---
rule_id: P001
validator: native
description: Validate module name (Ansible required).
---

## Module name validation (P001)

Validate module name (Ansible required).

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
