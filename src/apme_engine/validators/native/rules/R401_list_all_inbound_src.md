---
rule_id: R401
validator: native
description: Report inbound transfer sources.
---

## List inbound sources (R401)

Report inbound transfer sources (annotation-based). Lists tasks with INBOUND annotation at end of play. Depends on annotator.

### Example: pass

```yaml
- name: Debug message
  ansible.builtin.debug:
    msg: hello
```
