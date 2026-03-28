---
rule_id: M030
validator: native
description: Conditional expressions that fail Jinja2 parsing will error in 2.23 instead of being silently ignored
scope: task
---

## Broken conditional expressions (M030)

Conditional expressions that fail Jinja2 parsing will error in 2.23 instead of being silently ignored

**Removal version**: 2.23
**Fix tier**: 2
**Audience**: content

### Detection

Parse when: values as Jinja2 expressions and flag parse failures

Parses each `when:` value as a Jinja2 expression and flags parse failures.
Broken conditionals (unmatched parens, invalid syntax) may also cause YAML
parsing issues, making doc integration tests unreliable for this rule.

### Remediation

Depends on specific breakage -- AI review needed
