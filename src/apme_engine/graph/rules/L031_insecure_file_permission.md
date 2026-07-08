---
rule_id: L031
validator: native
description: File permission may be insecure (annotation-based).
scope: task
status: stub
status_reason: >
  Covered by OPA rules L020/L021 which check file mode arguments directly.
  This annotation-based variant is retained as a placeholder for future
  annotation-driven detection (requires engine annotation pipeline).
---

## Insecure file permission (L031)

File permission may be insecure (annotation-based). This rule depends on the
engine annotation pipeline emitting `is_insecure_permissions` on nodes. It is
**not currently implemented** as a GraphRule because the equivalent check is
already covered by OPA rules L020 and L021 which inspect `mode` arguments
directly.

### Status

**Stub** — no `_graph.py` implementation. OPA L020/L021 provide equivalent
coverage. This rule may be implemented in the future if annotation-based
detection offers additional value beyond what OPA checks provide.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
