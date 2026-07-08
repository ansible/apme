---
rule_id: L094
validator: native
description: Do not put dynamic dates in templates; breaks change detection.
scope: task
---

## Dynamic template date (L094)

Do not put dynamic "last modified" dates in templates. It breaks idempotent change detection.

### Example: violation

```yaml
- name: Deploy config
  ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/app.conf
  vars:
    last_modified: "{{ ansible_date_time.iso8601 }}"
```

### Example: pass

```yaml
- name: Deploy config
  ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/app.conf
```
