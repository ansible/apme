---
rule_id: L026
validator: native
description: Tasks should use FQCN for modules.
scope: task
---

## Non-FQCN use (L026)

Tasks should use FQCN for modules. Triggers when a short module name resolves to a non-builtin collection module. Requires the target collection to be installed for the resolver to map the short name.

The current native graph finding reports the short module name without a
resolved FQCN target. Its remediation therefore stays for manual review and is
not sent to AI to guess a module name. The shared FQCN transform is available
only to findings from sources that provide a canonical target.

### Example: pass

```yaml
- name: Copy
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
