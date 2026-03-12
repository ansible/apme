# Ansible-Lint Coverage

This technical document outlines how a custom inspection engine achieves rule parity with ansible-lint by categorising its own validation logic into specific functional groups. The system employs a multi-tiered strategy for quality control, utilising Open Policy Agent (OPA) for structural analysis, native Python for complex logic, and the Ansible runtime for deep semantic checks. While the framework successfully covers the vast majority of standard linting requirements, it also introduces unique risk-based rules that extend beyond traditional syntax or style enforcement. Ultimately, the text serves as a comprehensive coverage map, demonstrating that while the engine operates independently of ansible-lint's codebase, it maintains a robust and overlapping feature set to ensure playbook integrity.

---

## Rule Coverage vs ansible-lint

Coverage comparison against the ansible-lint codebase (rule id from `src/ansiblelint/rules/` and `_internal/rules.py`). Shows which ansible-lint rules we cover with OPA (L002–L025), native Python (L026–L056), Ansible runtime (L057–L059, M001–M004), or not at all.

### Summary

| Status | Count |
|--------|-------|
| Covered (OPA, native, or Ansible) | 38 |
| Partial / overlap | 4 |
| Not covered | 6 |
| Internal / N/A | 4 |

---

## Covered by Our Rules

### OPA Rules (L002–L025, R118)

| ansible-lint rule | Our rule | Description |
|-------------------|----------|-------------|
| name | L001 | **Removed**: subsumed by L024 |
| fqcn | L002 | Use FQCN for module (syntactic check) |
| play-name | L003 | Play should have a name |
| deprecated-module | L004 | Deprecated module (static list) |
| only-builtins | L005 | Use only ansible.builtin or ansible.legacy |
| command-instead-of-module | L006 | Command used in place of preferred module |
| command-instead-of-shell | L007 | Prefer command when no shell features needed |
| deprecated-local-action | L008 | Do not use local_action |
| empty-string-compare | L009 | Avoid comparison to empty string in when |
| ignore-errors | L010 | Use failed_when or register |
| literal-compare | L011 | Avoid comparison to literal true/false |
| latest / package-latest | L012 | Avoid state=latest; pin versions |
| no-changed-when | L013 | command/shell/raw should have changed_when |
| no-handler | L014 | Use notify/handler pattern |
| no-jinja-when | L015 | Avoid Jinja in when |
| no-prompting | L016 | pause without seconds/minutes |
| no-relative-paths | L017 | Avoid relative path in src |
| partial-become | L018 | become_user should have become |
| playbook-extension | L019 | Playbook .yml/.yaml extension |
| risky-octal | L020 | mode should be string with leading zero |
| risky-file-permissions | L021 | Set mode explicitly |
| risky-shell-pipe | L022 | Shell pipe + set -o pipefail |
| run-once | L023 | Consider run_once appropriateness |
| name[missing] | L024 | Task should have a name |
| name[casing] | L025 | Name should start with uppercase |

### Native Rules (L026–L056)

| ansible-lint rule | Our rule | Description |
|-------------------|----------|-------------|
| fqcn | L026 | Non-FQCN module use (deep, model-based) |
| galaxy | L027 | Role without metadata |
| name | L028 | Task without name |
| command-instead-of-shell | L029 | Prefer command over shell |
| only-builtins | L030 | Non-builtin module use |
| risky-file-permissions | L031 | Insecure file permission |
| no-tabs | L040 | No tabs in YAML |
| key-order | L041 | Key ordering (name before module) |
| complexity | L042 | Play/block task count |
| deprecated-bare-vars | L043 | Deprecated bare variables |
| avoid-implicit | L044 | Avoid implicit state |
| inline-env-var | L045 | Avoid inline environment in tasks |
| no-free-form | L046 | Avoid raw/command/shell without args key |
| no-log-password | L047 | no_log for password params (opt-in) |
| no-same-owner | L048 | copy with remote_src set owner (opt-in) |
| loop-var-prefix | L049 | Loop variable prefix |
| var-naming | L050 | Variable naming conventions |
| jinja | L051 | Jinja spacing |
| galaxy-version-incorrect | L052 | Galaxy version format in meta |
| meta-incorrect | L053 | Role meta structure |
| meta-no-tags | L054 | Role meta galaxy_tags |
| meta-video-links | L055 | Role meta video_links URLs |
| sanity | L056 | Path matches sanity ignore pattern |

### Ansible Runtime Rules (L057–L059, M001–M004)

| ansible-lint rule | Our rule | Description |
|-------------------|----------|-------------|
| syntax-check | L057 | ansible-playbook --syntax-check |
| args | L058 | Argspec validation (docstring-based) |
| args | L059 | Argspec validation (mock/patch-based) |
| fqcn (semantic) | M001 | FQCN resolution via plugin loader |
| deprecated-module (semantic) | M002 | Module deprecation metadata |
| — | M003 | Module redirect (runtime.yml) |
| — | M004 | Removed/tombstoned module |

### Risk Rules (R101–R501, R118)

These are **APME-specific rules** with no direct ansible-lint equivalent. They use the engine's risk annotations:

| Rule | Description |
|------|-------------|
| R101–R117 | Various risk detections (cmd_exec, file_change, package_install, etc.) |
| R118 (OPA) | Inbound transfer from external source |
| R201–R205 | Variable dependency risks |
| R301–R306 | Resolution risks (mapped to L032–L039 for lint equivalents) |
| R401–R404 | Privilege escalation risks |
| R501 | Dangerous module |

---

## Partial / Overlapping Coverage

| ansible-lint rule | Notes |
|-------------------|-------|
| galaxy | L027 checks for missing metadata; ansible-lint validates full meta/main.yml schema and tags |
| meta-runtime | ansible-lint checks meta/runtime.yml; we rely on M001–M004 for runtime resolution instead |
| schema | ansible-lint validates various YAML schemas; we don't run schema validation (L058/L059 cover argspec) |
| role-name | ansible-lint checks role directory naming; L027 checks for metadata presence but not naming conventions |

---

## Not Covered

| ansible-lint rule | Reason |
|-------------------|--------|
| role-argument-spec | Validates meta/argument_spec.yml; schema-heavy, could be added as native rule |
| yaml | ansible-lint wraps yamllint; users can run yamllint separately |
| load-failure | ansible-lint internal; our engine has its own error handling |

---

## Internal / N/A (ansible-lint only)

| ansible-lint rule | Notes |
|-------------------|-------|
| internal-error | ansible-lint internal |
| parser-error | ansible-lint internal |
| load-failure | ansible-lint internal |
| warning | ansible-lint internal |

---

## Strategy

| Validator | Use Case |
|-----------|----------|
| **OPA** | Structural JSON checks (fast, declarative, data-driven) |
| **Native** | Model-walking checks that need Python (variable tracking, heuristics, complex logic) |
| **Ansible** | Checks that require ansible-core runtime (syntax check, argspec, plugin resolution) |

**No ansible-lint adapter** — we implement our own rules walking the ARI model. See `DESIGN_VALIDATORS.md` for rationale.

---

## Reference

- Rule ID mapping: [lint-rule-mapping.md](lint-rule-mapping.md)
- Rule doc format: [rule-doc-format.md](rule-doc-format.md)
- Rule catalog: [rule-catalog.md](rule-catalog.md)

To regenerate this table, compare `../ansible-lint/src/ansiblelint/rules/*.py` against `lint-rule-mapping.md` and the validator rule directories.
