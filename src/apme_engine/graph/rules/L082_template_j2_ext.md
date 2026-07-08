---
rule_id: L082
validator: native
description: Template source files should use .j2 extension.
scope: task
---

## Template .j2 extension (L082)

Template source files should use `.j2` extension for clarity.

### Example: violation

```yaml
- name: Deploy config
  ansible.builtin.template:
    src: app.conf
    dest: /etc/app.conf
```

### Example: pass

```yaml
- name: Deploy config
  ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/app.conf
```
