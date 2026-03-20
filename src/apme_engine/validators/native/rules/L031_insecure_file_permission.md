---
rule_id: L031
validator: native
description: File permission may be insecure (annotation-based).
scope: task
---

## Insecure file permission (L031)

File permission may be insecure (annotation-based). This rule depends on annotation (is_insecure_permissions) and cannot be tested in the harness.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
