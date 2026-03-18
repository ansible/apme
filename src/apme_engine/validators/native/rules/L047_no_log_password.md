---
rule_id: L047
validator: native
description: Set no_log for password-like parameters.
---

## No log password (L047)

Set no_log for password-like parameters.

### Example: violation

```yaml
- name: Connect with password
  ansible.builtin.uri:
    url: https://api.example.com/login
    password: "{{ secret_password }}"
```

### Example: pass

```yaml
- name: Connect with password
  ansible.builtin.uri:
    url: https://api.example.com/login
    password: "{{ secret_password }}"
  no_log: true
```
