---
rule_id: L034
validator: native
description: Lower-precedence override may be unused.
---

## Unused override (L034)

Lower-precedence override may be unused. This rule cannot easily be tested in the harness (needs complex precedence).

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
