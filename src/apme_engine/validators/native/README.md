# Native (Python) validator

Runs built-in GraphRules over serialized ContentGraph data and static file rules
over the uploaded snapshot. Graph rules live in `apme_engine/graph/rules/`;
the shared Molecule static analysis lives in the neutral
`apme_engine/molecule/` package, with the rule documentation in `rules/`.

File checks run even when no Ansible graph is available. They use the existing
Native Validate RPC and executor, and report ordinary L-series findings.
See [Molecule validation](../../../../docs/guides/MOLECULE.md) and ADR-076.

## Tests

Graph tests construct ContentGraph objects and call match/process, or run the
graph scanner. File tests supply snapshot mappings and validate diagnostics,
RPC adaptation, and session reporting without executing Molecule.

Graph-rule unit tests live under `tests/` (for example
`tests/test_module_options_graph_rules.py`). Run a scoped subset via tox;
`--no-cov` avoids tripping the coverage gate on narrow runs:

```bash
tox -e unit -- --no-cov tests/test_module_options_graph_rules.py -q
```

Molecule rule coverage lives in `tests/test_molecule_validation.py` and
`tests/test_L057_molecule_playbooks.py`.
