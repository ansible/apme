---
rule_id: L084
validator: native
description: Task names in included sub-task files should use a prefix.
scope: task
---

## Sub-task prefix (L084)

Task names in included sub-task files should use the filename (without extension) as a prefix separator so logs show the origin (e.g. `install | Install package` for tasks in `install.yml`).

### Example: violation

In `roles/myrole/tasks/install.yml`:

```yaml
- name: Install package
  ansible.builtin.apt:
    name: nginx
```

### Example: pass

In `roles/myrole/tasks/install.yml`:

```yaml
- name: install | Install package
  ansible.builtin.apt:
    name: nginx
```
