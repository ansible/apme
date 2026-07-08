---
rule_id: L075
validator: native
description: Template source files should use .j2 extension (ansible_managed best practice).
scope: role
---

## Template .j2 extension (L075)

Template source files should use the `.j2` extension. This also signals that the template should
include `{{ ansible_managed | comment }}` at the top, but content inspection is not currently
performed — only the file extension is checked.

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
