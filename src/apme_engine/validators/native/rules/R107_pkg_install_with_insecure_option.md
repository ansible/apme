---
rule_id: R107
validator: native
description: Package install with insecure option (annotation-based).
scope: task
---

## Pkg install insecure (R107)

Package install with insecure option (e.g. validate_certs: false). Depends on PACKAGE_INSTALL annotation.

### Example: pass

```yaml
- name: Install package
  ansible.builtin.apt:
    name: nginx
    state: present
```
