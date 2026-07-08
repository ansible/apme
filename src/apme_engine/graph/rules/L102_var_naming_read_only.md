---
rule_id: L102
validator: native
description: Do not set read-only Ansible variables.
scope: playbook
---

## Variable naming read-only (L102)

Certain Ansible variables are read-only (`ansible_version`, `inventory_hostname`, `groups`, etc.). Attempting to set them via `set_fact` or `register` will cause unexpected behavior.

Maps to ansible-lint `var-naming[read-only]`.

### Example: violation

```yaml
- name: Bad idea
  ansible.builtin.set_fact:
    ansible_version: "3.0"
```

### Example: pass

```yaml
- name: Store version info
  ansible.builtin.set_fact:
    my_version: "3.0"
```
