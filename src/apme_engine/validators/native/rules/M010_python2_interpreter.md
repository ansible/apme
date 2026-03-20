---
rule_id: M010
validator: native
description: ansible_python_interpreter set to Python 2; dropped in 2.18+.
scope: play
---

## Python 2 interpreter (M010)

ansible-core 2.18+ requires Python 3.11+ on the control node and Python 3.8+ on target nodes. Python 2.7 is dropped. Detect `ansible_python_interpreter` set to a Python 2.x path.

### Example: violation

```yaml
- name: Run on old host
  ansible.builtin.command: echo hello
  vars:
    ansible_python_interpreter: /usr/bin/python2.7
```

### Example: pass

```yaml
- name: Run on host
  ansible.builtin.command: echo hello
  vars:
    ansible_python_interpreter: /usr/bin/python3
```
