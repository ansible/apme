---
rule_id: L056
validator: native
description: Path may match ignore pattern.
scope: playbook
---

## Sanity (L056)

File path matches a common ignore pattern (`.git/`, `.ansible/`,
`__pycache__`, `.pyc`). Files in these paths should typically be excluded
from scanning.

Requires a file path matching the ignore patterns; the single-file test
harness uses a temp path so violations cannot be triggered.

### Example: violation

```yaml
- name: Task in excluded path
  ansible.builtin.debug:
    msg: "this file is under __pycache__"
```

### Example: pass

```yaml
- name: Simple task
  ansible.builtin.debug:
    msg: hello
```
