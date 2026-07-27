# Rule description document format

Rule `.md` files describe a single rule and provide examples that can be used both for documentation and for **integration tests**. The structure is standardized so a test runner can parse each file and assert that the engine produces the expected result (violation or pass) for each example.

## File location

- **Native rules:** `src/apme_engine/validators/native/rules/<rule_file_stem>.md` (e.g. `R102_command_instead_of_shell.md` next to `R102_command_instead_of_shell.py`).
- **OPA rules:** `src/apme_engine/validators/opa/bundle/<rule_id>.md` (e.g. `L002.md`, `R118.md` next to the `.rego` file).
- **Ansible rules:** `src/apme_engine/validators/ansible/rules/<rule_id>.md` (e.g. `L057.md`, `M001.md`).

## Structure

1. **YAML frontmatter** (optional but recommended for test harness)
   - `rule_id` — Rule identifier (e.g. `L026`, `R102`, `L024`).
   - `validator` — `native`, `opa`, or `ansible`.
   - `description` — One-line description.
   - `scope` — Structural scope per ADR-026: `task`, `block`, `play`, `playbook`, `role`, `inventory`, or `collection`. Drives remediation routing.
   - `ai_prompt` — *(optional)* Per-rule guidance injected into the AI remediation prompt. Use YAML literal block (`|`) for multiline text. Tells the AI how to handle this rule — e.g., when to add `# noqa` instead of modifying code, or domain context about when the flagged pattern is legitimate. See `src/apme_engine/remediation/abbenay_provider.py` for how hints are loaded and injected.

   **Note:** Default severity is **not** set in rule docs. It comes from
   `src/apme_engine/severity_defaults.py` (ADR-043) and appears in
   [RULE_CATALOG.md](RULE_CATALOG.md).

2. **Title and prose** — Human-readable explanation of what the rule checks.

3. **Examples** — Exactly two section types, each followed by a single fenced code block:
   - `### Example: violation` — Content that **must** trigger this rule. Integration test: run the snippet; assert the rule reports a violation.
   - `### Example: pass` — Content that **must not** trigger this rule. Integration test: run the snippet; assert the rule does not report a violation.

## Requirements for examples

- Each example is a **single fenced code block** (use `yaml` as the language).
- The content must be **valid Ansible YAML** that the engine can parse (e.g. a list of tasks, a play, or a minimal playbook/role layout as required by the rule).
- Snippets should be **minimal** but **self-contained** so they can be run in isolation (e.g. a task list under `tasks:` or a full play with `hosts` and `tasks`).
- Use **consistent** section headers so a parser can find them:
  - `### Example: violation`
  - `### Example: pass`

## Example document

```markdown
---
rule_id: L026
validator: native
description: Tasks should use FQCN for modules.
---

## Non-FQCN module use (L026)

Tasks should use fully qualified collection names (FQCN) for modules instead of short names.

### Example: violation

```yaml
- name: Install package
  yum:
    name: httpd
```

### Example: pass

```yaml
- name: Install package
  ansible.builtin.yum:
    name: httpd
```
```

## Integration test usage

A test runner is provided in `tests/rule_doc_integration_test.py`. It uses `tests/rule_doc_parser.py` to discover and parse rule `.md` files.

The runner:

1. Discovers all rule `.md` files (per validator) via `discover_rule_docs(native_rules_dir, opa_bundle_dir)`.
2. Parses frontmatter to get `rule_id` and `validator`.
3. For each `### Example: violation` section: extract the next fenced code block, run the engine on it, and assert that the reported violations include this `rule_id`.
4. For each `### Example: pass` section: extract the next fenced code block, run the engine on it, and assert that no violation is reported for this `rule_id`.

Code blocks must be valid YAML (and valid Ansible structures) so the engine can load and evaluate them without error. If the scan fails (e.g. engine dependency or bug), the test is skipped so the suite still passes.
