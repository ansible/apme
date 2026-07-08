---
rule_id: P003
validator: ansible
description: Validate module argument values (Ansible required).
scope: task
status: delegated
status_reason: >
  Emitted by the Ansible validator via argspec validation (L058/L059).
  Not a native GraphRule — the Ansible validator owns module argument
  value validation using real module argspecs.
---

## Module argument value (P003)

Validate module argument values (Ansible required). This policy check is
**delegated to the Ansible validator** which loads module argspecs and
validates that argument values match expected types and constraints. The
equivalent runtime checks are emitted as L058/L059 by the Ansible validator.

### Status

**Delegated** — no native `_graph.py` implementation. The Ansible validator
loads module argspecs in a session venv and validates argument values via
L058 (docstring-based) and L059 (mock/patch-based). P003 is a policy-level
alias; the runtime rule_id in scan output is L058 or L059.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
