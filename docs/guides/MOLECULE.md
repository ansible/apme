# Static Molecule Validation

Run `apme check .` to assess Molecule configuration and scenario playbooks
automatically. L115-L119 are Native lint rules tagged `molecule`, enabled by
default at medium severity. Use ordinary rule overrides to change severity or
disable them. Molecule checks read uploaded content; they do not run Molecule,
provision resources, evaluate project lookups, or execute dependency commands.

As with other APME findings, active Molecule warnings affect `apme check`'s exit
status: it exits 1 when findings remain. Severity alone does not change this;
use rule enablement or suppression when a warning should not gate CI.

| Rule | Check |
|------|-------|
| L115 | Config YAML, duplicate keys, effective schema |
| L116 | Playbooks, inventory links, explicit Galaxy requirement files, and action arguments |
| L117 | Supported scenario lifecycle actions |
| L118 | Coverage limited by unavailable environment, files, or driver context |
| L119 | Referenced Ansible playbook YAML and play structure |

Scenario playbooks may use Ansible
[`module_defaults`](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_module_defaults.html).
Native L119 checks the declaration's shape at play, block, task, and include
`apply` scopes without loading plugins. The Ansible service's L058/L059 argument
checks include inherited defaults and action-group membership from the selected
environment's installed metadata. Defaults follow their invocation context through
roles and task includes/imports. A later module/group entry replaces its earlier
mapping; an empty module entry clears that entry. Group defaults precede module
defaults, then explicit arguments. Clearing a module entry does not clear a
matching group's defaults.
Role declarations retain their own defaults, and role argument checks follow
selected task/handler entry files and their imports. Files merely inventoried
under a role do not create additional invocations.

Defaults do not use `collections` to qualify names. Fully qualified action groups,
historical aliases, and extended groups follow Ansible's own metadata resolution.
When a collection, group, templated mapping, or bounded invocation context cannot
be assessed, L058 reports incomplete argument validation and excludes that context
from both argument checks. Defaults resolution does not run templates, lookups,
or Molecule actions.
If policy input or the complete graph exceeds serialization or transport limits,
or contains a cycle, the scan fails with an incomplete-validation error instead
of returning partial results.
Native-only requests provide defaults structure checks; module argument checks
require the Ansible service and its selected environment.

Ordinary Ansible/task graph findings keep their existing IDs. Native checks
referenced scenario playbooks, including import-only playbooks, statically.
L057 leaves Molecule scenario content and resolved shared/imported references to those checks because runtime syntax
checking can evaluate project lookups and requires Molecule context. Ordinary
runtime discovery does not classify config mappings as plays merely because
they contain `hosts` or `tasks` keys.

L057 also excludes entrypoints that statically import scenario content. If an
entrypoint's import or role closure cannot be established safely, L057 reports
incomplete syntax assessment rather than dispatching that entrypoint.
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
The Ansible service forwards the complete normalized upload for static runtime
exclusions, including extensionless imports. Standalone Python callers that omit
that snapshot receive an explicit L057 coverage error and skip runtime syntax
checks when Molecule content is detected.

Collection-qualified imports such as `example.testing.prepare` are supported.
Uploaded targets are checked in `collections/ansible_collections/` and
`ansible_collections/`, or in the source project's `playbooks/` when uploaded
`galaxy.yml` or `MANIFEST.json` identifies that collection. Subdirectories and
explicit `.yml`/`.yaml` extensions are supported. If collection content is not
supplied in those layouts, L118 explains the coverage limit; APME does not label
the FQCN a missing local file or assume the collection is installed.
Nested scenarios can also resolve sibling collections in their enclosing
uploaded `ansible_collections/` tree. Lookup keeps the first available collection
copy, matching collection shadowing rather than searching later copies for a
missing playbook. Project metadata is parsed once per assessment.

Custom Molecule `--base-config` arguments and home-directory configuration are
not implicit scan inputs. Supply effective project configuration in the
specified location when assessing those layouts.

Known Molecule scenario/project paths, values in a supplied project `.env.yml`,
and explicit `${VAR:-default}` / `${VAR-default}` fallbacks can resolve references.
Without a supplied value, the explicit fallback is used. Other environment or template values produce
an L118 coverage notice; server environment values are never substituted.
Diagnostics exclude substituted secret values.

Documented generated paths (`MOLECULE_EPHEMERAL_DIRECTORY`, `MOLECULE_FILE`,
`MOLECULE_STATE_FILE`, `MOLECULE_INVENTORY_FILE`, `MOLECULE_INSTANCE_CONFIG`)
are recognized symbolically. Using them in executor arguments does not require
runtime context. References that need the generated files' contents still
produce L118; scans never read or create Molecule's runtime directories.

Reference checks use only the uploaded snapshot. L116 means a configured target
is unavailable there, which can also result from exclusions or checking one
file. Optional default stage files are not mandatory. The built-in default/delegated
driver schema is checked; external driver schemas
and runtime package availability remain unassessed and are identified by L118.
Non-Ansible verifier code is outside Ansible playbook structural validation.
Explicit optional-stage references that are absent still receive L116, with the
affected scenario and a note that Molecule may skip the stage. Shared config
stage findings retain their original source location and distinguish affected scenarios.

Configuration findings retain source file/line locations after merging and
remain manual-review findings across remediation approval gates. Graph-only
rescans do not claim that independent file findings have been fixed. A fresh
full assessment is required after editing configuration.
Findings include unexpanded source excerpts for content-based suppression.
Use `# noqa: L116` (or another rule ID) on the diagnostic's source line for
inline suppression; enforced rules remain active.

## Updating the profile

Review a released Molecule configuration contract at or above the minimum
24.9.0 baseline. Pin its schema and defaults to an immutable upstream commit,
preserve the license and source hashes, and update the semantic checks together.
Retain the baseline Red Hat reference separately from newer upstream provenance.
Run rule and session regression coverage through `tox -e unit` and regenerate
catalogs/type-check through `tox -e lint`. Do not track upstream `main` at scan
time or treat an upstream version range as a vendor support policy.
