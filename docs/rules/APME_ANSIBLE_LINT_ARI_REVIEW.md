# APME rule review: ansible-lint and Ansible Risk Insights

## Executive summary

APME has broad coverage of the everyday Ansible task checks found in
ansible-lint. Its OPA and graph rules cover common naming, module, idempotency,
permissions, privilege, and YAML practices. It also contains substantial
Ansible Risk Insights (ARI) rule lineage: most of the original module risk
checks and the R201–R205 variable checks have APME implementations.

This is not full rule parity. The source comparison found several operational
gaps, and the follow-up changes in this working tree address the highest-impact
ones. Remaining gaps include:

1. **The old ansible-lint coverage map overstates several matches.** In
   particular, APME `L052`, `L054`, and `L056` do not implement the upstream
   rules their old mappings imply; APME `L095` is only narrow structural
   validation; and role argument validation checks only part of an argspec.
2. **Some ansible-lint checks remain gaps:** ansible-lint's
   sanity-ignore-file policy, broad schema coverage, and much of its YAML
   formatting set. The follow-up adds VCS revision checks, role-name format,
   and collection-version validation under `L112`–`L114`.
3. **ARI parity is approximate.** APME reimplements risk checks using a
   static module-risk map and its content graph. It does not run ARI's current
   annotator, call-tree, and optional RAM metadata pipeline. A few migrated
   checks therefore have materially different conditions or narrower
   detection.

## Scope and method

This is a source-level comparison, not a claim that every rule was exercised
against a representative playbook corpus. I reviewed APME's checked-in OPA
bundle, native `GraphRule` classes, Ansible validator rules, scanner loading
behavior, and the current rule documentation.

The comparison baselines are APME commit `0b9ee767`, upstream
[ansible-lint v26.10.1](https://github.com/ansible/ansible-lint/tree/v26.10.1),
and Ansible Risk Insights (ARI) at
[commit `18cbf071`](https://github.com/ansible/ansible-risk-insight/tree/18cbf07185a4b9cf209b40b470a92ac2393db1b6).
The project lockfile contains ansible-lint 26.4.0 as a development dependency;
APME's runtime dependencies include `ansible-core`, not ansible-lint. This
review compares source behavior and does not mean APME invokes ansible-lint.

The comparison baseline contains 95 native graph-rule classes (92 enabled
by their class defaults) and 49 OPA rule modules. This follow-up adds three
graph rules (`L112`–`L114`) and emits `L037` through the existing Ansible
plugin-introspection validator alongside `L057`–`L059`. The current branch
also includes upstream `M031` work merged after the comparison baseline; that
rule is outside this parity comparison.
L037 resolution includes each task's in-scope `collections` search order,
checks Ansible's module and action plugin loaders, and loads project-level
plugin paths from the scan root. Role-specific plugin paths are not inferred
per task.
`R402` and `R404` remain deliberately opt-in. The old disabled graph
heuristic for `L037` remains inactive; module resolution now uses Ansible's
semantic plugin-loader result.

## ansible-lint comparison

Status terms:

- **Close**: APME has a check with substantially the same target and intent.
- **Partial**: APME overlaps, but scope, conditions, or validation depth differ.
- **Gap**: no equivalent check was found in the current scan path.
- **N/A**: ansible-lint operational diagnostics rather than a comparable
  project rule.

### Task and playbook rules

| ansible-lint rule | APME coverage | Status and notes |
|---|---|---|
| `avoid-implicit` | `L044` | Close for supported implicit-state cases. |
| `command-instead-of-module` | `L006` | Close in intent. |
| `command-instead-of-shell` | `L007` | Close in intent; also overlaps ARI R102 lineage. |
| `complexity` | `L042` | Partial: APME uses its own play/block task-count limits and scope. |
| `deprecated-bare-vars` | `L043` | Close for legacy bare variables in loop syntax. |
| `deprecated-local-action` | `L008` | Close. |
| `deprecated-module` | `L004`, `M002` | Partial: static OPA checks plus ansible-core runtime metadata; behavior depends on what the runtime can resolve. |
| `empty-string-compare` | `L009` | Close for the matched `when` forms. |
| `fqcn` | `L026`, `M001`, `M003`, `M048` | Broad overlap: static non-FQCN detection, plugin-loader resolution/redirects, and builtin twins. Subrule-by-subrule parity is not established. |
| `ignore-errors` | `L010` | Close for the rule's targeted task patterns. |
| `inline-env-var` | `L045` | Close in intent. |
| `jinja` | `L051` | Partial: APME checks a narrower whitespace/style pattern than ansible-lint's Jinja rule family. |
| `jinja-template-extension` | `L082` | Close for template extension checks. |
| `key-order` | `L041` | Partial: APME checks Ansible task-key order; this should not be read as general YAML mapping-order validation. |
| `latest` | `L112` | Partial: flags omitted/default Git `version` and Mercurial `revision` (including `community.general.hg`); templated values are skipped. It does not prove that an explicit revision is immutable. |
| `package-latest` | `L012` | Close for package modules using `state: latest`; this is not coverage of the separate `latest` rule. |
| `literal-compare` | `L011` | Close for targeted literal comparisons. |
| `loop-var-prefix` | `L049` | Partial: APME has its own prefix convention. |
| `name` | `L003`, `L024`, `L025`, `L097` | Partial: play/task names, casing, and uniqueness are covered. The upstream name subrules for prefixes and template placement are not equivalent to these checks. |
| `no-changed-when` | `L013` | Close for command-like tasks. |
| `no-free-form` | `L046` | Close for targeted free-form task syntax. |
| `no-handler` | `L014` | Close for recognized changed-result patterns. |
| `no-jinja-when` | `L015` | Close. |
| `no-log-password` | `L047` | Overlap; APME's password-like option patterns determine coverage. |
| `no-prompting` | `L016` | Close for `pause` without a duration. |
| `no-relative-paths` | `L017` | Close for the targeted relative `src` fields. |
| `no-same-owner` | `L048` | Partial: limited to the rule's `copy`/`remote_src` case. |
| `no-tabs` | `L040` | Close. |
| `only-builtins` | `L005`, `M048` | Broad overlap, but the OPA rule's allowed namespaces and runtime builtin-twin detection differ from ansible-lint's exact implementation. |
| `partial-become` | `L018` | Close. |
| `playbook-extension` | `L019` | Close for recognized playbook files. |
| `risky-file-permissions` | `L021`, `L031` | Broad overlap across OPA and graph checks. |
| `risky-octal` | `L020` | Partial: APME focuses on Ansible `mode` values, not every YAML octal value. |
| `risky-shell-pipe` | `L022` | Close for recognized shell-pipe forms. |
| `role-name` | `L074`, `L085`, `L113` | Partial: L113 checks owned role directories (using `galaxy_info.role_name` when set); playbook role references are not independently checked. L074 and L085 cover related hyphen/import cases. |
| `run-once` | `L023` | Close in intent. |
| `syntax-check` | `L057` | Partial: APME runs `ansible-playbook --syntax-check`, but discovers candidate files by searching for literal `hosts:` or `tasks:` text. That is narrower than linting all discovered playbook inputs. |

### Args, metadata, schema, and YAML

| ansible-lint rule or family | APME coverage | Status and notes |
|---|---|---|
| `args` | `L058`, `L059` | Partial. Both check unknown keys, required arguments, and choices; L059 also enforces mutually-exclusive and required-together groups. Both skip templated tasks and short names in custom collection contexts. Neither checks argument types, `required_if`, or `required_one_of`, and neither merges `module_defaults`. |
| `galaxy` | `L027`, `L052`–`L055`, `L087`–`L088`, `L095`, `L103`–`L105`, `L114` | Partial collection/role metadata coverage, not the upstream rule as a whole. See the corrections below. |
| `galaxy-version-incorrect` | `L052`, `L114` | Partial: L114 checks collection `galaxy.yml` semantic versions and requires at least 1.0.0. L052 separately checks a role's `meta/main.yml` `galaxy_info.version` format. |
| `meta-incorrect` | `L053` | Different but related metadata check: APME validates selected field types and shape. Upstream flags placeholder defaults such as `your name`, `your description`, and `your company`; APME does not check those placeholders. |
| `meta-no-tags` | `L054` | Partial: APME only reports when both `galaxy_tags` and `categories` are absent/empty. Upstream validates list/type/string/tag format and flags use of `categories`; APME currently accepts non-empty `categories` as satisfying the check. |
| `meta-runtime` | `L096`, `L104` | Partial: APME checks `requires_ansible` presence and runtime-file presence. It does not validate the version expression or supported-version semantics. |
| `meta-video-links` | `L055` | Partial: APME accepts a list of HTTP(S) strings. Upstream expects mapping entries with only `url` and `title`, and a shared URL from YouTube, Vimeo, or Google Drive. |
| `role-argument-spec` | `L077` | Partial: APME checks that an argument-spec file/section exists. It does not validate the full role argument-spec schema. Upstream's rule is opt-in. |
| `sanity` | `L056` | **Gap:** not equivalent. Upstream checks entries in `tests/sanity/ignore*.txt` against an allowlist. APME `L056` flags nodes located in common ignored/generated paths such as `.git`, `.ansible`, `.pyc`, and `__pycache__`. |
| `schema` | `L095` | **Major partial:** APME checks unknown play-level keys against a fixed list and required collection `galaxy.yml` keys `namespace`, `name`, and `version`. It does not implement ansible-lint's schemas for roles, argspecs, inventories, requirements, execution environments, vars, playbooks, tasks, rulebooks, and related config files. |
| `var-naming` | `L050`, `L079`–`L081`, `L100`–`L102` | Partial: APME has several fixed naming checks, but does not provide all configurable regex, Jinja, and context semantics of ansible-lint's rule. |
| `yaml` | `L020`, `L040`, `L060`, `L061`, `L073`, `L098`, `L099` | Partial. APME covers tabs, 160-character lines, indentation, duplicate keys, selected truthy values, quoting, and Ansible mode octal formatting. It does not cover many default yamllint checks, including anchors, punctuation spacing, comments, document end, empty lines/values, floats, hyphen spacing, final newline, trailing spaces, and general YAML key ordering. `document-start` is disabled in ansible-lint's shipped yamllint config, and its quoted-string config is commented out; neither should be counted as a default parity gap. |

APME's collection-oriented rules are useful checks, but several have narrower
meaning than their old ansible-lint mapping. For example, `L087` checks for a
root license file, while ansible-lint's Galaxy license check concerns metadata;
`L088` checks that a root README exists; and `L103`–`L105` check individual
changelog/runtime/repository conditions rather than implementing the complete
`galaxy` and `schema` rule families.

`internal-error`, `parser-error`, `load-failure`, and `warning` are primarily
ansible-lint execution/load diagnostics. APME has its own parser and engine
error handling, but these are not one-for-one lint rules (**N/A**, rather than
ordinary missing policy checks).

### YAML default comparison detail

The ansible-lint v26.10.1 yamllint config sets a 160-character line limit,
disables `document-start`, disables `comments-indentation`, and leaves its
`quoted-strings` override commented out. APME `L060` therefore has a close
default line-length match; `L099` is an optional APME convention rather than
coverage of an enabled ansible-lint default. APME's `L041` is an Ansible task
key-order check, not yamllint's general mapping key ordering check. The main
default YAML gaps are the formatting and scalar checks listed in the table.

## Ansible Risk Insights comparison

APME's risk rules are related to ARI but no longer execute as ARI rules. APME's
architecture records that it uses an integrated graph implementation rather
than depending on the ARI package. Its task-risk checks use the local static
profiles in
[`_module_risk_mapping.py`](../../src/apme_engine/graph/rules/_module_risk_mapping.py)
and inspect graph nodes directly. ARI's documented model is a static call tree
with rules and risk annotations, optionally enriched by external collection
metadata sources. This difference matters for collection modules, annotation
semantics, and cross-task data flow.

### ARI policy and argument validation rules

| ARI rule | APME counterpart | Status and notes |
|---|---|---|
| `P001` module-name validation | `L037`, `M001` | Partial. L037 now reports names the Ansible plugin loader cannot resolve; M001 reports resolvable names whose canonical FQCN differs. Task collection order, project-level plugin paths, and installed runtime affect resolution; role-specific plugin paths are not inferred per task. |
| `P002` argument-key validation | `L058`, `L059` | Partial. Unknown/required keys and choices are checked when a module spec is found, including when all arguments are omitted; templated tasks are skipped, and module defaults/action groups are not merged. |
| `P003` argument-value validation | `L058`, `L059` | Partial. Choice values and selected groups are checked, but types, `required_if`, and `required_one_of` are not enforced. |
| `P004` variable validation | `L039` | Partial. APME has a broad undefined-variable heuristic using provenance and magic-variable exemptions; it is not equivalent to ARI's argument-context resolution and annotation behavior. |

The `P001`–`P004` Markdown files are delegated policy descriptions rather than
native graph rules. Their pages now distinguish module resolution, argspec
key/choice/group checks, and L039's approximate variable analysis. L058/L059
do not validate general argument types or conditional requirements.

### ARI module-risk rules

Most ARI module-risk rule families have APME counterparts. The mapping below
describes lineage and broad intent, not identical results:

| ARI rule family | APME counterpart | Parity note |
|---|---|---|
| Parameterized command execution (`R101`) | `R101` | APME flags a command field containing Jinja. ARI asks whether its annotation marks the command mutable. The predicates can produce different findings. |
| Command instead of shell (`R102`) | `L007` | Related best-practice finding, but linting preference is not a substitute for ARI risk context. |
| Download then execute (`R103`) | `R103` | Approximate. APME searches preceding tasks in an enclosing play and checks whether the download destination string occurs in a command. ARI uses annotated executable files and location containment. APME's path/data-flow analysis is simpler. |
| Unauthorized download source (`R104`) | `R104` | Corresponding check exists; source authorization context and matching behavior should be treated as APME-specific. |
| Outbound/inbound transfer (`R105`/`R106`) | `R105`/`R106` | Corresponding checks exist. APME's inbound check specifically requires a templated source; it is not a report of every inbound transfer. |
| Insecure package option (`R107`) | `R107` | Corresponding check exists for mapped package modules/options. |
| Privilege escalation / key config (`R108`/`R109`) | `R108`/`R109` | Corresponding graph checks exist; their rule predicates are local implementations. |
| Non-builtin module (`R110`) | `L005`, `M048` | Related checks exist under lint/modernize IDs. Not necessarily identical to ARI's module classification. |
| Parameterized role/task imports (`R111`/`R112`) | `R111`/`R112` | Corresponding graph checks exist. |
| Parameterized package install (`R113`) | `R113` | Corresponding graph check exists for mapped package modules. |
| File change/delete (`R114`/`R115`) | `R114`/`R115` | Corresponding graph checks exist for mapped modules. |
| Insecure file permissions (`R116`) | `L021`, `L031` | Related coverage exists under lint IDs; module and permission cases may differ. |
| External role (`R117`) | `R117` | Corresponding graph check exists. |

APME's static profile map covers the currently enumerated built-in risk module
families, but it is not an ARI risk annotator registry. Newly added or
third-party collection actions are not automatically covered merely because
ARI or a collection metadata source can describe them. Short module names use
the built-in aliases in APME's map; if a collection shadows one of those names,
the risk profile can differ from the plugin Ansible resolves. Explicit FQCNs
avoid that ambiguity, but do not add coverage for unmapped third-party modules.

### ARI data-flow, resolution, and audit rules

| ARI rule family | APME counterpart | Status and notes |
|---|---|---|
| Changed data dependence, unconditional override, unused override, unnecessary `set_fact`, unnecessary `include_vars` (`R201`–`R205`) | `L032`–`L036` | Direct lineage counterparts are present. They are APME graph analyses and should not be assumed byte-for-byte equivalent to ARI's run-context traversal. |
| Non-FQCN, role without metadata, task without name, unresolved module, unresolved role, undefined variable (`R301`–`R306`) | `L026`, `L027`, `L024`, `L037`, `L038`, `L039` | Counterparts are present. L037 now uses ansible-core's plugin loader; L039 remains a static approximation of ARI variable resolution. |
| List inbound sources (`R401`) | `R401` | Present as an informational report using APME's module-risk profiles. |
| List used variables / show task variables (`R402`, `R404`) | `R402`, `R404` | Present but intentionally disabled by default; both IDs are in the supported opt-in set. |
| Dependency suggestion (`R501`) | Documentation only | **Not implemented:** APME marks it planned and has no graph-rule class. |

#### R118 pipeline behavior

APME OPA rule `R118` expects a `risk_annotation` with
`risk_type: inbound_transfer`. The payload serializer now derives this
annotation from APME's local inbound module profiles for remote or templated
`get_url`, `git`, `subversion`, and `unarchive` sources. For `unarchive`, it
requires `remote_src: true`; templated sources are treated as potentially
remote. Local paths and `file://` sources are skipped. This makes R118
reachable in normal scans for those mapped
built-in modules; it does not add ARI's live annotator registry or cover
third-party collection modules automatically.

This is not fully replaced by `R106`: APME `R106` only reports inbound
transfers whose `src` is templated, while R118's stated purpose is to report
any annotated inbound transfer.

## Documentation issues found

The previous
[`ANSIBLELINT_COVERAGE.md`](ANSIBLELINT_COVERAGE.md) counts and classifications
are stale. Specific corrections:

- It calls `L095`/schema validation uncovered, though L095 now exists; the
  accurate status is **narrow partial coverage**.
- It calls role argument-spec validation uncovered, though `L077` checks
  presence; it does not validate full content.
- It maps `L052` to collection Galaxy version validation, but APME checks role
  metadata while upstream checks collection `galaxy.yml`.
- It maps `L054` too broadly: APME only checks tag presence, while upstream
  validates tag structure and format.
- It maps `L056` to ansible-lint `sanity`, though the checks have different
  purposes and inputs.
- It describes `R101`–`R117` as annotation-based and says R118 fires on ARI
  annotations. Native rules use a local risk map; R118 now receives a
  serialized annotation synthesized from that map.
- It groups `latest` and `package-latest` under `L012`; only the package state
  check is covered.
- It calls `L047` and `L048` opt-in and disabled by default, while both graph
  rule classes currently default to `enabled=True`.

The rule catalog describes the R namespace as risk/security and advisory
rules. The affected rule descriptions now identify local module-profile or
graph behavior instead of implying that native checks consume ARI annotations.

Relevant APME implementations: [L052](../../src/apme_engine/graph/rules/L052_galaxy_version_incorrect_graph.py),
[L054](../../src/apme_engine/graph/rules/L054_meta_no_tags_graph.py),
[L056](../../src/apme_engine/graph/rules/L056_sanity_graph.py),
[L095](../../src/apme_engine/graph/rules/L095_schema_validation_graph.py),
[L058](../../src/apme_engine/validators/ansible/rules/L058_argspec_doc.py), and
[L059](../../src/apme_engine/validators/ansible/rules/L059_argspec_mock.py).

## Follow-up status and remaining work

- **Implemented in this working tree:** semantic L037 resolution, R118 risk
  annotations from APME's inbound profiles, corrected P001–P004/L059/L088
  documentation, and selected lint rules L112–L114 for VCS defaults, role
  names, and collection versions.
- **Deferred:** ansible-lint sanity-ignore-file validation, broad schema and
  YAML formatting parity, and deeper galaxy metadata checks for tags,
  licenses, dependencies, and placeholder values.
- **Remaining ARI difference:** APME still uses a static module profile map;
  it does not reproduce ARI's annotation registry, full path-containment
  behavior for R103, or optional RAM metadata. R402/R404 remain opt-in.

## Upstream references

- [ansible-lint rule documentation](https://docs.ansible.com/projects/lint/rules/)
- [ansible-lint v26.10.1 rule source](https://github.com/ansible/ansible-lint/tree/v26.10.1/src/ansiblelint/rules)
- [Ansible Risk Insights README and model](https://github.com/ansible/ansible-risk-insight/blob/main/README.md)
- [Ansible Risk Insights rules](https://github.com/ansible/ansible-risk-insight/tree/18cbf07185a4b9cf209b40b470a92ac2393db1b6/ansible_risk_insight/rules)
- [Ansible Risk Insights annotators](https://github.com/ansible/ansible-risk-insight/tree/18cbf07185a4b9cf209b40b470a92ac2393db1b6/ansible_risk_insight/annotators)
