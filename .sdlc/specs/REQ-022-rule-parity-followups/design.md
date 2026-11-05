# REQ-022 Design: Rule Parity Follow-ups

## Design Summary

Keep APME's existing validator boundaries. Use ansible-core's plugin loader
inside the Ansible validator to emit L037 for unresolved modules. Derive the
R118 annotation in the OPA hierarchy serializer from the existing static
inbound risk profiles, so the Rego rule can retain its current input contract.
Implement selected straightforward parity rules in the graph rule layer.

## Decisions

- L037 remains disabled in the pure graph rule loader; its authoritative
  implementation runs where semantic resolution is available, with the scan
  root as the Ansible subprocess working directory.
- R118 remains an OPA rule. The serializer emits an inbound annotation only
  for a remote or potentially remote source covered by an inbound profile.
  Unarchive requires `remote_src: true`; templates are treated as potentially
  remote, while local paths and `file://` sources are skipped.
- Use separate L IDs for VCS pinning, role-name format, and collection version
  checks; do not broaden existing rules with unrelated semantics.
- Defer full ansible-lint schema validation, general yamllint parity, and
  sanity-ignore-file allowlist validation. Their inputs and coverage require
  broader file-level inspection than the current graph exposes.

## Affected Components

- `src/apme_engine/validators/ansible/rules/M001_M004_introspect.py`
- `src/apme_engine/engine/graph_opa_payload.py`
- `src/apme_engine/graph/rules/`
- `src/apme_engine/validators/ansible/rules/`
- `src/apme_engine/validators/opa/bundle/R118.rego`
- `docs/rules/`

## Risks

- Plugin resolution depends on the session venv containing the target
  collections. Each task's in-scope collection search order is passed to both
  the module and action plugin loaders, and project-level Ansible config is
  loaded from the scan root. Role-specific plugin paths are not inferred per
  task; unavailable plugins are reported as unresolved in that runtime.
- Static risk profiles only describe the module families currently listed by
  APME; they do not provide automatic coverage for arbitrary collections.
- New rules without broader file contents cannot safely claim schema, YAML, or
  sanity-file parity.
