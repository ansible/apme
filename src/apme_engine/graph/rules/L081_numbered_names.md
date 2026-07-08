---
rule_id: L081
validator: native
description: Do not number roles or playbooks.
scope: playbook
---

## Numbered names (L081)

Do not number roles or playbooks (e.g. `01_setup.yml`). Use descriptive names instead.

### Example: violation

```yaml
- name: Setup task in numbered file
  ansible.builtin.debug:
    msg: "This file is named 01_setup.yml"
```

### Example: pass

```yaml
- name: Setup task in descriptive file
  ansible.builtin.debug:
    msg: "This file is named setup.yml"
```
