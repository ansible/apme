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

Ordinary Ansible/task findings keep their existing IDs. L057 discovery includes
import-only playbooks and does not classify config mappings as plays merely
because they contain `hosts` or `tasks` keys.
Test-content graph discovery covers both `molecule/` and collection
`extensions/molecule/` layouts, including nested scenario directories.

## Compatibility

The initial pinned schema is Molecule **24.9.0**, which Red Hat documents as
distributed with AAP 2.5 Ansible development tools:
[Red Hat package reference](https://access.redhat.com/solutions/7101613).
This profile targets that configuration contract. A version numbered 6 or
greater does not itself establish Red Hat support, and the profile does not
certify external drivers or packages. Newer native `ansible:` configuration
requires a separately verified profile; it is not silently accepted under the
24.9.0 schema.

The upstream schema and defaults ship locally with commit provenance and an
MIT notice in `validators/native/molecule_data`. Scans never download schemas.

## Configuration and scan context

The 24.9.0 profile merges bundled defaults, the project's supplied
`.config/molecule/config.yml`, and each scenario's overrides, in that order.
Empty scenarios inherit these values. Lists replace inherited lists. Custom
Molecule `--base-config` arguments and home-directory configuration are not
implicit scan inputs. Supply the effective project configuration in the
documented location when assessing those layouts.

Known Molecule scenario/project paths and values in a supplied project
`.env.yml` can resolve references. Other environment or template values produce
an L115 coverage notice; server environment values are never substituted.
Diagnostics exclude substituted secret values.

Reference checks use only the uploaded snapshot. L113 means a configured target
is unavailable there, which can also result from exclusions or checking one
file. Optional default stage files are not mandatory. Driver-specific schemas
and runtime package availability remain unassessed and are identified by L115.
Non-Ansible verifier code is outside Ansible playbook structural validation.

Configuration findings retain source file/line locations after merging and
remain manual-review findings across remediation approval gates. Graph-only
rescans do not claim that independent file findings have been fixed. A fresh
full assessment is required after editing configuration.

## Updating the profile

Verify the target Red Hat ADT/AAP distribution first. Pin its schema and
defaults to an immutable upstream commit, preserve the license, record the
hashes and Red Hat reference, and update the supported semantic checks together.
Run rule and session regression coverage through `tox -e unit` and regenerate
catalogs/type-check through `tox -e lint`. Do not track upstream `main` at scan
time or treat an upstream version range as a vendor support policy.
