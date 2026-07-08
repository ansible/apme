---
rule_id: L092
validator: native
description: Avoid loop variable references in task names.
scope: task
---

## Loop var in name (L092)

Avoid loop variable references (like `{{ item }}`) in task names. They cause expansion issues in logs.

### Example: violation

```yaml
- name: "Install {{ item }}"
  ansible.builtin.apt:
    name: "{{ item }}"
  loop:
    - nginx
    - curl
```

### Example: pass

```yaml
- name: Install packages
  ansible.builtin.apt:
    name: "{{ item }}"
  loop:
    - nginx
    - curl
```
