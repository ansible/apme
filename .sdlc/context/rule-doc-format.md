# Rule Documentation Format

This document defines the standardized Markdown format for rule documentation in APME. Rule documents serve two purposes:

1. **Human-readable documentation**: Describes what the rule detects, why it matters, and how to fix it
2. **Integration testing**: Embedded code snippets enable automated verification that rules fire correctly

---

## File Locations

Each validator maintains its rule documentation in a dedicated directory:

| Validator | Rule IDs | Documentation Path |
|-----------|----------|-------------------|
| Native | L026–L056, R101–R501 | `docs/rules/native/` |
| OPA | L002–L025, R118 | `docs/rules/opa/` |
| Ansible | L057–L059, M001–M004, P001–P004 | `docs/rules/ansible/` |
| Gitleaks | SEC:* | `docs/rules/gitleaks/` |

**File naming**: `{rule_id}.md` in lowercase (e.g., `l026.md`, `r101.md`, `sec-aws-access-key.md`)

---

## Document Structure

Every rule document follows this structure:

```markdown
---
rule_id: L026
title: Short descriptive title
severity: error | warning | info
category: lint | modernize | risk | policy | secrets
validator: native | opa | ansible | gitleaks
tags: [fqcn, modules, deprecated]
since: 1.0.0
---

# L026: Short Descriptive Title

## Summary

One-paragraph description of what this rule detects and why it matters.

## Rationale

Explain the technical or operational reason this rule exists. Reference AAP 2.5+
requirements, security best practices, or Ansible recommendations.

## Detection

Describe what patterns or conditions trigger this rule.

## Remediation

Step-by-step guidance on how to fix violations.

## Examples

### Violation

```yaml
# apme: violation L026
- name: Copy file
  copy:
    src: /tmp/foo
    dest: /tmp/bar
```

### Pass

```yaml
# apme: pass L026
- name: Copy file
  ansible.builtin.copy:
    src: /tmp/foo
    dest: /tmp/bar
```

## Related Rules

- [L027](l027.md) — Related rule description
- [R101](../native/r101.md) — Another related rule

## References

- [Ansible documentation link]
- [AAP 2.5 migration guide]
```

---

## YAML Frontmatter

The frontmatter provides structured metadata for indexing and filtering:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rule_id` | string | Yes | Unique rule identifier (e.g., L026, R101, SEC:aws-access-key) |
| `title` | string | Yes | Short descriptive title (< 80 chars) |
| `severity` | enum | Yes | `error`, `warning`, or `info` |
| `category` | enum | Yes | `lint`, `modernize`, `risk`, `policy`, or `secrets` |
| `validator` | enum | Yes | `native`, `opa`, `ansible`, or `gitleaks` |
| `tags` | list | No | Searchable tags for filtering |
| `since` | string | No | Version when rule was introduced |
| `deprecated` | boolean | No | Set to `true` if rule is deprecated |
| `superseded_by` | string | No | Rule ID that replaces this one |

---

## Example Snippets

Example snippets are the core of integration testing. Each snippet must include a magic comment that declares the expected outcome.

### Magic Comment Format

```yaml
# apme: <outcome> <rule_id> [message_pattern]
```

| Outcome | Description |
|---------|-------------|
| `violation` | Rule MUST fire on this snippet |
| `pass` | Rule MUST NOT fire on this snippet |

The optional `message_pattern` is a regex to match against the violation message.

### Snippet Requirements

1. **Self-contained**: Each snippet must be valid YAML that can be parsed independently
2. **Minimal**: Include only the code necessary to demonstrate the violation/pass
3. **Annotated**: Every snippet needs exactly one magic comment
4. **Realistic**: Use plausible Ansible patterns, not contrived examples

### Multiple Violations

For rules that can fire multiple times in a single file:

```yaml
# apme: violation L026 "copy"
# apme: violation L026 "file"
- name: Multiple issues
  block:
    - copy:
        src: /tmp/foo
        dest: /tmp/bar
    - file:
        path: /tmp/baz
        state: directory
```

### Edge Cases

Document edge cases that should NOT trigger the rule:

```yaml
# apme: pass L026 "Jinja module lookup should not trigger"
- name: Dynamic module
  "{{ dynamic_module }}":
    key: value
```

---

## Test Runner Integration

The example snippets enable automated integration testing without maintaining separate test fixtures.

### Parser (`tests/rule_doc_parser.py`)

Extracts test cases from rule documentation:

```python
@dataclass
class RuleTestCase:
    rule_id: str
    snippet: str
    expected_outcome: Literal["violation", "pass"]
    message_pattern: str | None
    source_file: Path
    line_number: int

def parse_rule_doc(path: Path) -> list[RuleTestCase]:
    """Extract test cases from a rule documentation file."""
    ...

def discover_rule_docs(base_path: Path) -> list[Path]:
    """Find all rule documentation files."""
    ...
```

### Integration Test (`tests/rule_doc_integration_test.py`)

Parameterized pytest that runs each snippet through the appropriate validator:

```python
@pytest.fixture
def rule_test_cases() -> list[RuleTestCase]:
    """Discover and parse all rule documentation."""
    docs_path = Path("docs/rules")
    cases = []
    for doc_path in discover_rule_docs(docs_path):
        cases.extend(parse_rule_doc(doc_path))
    return cases

@pytest.mark.parametrize("case", rule_test_cases())
def test_rule_documentation(case: RuleTestCase, validator_client):
    """Verify rule fires/doesn't fire as documented."""
    result = validator_client.validate(case.snippet)

    if case.expected_outcome == "violation":
        violations = [v for v in result.violations if v.rule_id == case.rule_id]
        assert len(violations) > 0, f"Expected {case.rule_id} to fire"
        if case.message_pattern:
            assert any(re.search(case.message_pattern, v.message) for v in violations)
    else:
        violations = [v for v in result.violations if v.rule_id == case.rule_id]
        assert len(violations) == 0, f"Expected {case.rule_id} NOT to fire"
```

### CI Integration

Add to the test suite in CI:

```yaml
# .github/workflows/test.yml
- name: Rule documentation tests
  run: pytest tests/rule_doc_integration_test.py -v
```

---

## Validation Checklist

Before committing rule documentation:

- [ ] YAML frontmatter is valid and complete
- [ ] At least one `# apme: violation` example exists
- [ ] At least one `# apme: pass` example exists
- [ ] All snippets are valid YAML
- [ ] `pytest tests/rule_doc_integration_test.py -k {rule_id}` passes
- [ ] Remediation guidance is actionable
- [ ] Related rules are linked

---

## Example Document

Complete example for rule L026 (FQCN required):

```markdown
---
rule_id: L026
title: Module requires fully qualified collection name (FQCN)
severity: error
category: lint
validator: native
tags: [fqcn, modules, aap-2.5]
since: 1.0.0
---

# L026: Module requires fully qualified collection name (FQCN)

## Summary

Ansible modules must use fully qualified collection names (FQCN) for AAP 2.5+
compatibility. Short module names like `copy` are ambiguous and may resolve
incorrectly when multiple collections provide modules with the same name.

## Rationale

AAP 2.5 deprecates implicit collection resolution. Using FQCNs ensures:
- Deterministic module resolution across environments
- Explicit dependency declaration
- Compatibility with execution environments

## Detection

This rule fires when a task uses a module name that:
1. Does not contain a dot (`.`)
2. Is not a Jinja2 expression
3. Maps to a known ansible.builtin or collection module

## Remediation

Replace short module names with their FQCN equivalent:

| Short Name | FQCN |
|------------|------|
| `copy` | `ansible.builtin.copy` |
| `file` | `ansible.builtin.file` |
| `yum` | `ansible.builtin.yum` |

Use `apme scan --fix` to auto-remediate.

## Examples

### Violation

```yaml
# apme: violation L026 "copy"
- name: Copy configuration file
  copy:
    src: app.conf
    dest: /etc/app/app.conf
    mode: "0644"
```

### Pass

```yaml
# apme: pass L026
- name: Copy configuration file
  ansible.builtin.copy:
    src: app.conf
    dest: /etc/app/app.conf
    mode: "0644"
```

### Pass (Jinja2 module)

```yaml
# apme: pass L026 "Dynamic module should not trigger"
- name: Dynamic module execution
  "{{ module_name }}":
    key: value
```

## Related Rules

- [L027](l027.md) — Collection prefix required for roles
- [M001](../ansible/m001.md) — Deprecated module replacement

## References

- [Ansible FQCN documentation](https://docs.ansible.com/ansible/latest/collections_guide/collections_using_playbooks.html)
- [AAP 2.5 Migration Guide](https://access.redhat.com/documentation/en-us/red_hat_ansible_automation_platform/2.5)
```

---

## Related Documents

- [ADR-008: Rule ID Conventions](/.sdlc/adrs/ADR-008-rule-id-conventions.md) — Rule ID prefix meanings
- [conventions.md](conventions.md) — General coding and documentation standards
- [architecture.md](architecture.md) — Validator service architecture
