---
rule_id: L051
validator: native
description: "Jinja spacing: {{ var }} not {{var}}."
scope: task
---

## Jinja (L051)

Jinja spacing: {{ var }} not {{var}}.

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
    - name: Ok
      ansible.builtin.command: whoami
```
