---
rule_id: L047
validator: native
description: Set no_log for password-like parameters.
scope: task
---

## No log password (L047)

Set no_log for password-like parameters.

For tasks shared through includes, every play and include path that can reach
the task must inherit `no_log: true`; any unprotected execution can expose the
password.

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
