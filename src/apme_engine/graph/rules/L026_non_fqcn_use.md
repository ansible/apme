---
rule_id: L026
validator: native
description: Tasks should use FQCN for modules.
scope: task
---

## Non-FQCN use (L026)

Tasks should use FQCN for modules. Triggers when a short module name resolves to a non-builtin collection module. Requires the target collection to be installed for the resolver to map the short name.

Deterministic remediation uses a resolved FQCN supplied by the Ansible plugin
loader. If no canonical target is available, the finding stays for manual
review and is not sent to AI for a guessed module name.

### Example: pass

```yaml
- name: Copy
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
