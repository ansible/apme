# Lint Rule Mapping

This documentation outlines a comprehensive standardised numbering system for Ansible code validation, where rule IDs are defined by the category of the check rather than the specific technical tool performing it. The framework transitions from legacy identifiers to a structured taxonomy including Lint (L) for style and best practices, Modernise (M) for maintaining compatibility with current ansible-core versions, and Risk (R) for security considerations. These rules are implemented through diverse methods, ranging from static syntactic analysis using OPA to dynamic runtime execution that captures accurate module metadata. Ultimately, this mapping serves as a migration guide and technical reference, ensuring that developers can consistently track, filter, and resolve policy violations across different validation engines.

---

## Rule IDs — Cross-Mapping

Rule IDs categorize the **type of check**, not the validator that runs it. The validator is an implementation detail.

| Prefix | Category | Notes |
|--------|----------|-------|
| **L** | Lint (style, correctness, best practice) | Any validator can implement an L rule |
| **M** | Modernize (keep code current with ansible-core) | Uses ansible-core runtime metadata |
| **R** | Risk/security (annotation-based) | Any validator can implement an R rule |
| **P** | Policy/validation (legacy) | Superseded by L058/L059 |

> **Note**: `src/apme_engine/validators/native/rules/rule_versions.json` includes entries for both previous (R301, R302, ...) and current (L026, L027, ...) IDs for the renumbered rules; the loader uses the current `rule_id` (L###) when looking up version info.

---

## OPA (Rego) Rules — L002-L025, R118

L001 was removed — its scope was limited to shell tasks without names, which is a strict subset of L024 (all tasks should have a name).

| Previous rule_id (OPA) | New ID | Description |
|------------------------|--------|-------------|
| task-name | L001 | **Removed**: subsumed by L024 |
| fqcn | L002 | Use FQCN for module (syntactic check) |
| play-name | L003 | Play should have a name |
| deprecated-module | L004 | Deprecated module (static list) |
| only-builtins | L005 | Use only ansible.builtin or ansible.legacy |
| command-instead-of-module | L006 | Command used in place of preferred module |
| command-instead-of-shell | L007 | Prefer command when no shell features needed |
| deprecated-local-action | L008 | Do not use local_action; use delegate_to: localhost |
| empty-string-compare | L009 | Avoid comparison to empty string in when |
| ignore-errors | L010 | Use failed_when or register instead of ignore_errors |
| literal-compare | L011 | Avoid comparison to literal true/false in when |
| latest | L012 | Avoid state=latest; pin package versions |
| no-changed-when | L013 | command/shell/raw should have changed_when or creates/removes |
| no-handler | L014 | Use notify/handler instead of when: result.changed |
| no-jinja-when | L015 | Avoid Jinja in when; use variables |
| no-prompting | L016 | pause without seconds/minutes prompts for input |
| no-relative-paths | L017 | Avoid relative path in src |
| partial-become | L018 | become_user should have a corresponding become |
| playbook-extension | L019 | Playbook should have .yml or .yaml extension |
| risky-octal | L020 | mode should be string with leading zero |
| risky-file-permissions | L021 | Consider setting mode explicitly for file/copy/template |
| risky-shell-pipe | L022 | Shell with pipe should set set -o pipefail |
| run-once | L023 | Consider whether run_once is appropriate |
| name[missing] | L024 | Task should have a name |
| name[casing] | L025 | Task/play name should start with uppercase |
| (new) | R118 | Inbound transfer (annotation-based, any external source) |

---

## Native (Python) Rules — L026-L039

| Previous rule_id (ARI) | New ID | File | Description |
|------------------------|--------|------|-------------|
| R301 | L026 | `L026_non_fqcn_use.py` | Non-FQCN module use |
| R302 | L027 | `L027_role_without_metadata.py` | Role without metadata |
| R303 | L028 | **Removed** — duplicate of L024 (OPA) | Task without name |
| R102 | L029 | **Removed** — duplicate of L007 (OPA) | Prefer command over shell |
| R110 | L030 | `L030_non_builtin_use.py` | Non-builtin module use |
| R116 | L031 | `L031_insecure_file_permission.py` | Insecure file permission |
| R201 | L032 | `L032_changed_data_dependence.py` | Changed data dependence |
| R202 | L033 | `L033_unconditional_override.py` | Unconditional override |
| R203 | L034 | `L034_unused_override.py` | Unused override |
| R204 | L035 | `L035_unnecessary_set_fact.py` | Unnecessary set_fact |
| R205 | L036 | `L036_unnecessary_include_vars.py` | Unnecessary include_vars |
| R304 | L037 | `L037_unresolved_module.py` | Unresolved module |
| R305 | L038 | `L038_unresolved_role.py` | Unresolved role |
| R306 | L039 | `L039_undefined_variable.py` | Undefined variable |

---

## New Native Lint Rules — L040-L056

| Rule ID | File | Description |
|---------|------|-------------|
| L040 | `L040_no_tabs.py` | No tabs in YAML |
| L041 | `L041_key_order.py` | Key ordering (e.g. name before module) |
| L042 | `L042_complexity.py` | Play/block task count (complexity) |
| L043 | `L043_deprecated_bare_vars.py` | Deprecated bare variables `{{ foo }}` |
| L044 | `L044_avoid_implicit.py` | Avoid implicit state (set state explicitly) |
| L045 | `L045_inline_env_var.py` | Avoid inline environment in tasks |
| L046 | `L046_no_free_form.py` | Avoid raw/command/shell without args key |
| L047 | `L047_no_log_password.py` | no_log for password-like params (opt-in, disabled by default) |
| L048 | `L048_no_same_owner.py` | copy with remote_src set owner (opt-in, disabled by default) |
| L049 | `L049_loop_var_prefix.py` | Loop variable prefix (e.g. `item_`) |
| L050 | `L050_var_naming.py` | Variable naming (lowercase, underscores) |
| L051 | `L051_jinja.py` | Jinja spacing `{{ var }}` |
| L052 | `L052_galaxy_version_incorrect.py` | Galaxy version format in meta |
| L053 | `L053_meta_incorrect.py` | Role meta structure |
| L054 | `L054_meta_no_tags.py` | Role meta galaxy_tags |
| L055 | `L055_meta_video_links.py` | Role meta video_links URLs |
| L056 | `L056_sanity.py` | Path matches sanity ignore pattern |

---

## Ansible Validator Rules — L057-L059

These rules require an Ansible runtime (pre-built venv with ansible-core). They run in the Ansible validator service.

| Rule ID | Description | Method |
|---------|-------------|--------|
| L057 | Syntax check (`ansible-playbook --syntax-check`) | subprocess |
| L058 | Argspec validation (docstring-based: parses DOCUMENTATION string) | subprocess |
| L059 | Argspec validation (mock/patch-based: captures real argument_spec) | subprocess |

**L058 and L059** both check module arguments but use different extraction methods:

| Rule | Method | Characteristics |
|------|--------|-----------------|
| L058 | Parses the module's `DOCUMENTATION` string | Safe (no code execution), fast, but may drift from actual argument_spec |
| L059 | Patches `AnsibleModule.__init__` and calls `module.main()` to capture the real argument_spec | More accurate, catches `mutually_exclusive`/`required_together`/`required_if`, but executes module import code |

Both can run simultaneously; each has a unique rule ID so users can enable/disable independently.

---

## Modernize Rules — M001-M004 (Ansible Validator)

These rules use ansible-core's plugin loader (`find_plugin_with_context()`) to resolve modules against the actual runtime metadata (`ansible_builtin_runtime.yml` and collection `meta/runtime.yml`). They stay current with whichever ansible-core version is in the venv.

| Rule ID | Description |
|---------|-------------|
| M001 | FQCN resolution — module resolved to a different canonical FQCN |
| M002 | Deprecated module — module has deprecation metadata |
| M003 | Module redirect — module name was redirected to a new FQCN |
| M004 | Removed module — tombstoned module (raises `AnsiblePluginRemovedError`) |

> **Note**: OPA L002 also checks for non-FQCN module names but is purely syntactic (counts dot separators). M001 is semantic — it actually resolves the module via ansible-core's plugin loader. Both can fire for the same task (different rule IDs, complementary checks). M001 also works for third-party collections.

---

## Migration Rules — M005-M013 (ansible-core 2.19/2.20)

These rules detect patterns that break or behave differently under ansible-core 2.19 and 2.20. See `ANSIBLE_CORE_MIGRATION.md` for full details.

| Rule ID | Validator | Description | Fixable |
|---------|-----------|-------------|---------|
| M005 | native | Data tagging — registered var in Jinja template (untrusted in 2.19+) | Tier 2 (AI) |
| M006 | OPA | become + ignore_errors misses timeout (UNREACHABLE in 2.19+) | Yes — adds `ignore_unreachable: true` |
| M008 | OPA | Bare include: removed in 2.19+ | Yes — rewrites to `include_tasks:` |
| M009 | OPA | with_* loops deprecated | Yes — rewrites simple cases to `loop:` |
| M010 | native | Python 2 interpreter path (dropped in 2.18+) | Tier 3 (manual) |
| M011 | OPA | Network collection modules may need upgrade for 2.19+ | Tier 3 (informational) |
| — | — | M007 (nested var filters), M012 (error string parsing), M013 (smart transport) | Planned |

---

## Deprecation Rules — M014-M030 (ansible-core 2.21-2.24)

These rules detect patterns deprecated in upcoming ansible-core releases.

| Rule ID | Validator | Description | Overlap |
|---------|-----------|-------------|---------|
| M014 | native | Top-level ansible_* fact variables deprecated; use `ansible_facts['name']` | L076 |
| M015 | native | `play_hosts` magic variable deprecated; use `ansible_play_batch` | — |
| M016 | OPA | Empty `when:` on task is deprecated and will error in 2.23 | — |
| M017 | OPA | `action:` key with mapping value deprecated | — |
| M018 | OPA | Paramiko connection plugin deprecated; use `connection: ssh` | — |
| M019 | native | `!!omap` / `!!pairs` YAML tags deprecated | — |
| M020 | native | `!vault-encrypted` YAML tag deprecated | — |
| M021 | OPA | Empty `args:` on task deprecated | — |
| M022 | native | `tree` / `oneline` callback plugins removed in 2.23 | — |
| M023 | OPA | String booleans for `follow_redirects` deprecated; use true/false | L061 |
| M024 | OPA | `include_vars` `ignore_files` string form deprecated; use a list | — |
| M025 | OPA | Third-party strategy plugins deprecated; use `ansible.builtin` strategies | — |
| M026 | native | Invalid inventory variable names (non-identifier) | L050 |
| M027 | native | Legacy `k=v` inline args merged with `args:` mapping | L046, L062 |
| M028 | OPA | `first_found` auto-split on colon deprecated; use list syntax | — |
| M029 | native | Inventory script missing `_meta` key (disabled — requires runtime) | — |
| M030 | native | Broken conditional expressions (Jinja2 syntax errors in `when:`) | — |

---

## Good-Practices OPA Rules — L061-L072

These rules enforce recommendations from the automation-good-practices documentation.

| Rule ID | File | Description |
|---------|------|-------------|
| L061 | `L061.rego` | Use true/false for booleans, not yes/no/True/False |
| L062 | `L062.rego` | Use YAML-style module arguments, not key=value one-liners |
| L063 | `L063.rego` | Block should have a name |
| L064 | `L064.rego` | Avoid meta: end_play; prefer meta: end_host |
| L065 | `L065.rego` | Play names should not contain Jinja expressions |
| L066 | `L066.rego` | Do not mix roles: and tasks: in the same play |
| L067 | `L067.rego` | Set verbosity on debug tasks |
| L068 | `L068.rego` | Avoid lineinfile; prefer template/ini_file/blockinfile |
| L069 | `L069.rego` | Batch package names in a list instead of looping with item |
| L070 | `L070.rego` | Jinja in task names should only appear at the end |
| L071 | `L071.rego` | Consider using template instead of copy with Jinja content |
| L072 | `L072.rego` | Consider setting backup: true on template/copy tasks |

---

## Good-Practices Native Rules — L073-L094

These rules enforce recommendations from the automation-good-practices documentation.

| Rule ID | File | Description |
|---------|------|-------------|
| L073 | `L073_indentation.py` | YAML should use 2-space indentation |
| L074 | `L074_no_dashes_in_role_name.py` | Role names should not contain dashes |
| L075 | `L075_ansible_managed.py` | Templates should include ansible_managed comment |
| L076 | `L076_ansible_facts_bracket.py` | Use ansible_facts bracket notation instead of injected fact variables |
| L077 | `L077_role_arg_specs.py` | Roles should have meta/argument_specs.yml |
| L078 | `L078_dot_notation.py` | Use bracket notation for dict key access in Jinja |
| L079 | `L079_role_var_prefix.py` | Role defaults/vars should be prefixed with the role name |
| L080 | `L080_internal_var_prefix.py` | Internal role variables should be prefixed with __ |
| L081 | `L081_numbered_names.py` | Do not number roles or playbooks |
| L082 | `L082_template_j2_ext.py` | Template source files should use .j2 extension |
| L083 | `L083_hardcoded_group.py` | Do not hardcode host group names in roles |
| L084 | `L084_subtask_prefix.py` | Task names in included sub-task files should use a prefix |
| L085 | `L085_role_path_include.py` | Use explicit role_path prefix in include paths within roles |
| L086 | `L086_play_vars_usage.py` | Avoid playbook/play vars for routine config |
| L087 | `L087_collection_license.py` | Collection root should have a LICENSE or COPYING file |
| L088 | `L088_collection_readme.py` | Collection README should document supported ansible-core versions |
| L089 | `L089_plugin_type_hints.py` | Plugin Python files should include type hints |
| L090 | `L090_plugin_file_size.py` | Plugin entry files should be small |
| L091 | `L091_bool_filter.py` | Use \| bool for bare variables in when conditions |
| L092 | `L092_loop_var_in_name.py` | Avoid loop variable references in task names |
| L093 | `L093_set_fact_override.py` | Do not override role defaults/vars with set_fact |
| L094 | `L094_dynamic_template_date.py` | Do not put dynamic dates in templates |

---

## ansible-lint Gap Native Rules — L095-L105

These rules close coverage gaps identified by cross-referencing with ansible-lint.

| Rule ID | File | Description |
|---------|------|-------------|
| L095 | `L095_schema_validation.py` | Basic structural schema validation for playbooks and galaxy.yml |
| L096 | `L096_meta_runtime.py` | meta/runtime.yml requires_ansible must be a valid version specifier |
| L097 | `L097_name_unique.py` | Task names should be unique within a play |
| L098 | `L098_yaml_key_duplicates.py` | YAML files should not have duplicate mapping keys |
| L099 | `L099_yaml_quoted_strings.py` | Prefer double quotes for YAML string values |
| L100 | `L100_var_naming_keyword.py` | Variable names must not be Python or Ansible keywords |
| L101 | `L101_var_naming_reserved.py` | Variable names must not collide with Ansible reserved names |
| L102 | `L102_var_naming_read_only.py` | Do not set read-only Ansible variables |
| L103 | `L103_galaxy_changelog.py` | Collection should have a CHANGELOG file |
| L104 | `L104_galaxy_runtime.py` | Collection should have meta/runtime.yml |
| L105 | `L105_galaxy_repository.py` | galaxy.yml should have a repository key |

---

## Other Rule Namespaces (Unchanged)

| Namespace | Description |
|-----------|-------------|
| **R###** | Risk/security rules (e.g. R101, R103-R109, R111-R115, R117, R118, R401-R404, R501) remain R###. R118 is an OPA rule; the rest are native. |
| **P###** | Policy/validation rules (P001-P004) — legacy; superseded by L058/L059 and M001. |
| **Sample101** | Sample rule; unchanged. |

---

## Usage

- In output, violations use their rule ID directly: `L002`-`L105`, `M001`-`M030`, `R###`.
- Native (Python) lint violations include the `native:` prefix (e.g. `native:L026`) for backward compatibility.
- To map an old ID to the current one, use the tables above.
- Filtering by rule (e.g. `--rule L057`) uses the rule ID.

---

## Related Documents

- [ADR-008: Rule ID Conventions](/.sdlc/adrs/ADR-008-rule-id-conventions.md) — Rule ID prefix meanings
- [rule-catalog.md](rule-catalog.md) — Complete rule listing with fixer status
- [rule-doc-format.md](rule-doc-format.md) — Documentation format for individual rules
- [architecture.md](architecture.md) — Validator service architecture
