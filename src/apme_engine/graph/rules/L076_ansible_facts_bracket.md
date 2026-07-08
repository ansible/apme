---
rule_id: L076
validator: native
description: Use ansible_facts bracket notation instead of injected fact variables.
scope: task
---

## Ansible facts bracket notation (L076)

Use `ansible_facts['distribution']` bracket notation instead of injected fact variables like `ansible_distribution`.

### Example: violation

```yaml
- name: Show OS
  ansible.builtin.debug:
    msg: "{{ ansible_distribution }}"
```

### Example: pass

```yaml
- name: Show OS
  ansible.builtin.debug:
    msg: "{{ ansible_facts['distribution'] }}"
```
