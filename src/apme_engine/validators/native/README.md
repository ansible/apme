# Native (Python) validator

Runs in-tree Python rules on `context.scandata` from the engine. Rules live in `rules/`.

## Colocated tests

Each rule can have a colocated test file: `*_test.py` next to the rule module (e.g. `R301_non_fqcn_use_test.py` next to `R301_non_fqcn_use.py`). Tests use **Python objects**, not Ansible YAML:

- **`_test_helpers.py`** in `rules/` provides `make_task_spec`, `make_task_call`, `make_role_spec`, `make_role_call`, and `make_context` to build minimal engine objects.
- Tests instantiate the rule class, build a context with the helper, then call `rule.match(ctx)` and `rule.process(ctx)` and assert on the result.

Graph-rule unit tests live under `tests/` (for example
`tests/test_module_options_graph_rules.py`). Run a scoped subset via tox;
`--no-cov` avoids tripping the coverage gate on narrow runs:

```bash
tox -e unit -- --no-cov tests/test_module_options_graph_rules.py -q
```

To add a test for a new rule: create `Rxxx_rule_name_test.py`, import the rule class and helpers, and add tests that cover “fires when” and “does not fire when” cases.
