---
rule_id: L089
validator: native
description: Plugin Python files should include type hints.
scope: collection
---

## Plugin type hints (L089)

Plugin Python files should include type hints (Python 3.5+) for clarity and static analysis.

### Example: violation

```python
def run(self, terms, variables):
    return terms
```

### Example: pass

```python
def run(self, terms: list, variables: dict) -> list:
    return terms
```
