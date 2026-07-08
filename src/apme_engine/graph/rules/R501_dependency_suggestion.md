---
rule_id: R501
validator: native
description: Suggest collection/role dependency.
scope: collection
status: planned
status_reason: >
  Advisory rule requiring collection dependency resolution context. The rule
  needs access to the Galaxy/collection index to suggest which collection
  provides an unresolved module. Planned for future implementation.
---

## Dependency suggestion (R501)

Suggest collection/role dependency for unresolved modules with possible
candidates. This is an **advisory/suggestion rule** that would recommend
adding collection dependencies when unresolved modules have known candidates.

### Status

**Planned** — no `_graph.py` implementation yet. This rule requires access to
a collection index (Galaxy metadata or local collection cache) to map
unresolved module names to their providing collections. The single-file
test harness resolves short names via builtin lookup, making it difficult
to test without multi-collection fixtures.

### Example: pass

```yaml
- name: Copy file with FQCN
  ansible.builtin.copy:
    src: files/config.yml
    dest: /etc/config.yml
```
