---
rule_id: P002
validator: native
description: Validate module argument keys (Ansible required).
---

## Module argument key (P002)

Validate module argument keys (Ansible required).

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
