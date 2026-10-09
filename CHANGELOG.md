# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **M047** — Flag stale top-level `disable_lookups` keys in Constructable inventory
  YAML (removed in ansible-core 2.23). Python `_compose(disable_lookups=...)`
  in custom inventory plugins remains REQ-018 **M040**.
- **M048** — Prefer `ansible.builtin` when a non-builtin FQCN has a builtin
  equivalent, resolved authoritatively via the session venv's
  `find_plugin_with_context()` ([#714](https://github.com/ansible/apme/issues/714)).
  Covers already-FQCN prefer-builtin cases (e.g. `community.general.copy`)
  without restoring L030's static `builtin-modules.txt` check. Modules that
  left core (e.g. `mount` → `ansible.posix`) are not flagged.

### Removed

- **L030** — Static builtin-module list check retired; short names are covered by
  Ansible validator **M001**, `community.*` by OPA **L005**, and fully qualified
  modules with builtin twins by Ansible validator **M048** ([#714](https://github.com/ansible/apme/issues/714)).

### Fixed

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
