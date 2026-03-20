---
rule_id: L046
validator: native
description: Avoid raw/command/shell without args key.
scope: task
---

## No free form (L046)

Avoid raw/command/shell without args key.

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
  tasks:
    - name: Run whoami with structured args
      ansible.builtin.command:
        cmd: whoami
```
