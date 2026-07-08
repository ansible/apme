---
rule_id: R107
validator: native
description: Package install with insecure option (annotation-based).
scope: task
---

## Pkg install insecure (R107)

Flags package-install tasks that disable security checks — `validate_certs:
false`, `disable_gpg_check: true`, or `allow_downgrade: true`.

### Example: violation

```yaml
- name: Install package without GPG check
  ansible.builtin.dnf:
    name: custom-package
    state: present
    disable_gpg_check: true
```

### Example: pass

```yaml
- name: Install package
  ansible.builtin.dnf:
    name: nginx
    state: present
```
