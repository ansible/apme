---
rule_id: P004
validator: ansible
description: Validate variables (Ansible required).
scope: inventory
status: delegated
status_reason: >
  Emitted by the Ansible validator via argspec validation (L058/L059).
  Not a native GraphRule — the Ansible validator owns variable validation
  in the context of module argument resolution.
---

## Variable validation (P004)

Validate variables (Ansible required). This policy check is **delegated to
the Ansible validator** which validates variable usage within module arguments
during argspec resolution. The equivalent runtime checks are emitted as
L058/L059 by the Ansible validator.

### Status

**Delegated** — no native `_graph.py` implementation. The Ansible validator
validates variable references within module arguments via L058
(docstring-based) and L059 (mock/patch-based). P004 is a policy-level alias;
the runtime rule_id in scan output is L058 or L059.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
