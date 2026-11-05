---
rule_id: P004
validator: ansible
description: Validate variables (Ansible required).
scope: task
status: delegated
status_reason: >
  L039 performs APME's approximate static undefined-variable analysis. It is
  not equivalent to ARI's argument-context variable validation.
---

## Variable validation (P004)

Validates possible undefined-variable use with APME's static graph analysis.
This is an approximation of variable validity, not argument-context
validation.

### Status

**Partial** — L039 is the runtime rule. It evaluates variable provenance and
common exemptions but is not an equivalent implementation of ARI's
argument-context checks.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
