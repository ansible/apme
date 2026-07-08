---
rule_id: L030
validator: native
description: Non-builtin module used when a builtin equivalent exists.
scope: task
---

## Non-builtin use (L030)

Prefer ansible.builtin modules when a builtin equivalent exists. Triggers when a task uses a non-builtin FQCN (e.g. `community.general.copy`) and the short module name (`copy`) has a known `ansible.builtin` counterpart.

Collection modules with no builtin equivalent (e.g. `community.general.timezone`) are **not** flagged — they are legitimate external dependencies.

### Example: violation

```yaml
- name: Copy file from collection
  community.general.copy:
    src: a
    dest: /tmp/b
```

### Example: pass (builtin used)

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```

### Example: pass (no builtin equivalent)

```yaml
- name: Set timezone
  community.general.timezone:
    name: UTC
```
