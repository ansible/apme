---
rule_id: L027
validator: native
description: Roles should have meta/main.yml with metadata.
---

## Role without metadata (L027)

Roles should have meta/main.yml with metadata. This rule checks RunTargetType.Role and cannot be tested with playbook YAML in the test harness.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
