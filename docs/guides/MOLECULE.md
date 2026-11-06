# Static Molecule Validation

Run `apme check .` to assess Molecule configuration and scenario playbooks
automatically. L112-L116 are Native lint rules tagged `molecule`, enabled by
default at medium severity. Use ordinary rule overrides to change severity or
disable them. Validators read uploaded content; they do not run Molecule,
provision resources, evaluate project lookups, or execute dependency commands.

As with other APME findings, active Molecule warnings affect `apme check`'s exit
status: it exits 1 when findings remain. Severity alone does not change this;
use rule enablement or suppression when a warning should not gate CI.

| Rule | Check |
|------|-------|
| L112 | Config YAML, duplicate keys, effective schema |
| L113 | Playbooks, inventory links, explicit Galaxy requirement files, and action arguments |
| L114 | Supported scenario lifecycle actions |
| L115 | Coverage limited by unavailable environment, files, or driver context |
| L116 | Referenced Ansible playbook YAML and play structure |

Ordinary Ansible/task graph findings keep their existing IDs. Native checks
referenced scenario playbooks, including import-only playbooks, statically.
L057 leaves Molecule scenario content and resolved shared/imported references to those checks because runtime syntax
checking can evaluate project lookups and requires Molecule context. Ordinary
runtime discovery does not classify config mappings as plays merely because
they contain `hosts` or `tasks` keys.
Test-content graph discovery covers both `molecule/` and collection
`extensions/molecule/` layouts, including nested scenario directories.

## Compatibility

The minimum compatibility target is **Molecule 24.9.0+**. Automatic checks
recognize both legacy `provisioner:` configuration and newer native `ansible:`
configuration, including shared-state scenarios. The baseline originates from
[Red Hat's AAP 2.5 package reference](https://access.redhat.com/solutions/7101613).
Syntax compatibility with later upstream releases is separate from Red Hat's
support policy for a particular distribution or driver.

Two immutable core schema/default snapshots and their built-in driver schemas ship locally:

| Configuration family | Bundled profile | Selection |
|----------------------|-----------------|-----------|
| Legacy provisioner | 24.9.0 | No native/shared-state keys in merged user config |
| Native / shared state | 26.11.0 | `ansible` or `shared_state` in merged user config |

Selection happens after shared settings and scenario overrides are merged, so
an empty scenario can inherit native configuration. These profiles check the
configuration contract, not the installed Molecule version. They cover the
bundled syntax families; future new syntax requires a profile update. An
unknown key remains a finding rather than being silently accepted as a future
feature. Schema sources, commit hashes, and the MIT notice are included in
`validators/native/molecule_data`. Scans never download schemas.

## Configuration and scan context

The supplied project `.config/molecule/config.yml` is merged with each scenario's
overrides. The selected profile's defaults are applied to the result. Lists
replace inherited lists. For the native profile, explicit legacy provisioner
settings are migrated to `ansible` after user layers merge and before defaults
are applied, matching upstream precedence: explicit legacy values replace the
corresponding native values. Diagnostics retain the original source locations.

Native `ansible.playbooks` references receive the same file and play-structure
checks as legacy references. Paths are relative to the scenario directory, as
specified in the [native configuration reference](https://docs.ansible.com/projects/molecule/ansible-native/).
Executor backends and argument shapes are checked by the bundled native schema.
Optional stages and repeated verification steps remain valid.
Unknown playbook stages and ignored lifecycle arguments receive lint findings.
Scenario names honor merged overrides and native nested collection names.
Literal `import_playbook` targets and imports using known Molecule project/scenario
paths are followed within the uploaded snapshot. Missing imports and cycles are
diagnosed. Unknown import expressions remain coverage notices; no Jinja is evaluated.

Custom Molecule `--base-config` arguments and home-directory configuration are
not implicit scan inputs. Supply effective project configuration in the
specified location when assessing those layouts.

Known Molecule scenario/project paths, values in a supplied project `.env.yml`,
and explicit `${VAR:-default}` / `${VAR-default}` fallbacks can resolve references.
Without a supplied value, the explicit fallback is used. Other environment or template values produce
an L115 coverage notice; server environment values are never substituted.
Diagnostics exclude substituted secret values.

Reference checks use only the uploaded snapshot. L113 means a configured target
is unavailable there, which can also result from exclusions or checking one
file. Optional default stage files are not mandatory. The built-in default/delegated
driver schema is checked; external driver schemas
and runtime package availability remain unassessed and are identified by L115.
Non-Ansible verifier code is outside Ansible playbook structural validation.

Configuration findings retain source file/line locations after merging and
remain manual-review findings across remediation approval gates. Graph-only
rescans do not claim that independent file findings have been fixed. A fresh
full assessment is required after editing configuration.
Findings include unexpanded source excerpts for content-based suppression.
Use `# noqa: L113` (or another rule ID) on the diagnostic's source line for
inline suppression; enforced rules remain active.

## Updating the profile

Review a released Molecule configuration contract at or above the minimum
24.9.0 baseline. Pin its schema and defaults to an immutable upstream commit,
preserve the license and source hashes, and update the semantic checks together.
Retain the baseline Red Hat reference separately from newer upstream provenance.
Run rule and session regression coverage through `tox -e unit` and regenerate
catalogs/type-check through `tox -e lint`. Do not track upstream `main` at scan
time or treat an upstream version range as a vendor support policy.
