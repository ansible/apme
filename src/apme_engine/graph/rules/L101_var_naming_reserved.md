---
rule_id: L101
validator: native
description: Variable names must not collide with Ansible reserved names.
scope: playbook
---

## Variable naming reserved (L101)

Variable names defined via `set_fact` or `register` must not collide with Ansible built-in reserved variable names like `ansible_facts`, `inventory_hostname`, `groups`, etc.

Maps to ansible-lint `var-naming[no-reserved]`.

### Example: violation

```yaml
- name: Override facts
  ansible.builtin.set_fact:
    ansible_facts: "bad"
```

### Example: pass

```yaml
- name: Set app facts
  ansible.builtin.set_fact:
    app_facts: "good"
```
