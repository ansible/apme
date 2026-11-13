# REQ-023: Design

## Native service

Add a built-in file assessment pass to Native using the existing uploaded File
messages. It runs in the executor beside graph rules and does not depend on
playbook nodes, engine temp paths, or runtime plugin discovery. Declare metadata
alongside each implementation and include it in catalog discovery and generated
rule documentation. Keep rule IDs independent of validator implementation.

## Configuration

Retain a source map from effective configuration paths to original YAML nodes.
Parse raw documents before merging so duplicate keys and malformed YAML are
reported at their source. Merge known shared configuration and scenario layers
using version-specific semantics. Distinguish raw fragment validation from
effective configuration completeness. Evaluate only known Molecule path
variables and deterministic literal/default substitutions; represent unknown
values explicitly rather than using the daemon's environment.

## Profiles and references

Keep the 24.9.0 legacy snapshot and add the 26.11.0 native snapshot with
upstream provenance and redistribution notices. Select the profile from merged
user config (`ansible` or `shared_state` selects native), migrate explicit
legacy settings with source attribution, then apply the selected defaults.
Never refresh schemas during scans. Native and legacy syntax support must follow the chosen release; accepting a newer upstream
schema does not establish vendor support. Driver-specific plugin validation is
limited to trusted bundled schemas and never imports project plugins.

Resolve local references relative to the appropriate scenario/project root.
Allow references to siblings within the snapshot, respect exclusions, and
distinguish unavailable content from a proven invalid reference. Scenario
actions require semantic checks beyond the upstream schema's permissive strings.
Respect optional stages, action arguments, repeated actions, and shared state.

Treat collection-qualified imports separately from filesystem paths. Resolve
uploaded collections under `collections/ansible_collections/` or
`ansible_collections/`, and a source collection identified by its uploaded
`galaxy.yml` or `MANIFEST.json`. Unavailable collection content needs L118.
Cache project identities, including unavailable/malformed metadata, per request.
Use the enclosing collection layout for nested sibling imports and select a
collection copy before looking for its playbook, preserving shadowing.
Represent documented generated Molecule paths with inert virtual runtime paths:
argument use is known, but generated-file contents remain unavailable for
reference checks. Optional explicit missing stage references identify the
affected scenario and explain Molecule's ability to skip the stage.

Merge values separately from source origins so wide mappings do not rescan the
entire origin map per key. Decode source text once per file for excerpts; inline
suppression examines only the diagnostic line while fingerprints retain the
full excerpt.

## Playbooks and rescans

Inspect explicitly referenced YAML as Ansible content and reuse existing graph
task checks for parsed playbooks. Native assesses import-only playbooks inertly;
L057 excludes Molecule scenario content because runtime syntax checking may
evaluate lookups. Avoid accidental runtime matches in config/inventory. Resolve
only statically known context. Report unknown execution-time variables as
coverage limits instead of treating them as definitive syntax failures.
Forward the complete uploaded byte map through the Ansible adapter into L057's
static exclusion lookup so extensionless collection imports and their YAML
descendants receive the same exclusions as Native's assessment.

Preserve config findings through convergence. Graph-only incremental requests
must not imply that config files were revalidated. Shared config changes require
rechecking dependent scenarios with an updated snapshot.

## Verification

Carry ordered, distinct inherited `module_defaults` contexts in the existing
hierarchy JSON. Preserve play defaults during graph construction and retain
task-owned arguments unchanged. Bound the number of distinct contexts for
shared roles/task files and report incomplete assessment if the bound is reached.
Role call edges retain invocation defaults and entry selectors separately from
role file inventory. Reuse one executor-based context calculation during
incremental rescans; OPA and Ansible share its serialized node dictionaries.
Bound expanded JSON nodes and bytes, including repeated YAML aliases and the complete
per-task defaults payload. An exceeded budget marks the affected context
incomplete. Inactive parent paths cannot introduce new coverage failures into
an independently active include; existing incomplete ancestry still propagates.
Graph and hierarchy transport also reject cyclic or excessive expanded input
with an explicit incomplete-validation error instead of sending truncated data.
The Ansible argument checks resolve module aliases and action groups using the
session runtime's metadata without running plays or templating values. Merge
defaults by module/group entry in scope order, then groups, the module entry,
and explicit task arguments. Reuse the resolved arguments for both argspec
checks; distinguish malformed defaults from unavailable runtime/template context.
Preserve distinct argument errors and coverage warnings through output and
the graph ledger, and retain manual-review routing for inherited defaults.
Do not resolve prior argument errors solely because a rescan could not assess
the associated invocation; preserve distinct same-line Molecule findings.
Native scenario validation also checks task-section shapes in referenced files;
Ansible-compatible empty play sections remain valid.

Use tox for rule tests, Native RPC behavior, engine no-hierarchy behavior,
catalog registration, file/line locations, suppression/severity overrides, and
ordinary scenario playbook coverage. Include the native example from issue
#5119, inherited native configuration, malformed executor settings, and mixed
legacy/native repositories. Validate redistribution/package data and that no
Molecule command or untrusted loader is invoked.
