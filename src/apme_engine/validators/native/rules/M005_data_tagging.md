---
rule_id: M005
validator: native
description: Registered variable used in Jinja template may be untrusted in 2.19+.
scope: task
---

## Data tagging trust model (M005)

In ansible-core 2.19+, the trust model is inverted. Strings from module results (registered variables) are untrusted and will not be re-templated. Playbooks that register a variable and then use it inside `{{ }}` expressions may fail with "Conditional is marked as unsafe."

### Example: pass

```yaml
- name: Test play
  hosts: localhost
  tasks:
    - name: Simple task
      ansible.builtin.debug:
        msg: hello
```
