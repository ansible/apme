---
rule_id: R109
validator: native
description: Key/config change (annotation-based).
scope: task
---

## Key config change (R109)

Flags config-change tasks (`rpm_key`, `apt_key`) where the key field
contains Jinja2 template syntax. A parameterized key is a trust-anchor
risk when variables are externally controlled.

### Example: violation

```yaml
- name: Add GPG key from parameterized URL
  ansible.builtin.rpm_key:
    key: "{{ gpg_key_url }}"
    state: present
```

### Example: pass

```yaml
- name: Add GPG key from fixed URL
  ansible.builtin.rpm_key:
    key: https://packages.example.com/gpg.key
    state: present
```
