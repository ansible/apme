---
rule_id: L048
validator: native
description: copy with remote_src should set owner.
scope: task
---

## No same owner (L048)

copy with remote_src should set owner.

### Example: violation

```yaml
- name: Copy remote file
  ansible.builtin.copy:
    src: /tmp/remote_file
    dest: /opt/app/file
    remote_src: true
```

### Example: pass

```yaml
- name: Copy remote file
  ansible.builtin.copy:
    src: /tmp/remote_file
    dest: /opt/app/file
    remote_src: true
    owner: root
```
