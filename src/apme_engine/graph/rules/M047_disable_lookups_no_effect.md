---
rule_id: M047
validator: native
description: Inventory plugin disable_lookups argument has no effect (removed in 2.23)
scope: inventory
ansible_core_version: ">=2.23"
---

## disable_lookups has no effect (M047)

In ansible-core 2.23 the `disable_lookups` argument to inventory plugin
`_compose()` is a no-op (deprecated in 2.22, removed in 2.23). Inventory
plugin configs that still pass it should drop the key.

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Scans Constructable inventory plugin configs (`plugin: constructed` or
`plugin: ansible.builtin.constructed` in `inventory.yml`,
`inventory/constructed.yml`, …) adjacent to playbooks for a stale top-level
`disable_lookups` key. Static inventories with a group named
`disable_lookups` are not flagged. Python `_compose(disable_lookups=...)`
calls in plugin source are covered by REQ-018 rule M040, not this rule.

Sample inventory plugin config with the deprecated argument:

```yaml
plugin: ansible.builtin.constructed
disable_lookups: true
compose:
  ansible_host: ansible_host | default(inventory_hostname)
```

The same file without the argument passes:

```yaml
plugin: ansible.builtin.constructed
compose:
  ansible_host: ansible_host | default(inventory_hostname)
```

### Remediation

Delete the `disable_lookups` key; template composition behaves identically
without it.

See also REQ-018 rule M040 (`constructable_disable_lookups`), which covers
the same deprecated argument in Python plugin source via AST analysis.
