# OPA rule bundle

Rules are split into one file per rule for consistency and easier review.

## Layout

- **`_helpers.rego`** — Shared helpers used by several rules (e.g. `short_module_name`, `cmd_shell_modules`, `file_permission_modules`). Do not remove; other rules depend on it.
- **`L003.rego` … `L025.rego`** — Lint rules. One rule per file. Each adds to the `violations` set. (L001 subsumed by L024; L002 subsumed by M001 in the Ansible validator.)
- **`M006.rego`, `M008.rego`, `M009.rego`, `M011.rego`** — Migration rules for ansible-core 2.19/2.20. See `docs/ANSIBLE_CORE_MIGRATION.md`.
- **`R118.rego`** — Risk rule (inbound transfer, annotation-based).
- **`*_test.rego`** — Colocated integration tests. One `_test.rego` next to each rule.
- **`data.json`** — Bundle data (e.g. `data.apme.ansible`: deprecated_modules, command_to_module, etc.). Required for L004, L006, L012, L013, L017, L020, L021.

All rule files use package `apme.rules`. Tests use package `apme.rules_test` and import `data.apme.rules`.

## Running OPA tests

**From pytest (recommended):** Run the OPA Rego tests via Podman or local `opa`:

```bash
pytest tests/test_opa_client.py::TestRunOpaTest::test_opa_bundle_rego_tests_pass -v
```

Uses `run_opa_test()` from `apme_engine.opa_client` (Podman by default; set `OPA_USE_PODMAN=0` for local `opa`).

**Manually** with the [OPA binary](https://www.openpolicyagent.org/docs/latest/#running-opa) on your PATH:

```bash
cd src/apme_engine/validators/opa/bundle
opa test . -v
```

With Podman (same flags as the engine: `--userns=keep-id`, `-u root`, `:ro,z` on the volume):

```bash
cd src/apme_engine/validators/opa/bundle
podman run --rm --userns=keep-id -u root -v "$(pwd):/bundle:ro,z" docker.io/openpolicyagent/opa:latest test /bundle -v
```

This runs all `*_test.rego` files against the policy and data in this directory.

## Entrypoint

The CLI and validator evaluate the entrypoint **`data.apme.rules.violations`**. Violations are produced by each Lxxx.rego file and merged by the bundle.
