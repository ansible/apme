---
rule_id: L026
validator: native
description: Tasks should use FQCN for modules.
scope: task
---

## Non-FQCN use (L026)

Tasks should use FQCN for modules. Triggers when a task or handler declares a
short module name, including builtin and collection modules.

During remediation, the finding receives the canonical target from the Ansible
validator's M001/M003 result for the same file, task, and original module name.
The resolver uses the session's ansible-core, installed collections and task
collection search order. It follows aliases and redirects and verifies that
the qualified name selects the same module and action plugin files before
offering an automatic fix. No maintained module-name mapping is used.

Missing dependencies, dynamic names and names without an equivalent qualified
spelling remain for manual review. Project-local plugins participate when
project-code trust is enabled; local overrides use `ansible.legacy` when that
preserves the selected plugins. AI does not invent missing module names.

### Example: pass

```yaml
- name: Copy
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```
