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

## Playbooks and rescans

Inspect explicitly referenced YAML as Ansible content and reuse existing graph
task checks for parsed playbooks. Native assesses import-only playbooks inertly;
L057 excludes Molecule scenario content because runtime syntax checking may
evaluate lookups. Avoid accidental runtime matches in config/inventory. Resolve
only statically known context. Report unknown execution-time variables as
coverage limits instead of treating them as definitive syntax failures.

Preserve config findings through convergence. Graph-only incremental requests
must not imply that config files were revalidated. Shared config changes require
rechecking dependent scenarios with an updated snapshot.

## Verification

Use tox for rule tests, Native RPC behavior, engine no-hierarchy behavior,
catalog registration, file/line locations, suppression/severity overrides, and
ordinary scenario playbook coverage. Include the native example from issue
#5119, inherited native configuration, malformed executor settings, and mixed
legacy/native repositories. Validate redistribution/package data and that no
Molecule command or untrusted loader is invoked.
