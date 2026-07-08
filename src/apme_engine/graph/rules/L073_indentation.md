---
rule_id: L073
validator: native
description: YAML should use 2-space indentation.
scope: playbook
---

## Indentation (L073)

YAML should use 2-space indentation consistently. Lines with indentation
that is not a multiple of 2 spaces are flagged.

Checks `yaml_lines` on each task node; the harness may or may not populate
this field depending on engine internals.

### Example: violation

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Bad indent
      ansible.builtin.debug:
         msg: hello
```

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.debug:
        msg: hello
```
