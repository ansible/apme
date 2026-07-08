---
rule_id: L100
validator: native
description: Variable names must not be Python or Ansible keywords.
scope: playbook
---

## Variable naming keyword (L100)

Variable names defined via `set_fact`, `register`, or `include_vars` must not collide with Python or Ansible keywords (e.g. `hosts`, `True`, `when`, `tags`).

Maps to ansible-lint `var-naming[no-keyword]`.

### Example: violation

```yaml
- name: Set a fact
  ansible.builtin.set_fact:
    when: "now"
```

### Example: pass

```yaml
- name: Set a fact
  ansible.builtin.set_fact:
    deploy_when: "now"
```
