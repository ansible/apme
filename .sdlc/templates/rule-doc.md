---
rule_id: LXXX
title: Short descriptive title
severity: error
category: lint
validator: native
tags: []
since: 1.0.0
---

# LXXX: Short Descriptive Title

## Summary

One-paragraph description of what this rule detects and why it matters.

## Rationale

Explain the technical or operational reason this rule exists.

## Detection

Describe what patterns or conditions trigger this rule.

## Remediation

Step-by-step guidance on how to fix violations.

## Examples

### Violation

```yaml
# apme: violation LXXX
- name: Example violation
  module_name:
    key: value
```

### Pass

```yaml
# apme: pass LXXX
- name: Example pass
  ansible.builtin.module_name:
    key: value
```

## Related Rules

- [LYYY](lyyy.md) — Related rule description

## References

- [Relevant documentation link]
