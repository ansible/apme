---
rule_id: R113
validator: native
description: Package installation uses a parameterized package name.
scope: task
---

## Parameterized pkg install (R113)

Flags package installation tasks whose package name contains Jinja syntax.

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
