---
rule_id: R404
validator: native
description: Expose variable_set for the task.
scope: task
status: implemented
---

## Show variables (R404)

Expose variable_set for the task. This is an **informational/debug rule**
intended for development and troubleshooting. **Disabled by default.**

Uses `VariableProvenanceResolver` to expose the full variable set available
to each task, including variable origin (local, play, role_default, etc.).
To avoid persisting secrets through debug/audit surfaces, sensitive variable
**names** are masked via ``var_looks_sensitive``. Scalar **values** are always
emitted as ``[REDACTED]`` (see value redaction limits below). Values under an
effective ``no_log`` scope are also fully redacted.

**Value redaction limits:** scalar values (strings, numbers, booleans) are
always emitted as ``[REDACTED]`` even when benign (for example
``http_port: 8080``). Only dict/list values preserve outer structure, with
leaf scalars still redacted. The ``value`` field therefore carries shape for
collections but no cleartext for scalars — treat R404 as a scope/name debugger,
not a literal value dump.

**Performance note:** each task currently constructs a fresh
``VariableProvenanceResolver`` and re-walks the scope chain. Plays with many
tasks may repeat identical ancestry work; caching resolvers per play ancestry
is a possible future optimization.

### Example: violation

```yaml
- name: Example play
  hosts: localhost
  connection: local
  vars:
    server_port: 443
  tasks:
    - name: Show scope
      ansible.builtin.debug:
        msg: "test"
```

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
