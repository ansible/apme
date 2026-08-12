---
rule_id: L031
validator: native
description: File permission may be insecure.
scope: task
status: active
---

## Insecure file permission (L031)

Detects insecure file permission values on file-related modules. Complements
OPA rules L020 (numeric mode) and L021 (missing mode) by catching **insecure
permission values** — world-writable bits, overly permissive patterns like
0777/0666, and boolean mode values.

### Detection

Checks the `mode` parameter of file-related modules (`copy`, `file`,
`template`, `lineinfile`, `replace`, `synchronize`, `unarchive`, `assemble`)
for:

- **World-writable** modes (others write bit set, e.g. `0777`, `0646`)
- **Overly permissive** patterns (`0777`, `0666`, `0776`, `0767`, `0677`)
- **Boolean** mode values (which silently produce wrong permissions)
- Templated values (Jinja) are skipped since the actual value is unknown

### Remediation

Use restrictive permissions: `0644` for files, `0755` for directories.

### Example: fail

```yaml
- name: Create world-writable file
  ansible.builtin.file:
    path: /tmp/data
    mode: "0777"
    state: touch
```

### Example: pass

```yaml
- name: Create file with safe permissions
  ansible.builtin.file:
    path: /tmp/data
    mode: "0644"
    state: touch
```
