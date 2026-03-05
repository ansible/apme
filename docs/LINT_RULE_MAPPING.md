# Rule IDs — cross-mapping

Rule IDs categorize the **type of check**, not the validator that runs it. The validator is an implementation detail.

| Prefix | Category | Notes |
|--------|----------|-------|
| L | Lint (style, correctness, best practice) | Any validator can implement an L rule |
| M | Modernize (keep code current with ansible-core) | Uses ansible-core runtime metadata |
| R | Risk/security (annotation-based) | Any validator can implement an R rule |
| P | Policy/validation (legacy) | Superseded by L058/L059 |

**Note:** `src/apme_engine/validators/native/rules/rule_versions.json` includes entries for both previous (R301, R302, ...) and current (L026, L027, ...) IDs for the renumbered rules; the loader uses the current rule_id (L###) when looking up version info.

## OPA (Rego) rules — L002-L025, R118

L001 was removed — its scope was limited to shell tasks without names, which is a strict subset of L024 (all tasks should have a name).

| Previous rule_id (OPA) | New ID | Description |
|------------------------|--------|-------------|
| ~~task-name~~ | ~~L001~~ | ~~Removed: subsumed by L024~~ |
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

## Native (Python) rules — L026-L039

| Previous rule_id (ARI) | New ID | File | Description |
|------------------------|--------|------|-------------|
| R301 | L026 | L026_non_fqcn_use.py | Non-FQCN module use |
| R302 | L027 | L027_role_without_metadata.py | Role without metadata |
| R303 | L028 | L028_task_without_name.py | Task without name |
| R102 | L029 | L029_command_instead_of_shell.py | Prefer command over shell |
| R110 | L030 | L030_non_builtin_use.py | Non-builtin module use |
| R116 | L031 | L031_insecure_file_permission.py | Insecure file permission |
| R201 | L032 | L032_changed_data_dependence.py | Changed data dependence |
| R202 | L033 | L033_unconditional_override.py | Unconditional override |
| R203 | L034 | L034_unused_override.py | Unused override |
| R204 | L035 | L035_unnecessary_set_fact.py | Unnecessary set_fact |
| R205 | L036 | L036_unnecessary_include_vars.py | Unnecessary include_vars |
| R304 | L037 | L037_unresolved_module.py | Unresolved module |
| R305 | L038 | L038_unresolved_role.py | Unresolved role |
| R306 | L039 | L039_undefined_variable.py | Undefined variable |

## New native lint rules — L040-L056

| Rule ID | File | Description |
|---------|------|-------------|
| L040 | L040_no_tabs.py | No tabs in YAML |
| L041 | L041_key_order.py | Key ordering (e.g. name before module) |
| L042 | L042_complexity.py | Play/block task count (complexity) |
| L043 | L043_deprecated_bare_vars.py | Deprecated bare variables {{ foo }} |
| L044 | L044_avoid_implicit.py | Avoid implicit state (set state explicitly) |
| L045 | L045_inline_env_var.py | Avoid inline environment in tasks |
| L046 | L046_no_free_form.py | Avoid raw/command/shell without args key |
| L047 | L047_no_log_password.py | no_log for password-like params (opt-in, disabled by default) |
| L048 | L048_no_same_owner.py | copy with remote_src set owner (opt-in, disabled by default) |
| L049 | L049_loop_var_prefix.py | Loop variable prefix (e.g. item_) |
| L050 | L050_var_naming.py | Variable naming (lowercase, underscores) |
| L051 | L051_jinja.py | Jinja spacing {{ var }} |
| L052 | L052_galaxy_version_incorrect.py | Galaxy version format in meta |
| L053 | L053_meta_incorrect.py | Role meta structure |
| L054 | L054_meta_no_tags.py | Role meta galaxy_tags |
| L055 | L055_meta_video_links.py | Role meta video_links URLs |
| L056 | L056_sanity.py | Path matches sanity ignore pattern |

## Ansible validator rules — L057-L059

These rules require an Ansible runtime (pre-built venv with ansible-core). They run in the Ansible validator service.

| Rule ID | Description | Method |
|---------|-------------|--------|
| L057 | Syntax check (`ansible-playbook --syntax-check`) | subprocess |
| L058 | Argspec validation (docstring-based: parses DOCUMENTATION string) | subprocess |
| L059 | Argspec validation (mock/patch-based: captures real `argument_spec`) | subprocess |

L058 and L059 both check module arguments but use different extraction methods:
- **L058** parses the module's `DOCUMENTATION` string -- safe (no code execution), fast, but may drift from actual argument_spec.
- **L059** patches `AnsibleModule.__init__` and calls `module.main()` to capture the real `argument_spec` -- more accurate, catches `mutually_exclusive`/`required_together`/`required_if`, but executes module import code.

Both can run simultaneously; each has a unique rule ID so users can enable/disable independently.

## Modernize rules — M001-M004

These rules use ansible-core's plugin loader (`find_plugin_with_context()`) to resolve modules against the actual runtime metadata (`ansible_builtin_runtime.yml` and collection `meta/runtime.yml`). They stay current with whichever ansible-core version is in the venv.

| Rule ID | Description |
|---------|-------------|
| M001 | FQCN resolution -- module resolved to a different canonical FQCN |
| M002 | Deprecated module -- module has deprecation metadata |
| M003 | Module redirect -- module name was redirected to a new FQCN |
| M004 | Removed module -- tombstoned module (raises `AnsiblePluginRemovedError`) |

Note: OPA **L002** also checks for non-FQCN module names but is purely syntactic (counts dot separators). **M001** is semantic -- it actually resolves the module via ansible-core's plugin loader. Both can fire for the same task (different rule IDs, complementary checks). M001 also works for third-party collections.

## Other rule namespaces (unchanged)

- **R###** -- Risk/security rules (e.g. R101, R103-R109, R111-R115, R117, R118, R401-R404, R501) remain R###. R118 is an OPA rule; the rest are native.
- **P###** -- Policy/validation rules (P001-P004) -- legacy; superseded by L058/L059 and M001.
- **Sample101** -- Sample rule; unchanged.

## Usage

- In output, violations use their rule ID directly: **L002**-**L059**, **M001**-**M004**, **R###**.
- Native (Python) lint violations include the **native:** prefix (e.g. **native:L026**) for backward compatibility.
- To map an old ID to the current one, use the tables above.
- Filtering by rule (e.g. `--rule L057`) uses the rule ID.
