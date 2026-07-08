---
rule_id: R115
validator: native
description: File deletion (annotation-based).
scope: task
---

## File deletion (R115)

Flags file-deletion tasks (`state: absent`) where the path contains
Jinja2 template syntax. A parameterized deletion path is a destructive-
action risk when variables are externally controlled.

### Example: violation

```yaml
- name: Remove file with parameterized path
  ansible.builtin.file:
    path: "{{ cleanup_dir }}/temp_data"
    state: absent
```

### Example: pass

```yaml
- name: Remove known temp file
  ansible.builtin.file:
    path: /tmp/build-cache
    state: absent
```
