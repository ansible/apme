---
rule_id: M005
validator: native
description: Registered variable used in Jinja template may be untrusted in 2.19+.
scope: task
ansible_core_version: ">=2.19"
---

## Data tagging trust model (M005)

In ansible-core 2.19+, the trust model is inverted. Strings from module results (registered variables) are untrusted and will not be re-templated. Playbooks that register a variable and then use it inside `{{ }}` expressions may fail with "Conditional is marked as unsafe."

### Example: violation

```yaml
- name: Check disk space
  hosts: localhost
  tasks:
    - name: Get filesystem usage
      ansible.builtin.command: df -h /
      register: disk_output

    - name: Report disk usage
      ansible.builtin.debug:
        msg: "Disk usage: {{ disk_output.stdout }}"
```

### Example: pass

```yaml
- name: Simple play
  hosts: localhost
  tasks:
    - name: Show greeting
      ansible.builtin.debug:
        msg: "Hello, world"
```
