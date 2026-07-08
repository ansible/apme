---
rule_id: R113
validator: native
description: Parameterized package install (annotation-based).
scope: task
---

## Parameterized pkg install (R113)

Package install with variable package name (annotation-based).

### Example: violation

```yaml
- name: Install package
  ansible.builtin.apt:
    name: "{{ pkg_name }}"
    state: present
```

### Example: pass

```yaml
- name: Install package
  ansible.builtin.apt:
    name: nginx
    state: present
```
