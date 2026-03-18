---
rule_id: L040
validator: native
description: YAML should not contain tabs; use spaces.
---

## No tabs (L040)

YAML should not contain tabs; use spaces. This rule checks the YAML source for tab characters; tabs may not survive YAML parsing in the test harness.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
