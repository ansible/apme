---
rule_id: R112
validator: native
description: Taskfile import uses a parameterized path.
scope: task
---

## Parameterized import taskfile (R112)

import_tasks with variable path.

### Example: violation

```yaml
- name: Import tasks
  ansible.builtin.import_tasks: "{{ task_file }}"
```

### Example: pass

```yaml
- name: Import tasks
  ansible.builtin.import_tasks: tasks/main.yml
```
