---
rule_id: L093
validator: native
description: Do not override role defaults/vars with set_fact.
scope: task
---

## set_fact override (L093)

Do not override role defaults/vars with `set_fact`. Using the same variable name as a role default or role var creates confusion about the effective value and breaks the Ansible precedence model. Use a different variable name instead.

Requires role context with `role_defaults`/`role_vars` populated by the engine.

### Example: violation

```yaml
- name: Override port dynamically
  ansible.builtin.set_fact:
    http_port: 8080
```

### Example: pass

```yaml
- name: Compute a derived value
  hosts: localhost
  tasks:
    - name: Set a new fact
      ansible.builtin.set_fact:
        deployment_timestamp: "2025-01-01"
```
