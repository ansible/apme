# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **L025** — Check named task blocks. Standalone task files now reach all
  applicable OPA rules, so existing projects may receive additional findings
  and remediation changes ([#880](https://github.com/ansible/apme/issues/880)).

- **M047** — Flag stale top-level `disable_lookups` keys in Constructable inventory
  YAML (removed in ansible-core 2.23). Python `_compose(disable_lookups=...)`
  in custom inventory plugins remains planned in REQ-018 as **M040**.
- **M048** — Prefer `ansible.builtin` when a non-builtin FQCN has a builtin
  equivalent, resolved authoritatively via the session venv's
  `find_plugin_with_context()` ([#714](https://github.com/ansible/apme/issues/714)).
  Covers already-FQCN prefer-builtin cases (e.g. `community.general.copy`)
  without restoring L030's static `builtin-modules.txt` check. Modules that
  left core (e.g. `mount` → `ansible.posix`) are not flagged.

- **L115–L119** — Static Molecule validation: legacy `provisioner:` and native
  `ansible:`/shared-state configuration checked against bundled Molecule
  profiles (24.9.0 and 26.11.0), scenario playbook/import/inventory references,
  lifecycle actions, and play `module_defaults` structure, without running
  Molecule or reading anything outside the uploaded snapshot.
  Ansible `L057` leaves Molecule scenario content to these checks and no longer
  runs runtime syntax checks there. Findings carry unexpanded source lines for
  `# noqa` and fingerprint-based suppression.

### Changed

- `apme format` preserves every YAML `name` value. Use `apme remediate` with
  L025 enabled to capitalize play, task, handler, and block labels. CI workflows
  that used formatting to satisfy L025 must run remediation explicitly.
- `apme check` can now report enabled L025 findings for lowercase labels that
  the formatting phase previously capitalized before validation.

### Removed

- **L030** — Static builtin-module list check retired; short names are covered by
  Ansible validator **M001**, `community.*` by OPA **L005**, and fully qualified
  modules with builtin twins by Ansible validator **M048** ([#714](https://github.com/ansible/apme/issues/714)).

### Fixed

- Preserve collection, variable, and dependency identifiers during formatting
  and exclude list-shaped data from task discovery outside known task contexts.
- Retain role-local custom modules under `includes/` and collection modules in
  nested plugin directories during task discovery ([#883](https://github.com/ansible/apme/issues/883)).
- Share role task-source classification with formatting so inline custom tasks
  in role includes receive separate source lines before remediation; keep data
  and fixture lists in flow style. Explicit supplemental task directories use
  their own discovery boundary, including paths outside the project root.
  Requirements-shaped entries remain data in role test sources even when test
  contents are enabled; actual task files named `requirements.yml` still load.
- Report conflicting source patches through `remediation_failed` session events
  in initial remediation and both approval gates instead of aborting the RPC
  with an invalid-argument status.
- Keep post-splice formatting consistent across initial remediation, approvals,
  and AI gates. Concurrent approval requests cannot consume the same proposals;
  disconnected approvals finish persistence and preserve their next gate for
  resume, while duplicate approvals replay the recorded decision. Failed
  approval writes preserve proposals for retry, including partial batches.
  Slow completion reporting no longer delays the close handshake that lets
  the CLI write its buffered patches. Approval requests now echo the offered
  `approval_token` so delayed empty decisions cannot reject a later gate.
  Recoverable request errors keep the interactive WebSocket session usable.
  Legacy tokenless empty requests after the first completed gate must refresh
  proposals and include their token.
- Recognize collection-qualified `import_playbook` directives when formatting
  playbook lists, avoiding task-only blank lines between imports.
- Preserve independent play/block and child fixes during source assembly;
  preserve independent sibling edits at touching boundaries, merge compatible
  overlapping source changes, and reject conflicting or
  ambiguous replacements instead of silently overwriting fixes.
  Preserve the last approved snapshot when pending progression history rolls
  over so approved fixes survive later unapproved attempts.
- L025 ignores uncased prefixes such as digits and punctuation, and its
  transform reports success only when the name actually changes.

- **M005** — No longer flags registered variables in `ansible.builtin.assert`
  `that` conditions (boolean tests, not re-templating sinks); `fail_msg` /
  `success_msg` remain in scope ([#581](https://github.com/ansible/apme/issues/581)).
- **L110** — Play-scoped `no_log` resolution for shared includes; requires
  protection on every enclosing play and include path
  ([#593](https://github.com/ansible/apme/issues/593)).
- **R402** — Play-scoped variable provenance for tasks reached via shared
  includes ([#592](https://github.com/ansible/apme/issues/592)).
- **L039, L032, M026, L034, R404, L047, L110** — Resolve shared-task variable
  and `no_log` context independently for each enclosing play and include path;
  L047 and L110 require protection across every execution context
  ([#748](https://github.com/ansible/apme/issues/748)).
- **M005** — For `that: []`, scan rendered `success_msg` but not the
  unrendered `fail_msg`; skip module messages when required `that` is missing
  ([#749](https://github.com/ansible/apme/issues/749)).
