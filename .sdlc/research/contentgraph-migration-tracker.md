# ContentGraph Rule Migration Tracker

**Status**: Phase 2 Rule Porting Complete (2 deferred to Phase 3)
**Related**: [ADR-044](/.sdlc/adrs/ADR-044-node-identity-progression-model.md) |
[Research](/.sdlc/research/ari-to-contentgraph-migration.md)

## Summary

| Metric | Count |
|--------|-------|
| Total native rules | 96 |
| Ported to GraphRule | 85 |
| Skipped (N/A) | 9 |
| Deferred (Phase 3) | 2 |
| Remaining | 0 |
| Migration % | 88.5% |

## Ported Rules (85)

### Phase 2A — Scanner bootstrap + R108 (PR #138)

| Rule | Name | Category |
|------|------|----------|
| R108 | PrivilegeEscalation | INHERITED_PROPERTY |

### Phase 2B — INHERITED_PROPERTY batch (PR #140)

| Rule | Name | Category |
|------|------|----------|
| L033 | UnconditionalOverride | INHERITED_PROPERTY |
| L045 | InlineEnvVar | INHERITED_PROPERTY |
| L047 | NoLogPassword | INHERITED_PROPERTY |
| L049 | LoopVarPrefix | INHERITED_PROPERTY |
| M010 | Python2Interpreter | INHERITED_PROPERTY |
| M022 | TreeOnelineCallbackPlugins | INHERITED_PROPERTY |
| M026 | InvalidInventoryVariableNames | INHERITED_PROPERTY |
| M030 | BrokenConditionalExpressions | INHERITED_PROPERTY |

### Phase 2C — SCOPE_AWARE batch (PR #141)

| Rule | Name | Category |
|------|------|----------|
| L032 | ChangedDataDependence | SCOPE_AWARE |
| L034 | UnusedOverride | SCOPE_AWARE |
| L042 | Complexity | SCOPE_AWARE |
| L086 | PlayVarsUsage | SCOPE_AWARE |
| L093 | SetFactOverride | SCOPE_AWARE |
| L097 | NameUnique | SCOPE_AWARE |
| M005 | DataTagging | SCOPE_AWARE |
| R117 | ExternalRole | SCOPE_AWARE |

### Phase 2D — TASK_LOCAL batch 1 (PR #143)

| Rule | Name | Category |
|------|------|----------|
| L026 | NonFQCNUse | TASK_LOCAL |
| L030 | NonBuiltinUse | TASK_LOCAL |
| L036 | UnnecessaryIncludeVars | TASK_LOCAL |
| L044 | AvoidImplicit | TASK_LOCAL |
| L048 | NoSameOwner | TASK_LOCAL |
| L074 | NoDashesInRoleName | TASK_LOCAL |
| L081 | NumberedNames | TASK_LOCAL |
| L082 | TemplateJ2Ext | TASK_LOCAL |
| L084 | SubtaskPrefix | TASK_LOCAL |
| L092 | LoopVarInName | TASK_LOCAL |

### Phase 2E — TASK_LOCAL batch 2 (PR #145)

| Rule | Name | Category |
|------|------|----------|
| L037 | UnresolvedModule | TASK_LOCAL |
| L038 | UnresolvedRole | TASK_LOCAL |
| L075 | AnsibleManaged | TASK_LOCAL |
| L080 | InternalVarPrefix | TASK_LOCAL |
| L085 | RolePathInclude | TASK_LOCAL |
| L100 | VarNamingKeyword | TASK_LOCAL |
| L101 | VarNamingReserved | TASK_LOCAL |
| L102 | VarNamingReadOnly | TASK_LOCAL |
| M027 | LegacyKvMergedWithArgs | TASK_LOCAL |

### Phase 2F — Role-metadata batch (PR #145)

| Rule | Name | Category |
|------|------|----------|
| L027 | RoleWithoutMetadata | ROLE_METADATA |
| L052 | GalaxyVersionIncorrect | ROLE_METADATA |
| L053 | MetaIncorrect | ROLE_METADATA |
| L054 | MetaNoTags | ROLE_METADATA |
| L055 | MetaVideoLinks | ROLE_METADATA |
| L077 | RoleArgSpecs | ROLE_METADATA |
| L079 | RoleVarPrefix | ROLE_METADATA |

### Phase 2G — module_options-based batch (PR #147)

| Rule | Name | Category |
|------|------|----------|
| L035 | UnnecessarySetFact | TASK_LOCAL |
| L046 | NoFreeForm | TASK_LOCAL |
| R111 | ParameterizedImportRole | TASK_LOCAL |
| R112 | ParameterizedImportTaskfile | TASK_LOCAL |

### Phase 2H — yaml_lines extension + content rules (PR #147)

| Rule | Name | Category |
|------|------|----------|
| L040 | NoTabs | TASK_LOCAL |
| L041 | KeyOrder | TASK_LOCAL |
| L043 | DeprecatedBareVars | TASK_LOCAL |
| L051 | Jinja | TASK_LOCAL |
| L060 | LineLength | TASK_LOCAL |
| L073 | Indentation | TASK_LOCAL |
| L076 | AnsibleFactsBracket | TASK_LOCAL |
| L078 | DotNotation | TASK_LOCAL |
| L083 | HardcodedGroup | TASK_LOCAL |
| L091 | BoolFilter | TASK_LOCAL |
| L094 | DynamicTemplateDate | TASK_LOCAL |
| L098 | YamlKeyDuplicates | TASK_LOCAL |
| L099 | YamlQuotedStrings | TASK_LOCAL |
| M014 | TopLevelFactVariables | TASK_LOCAL |
| M015 | PlayHostsMagicVariable | TASK_LOCAL |
| M019 | OmapPairsYamlTags | TASK_LOCAL |
| M020 | VaultEncryptedTag | TASK_LOCAL |

### Phase 2I — Annotation rules via module_options (PR #149)

Strategy: Inline module-options field mapping + `is_templated()` heuristic
instead of wiring the legacy annotation pipeline. Shared infrastructure
in `_module_risk_mapping.py` provides FQCN-to-semantic-field lookup.

| Rule | Name | Category |
|------|------|----------|
| R101 | CommandExec | TASK_LOCAL |
| R103 | DownloadExec | CROSS_TASK |
| R104 | InvalidDownloadSource | TASK_LOCAL |
| R105 | OutboundTransfer | TASK_LOCAL |
| R106 | InboundTransfer | TASK_LOCAL |
| R107 | InsecurePkgInstall | TASK_LOCAL |
| R109 | ConfigChange | TASK_LOCAL |
| R113 | PkgInstall | TASK_LOCAL |
| R114 | FileChange | TASK_LOCAL |
| R115 | FileDeletion | TASK_LOCAL |

### Phase 2I — Skipped (N/A)

P001-P004 are severity NONE annotation producers with no user-facing
findings. Their function is subsumed by R-rules reading `module_options`
directly.

| Rule | Name | Reason |
|------|------|--------|
| P001 | ModuleNameValidation | Annotation producer, severity NONE |
| P002 | ModuleArgumentKeyValidation | Annotation producer, severity NONE |
| P003 | ModuleArgumentValueValidation | Annotation producer, severity NONE |
| P004 | VariableValidation | Annotation producer, severity NONE |

### Phase 2J+K — Remaining rules: sanity, aggregation, stubs (PR #150)

Strategy: L056 and R401 are fully functional. Nine collection/plugin rules
are ported as `match()=False` stubs for behavioral parity — they become
active once COLLECTION/PLUGIN node types are populated in the graph.

| Rule | Name | Category | Notes |
|------|------|----------|-------|
| L056 | Sanity | TASK_LOCAL | `file_path` regex against ignore patterns |
| R401 | ListAllInboundSrc | CROSS_TASK | Walks PLAYBOOK descendants for inbound `src` values |
| L087 | CollectionLicense | STUB | `match()=False` — awaits COLLECTION file listing |
| L088 | CollectionReadme | STUB | `match()=False` — awaits COLLECTION file listing |
| L089 | PluginTypeHints | STUB | `match()=False` — awaits plugin Python content |
| L090 | PluginFileSize | STUB | `match()=False` — awaits plugin Python content |
| L095 | SchemaValidation | STUB | `match()=False` — awaits play_data / metadata attrs |
| L096 | MetaRuntime | STUB | `match()=False` — awaits COLLECTION metadata |
| L103 | GalaxyChangelog | STUB | `match()=False` — awaits COLLECTION file listing |
| L104 | GalaxyRuntime | STUB | `match()=False` — awaits COLLECTION file listing |
| L105 | GalaxyRepository | STUB | `match()=False` — awaits COLLECTION metadata |

### Phase 2J+K — Skipped (N/A)

| Rule | Name | Reason |
|------|------|--------|
| R402 | ListAllUsedVariables | Severity NONE, aggregation of `variable_use` keys |
| R404 | ShowVariables | Severity NONE, disabled debug rule |
| R501 | DependencySuggestion | Severity NONE, needs `possible_candidates` from collection resolver |
| L031 | FilePermissionRule | Disabled (`enabled=False`), no active implementation |
| M029 | InventoryScriptMissingMeta | Disabled, no implementation |

---

## Deferred Rules (2) — Phase 3 Prerequisites

These rules require the full variable resolution / provenance infrastructure
(Phase 3 of ADR-044). They cannot be ported until `ContentNode` has
`variable_use` and `variable_set` fields populated by the
`VariableProvenanceResolver`.

| Rule | Name | Severity | What it needs |
|------|------|----------|---------------|
| L039 | UndefinedVariable | LOW | `variable_use` with resolution status — needs resolver to mark unknown variables |
| L050 | VarNaming | VERY_LOW | `variable_set` with variable names — needs resolver to enumerate defined variables |

**Tracking**: These rules will be ported as part of Phase 3 work item
"PropertyOrigin consumed by rules" when the variable provenance
infrastructure lands.

## Stub Rules (9) — Activation Checklist

These GraphRules exist but `match()` always returns `False`. Each group
activates when its prerequisite `ContentNode` / `NodeType` extension lands.

### Needs COLLECTION node with file listing

When `GraphBuilder` creates `COLLECTION` nodes and populates a file
listing (e.g., `collection_files: list[str]`), these rules can inspect
the listing for expected files.

| Rule | Name | What it checks |
|------|------|----------------|
| L087 | CollectionLicense | LICENSE or COPYING file exists |
| L088 | CollectionReadme | README file exists |
| L103 | GalaxyChangelog | CHANGELOG file exists |
| L104 | GalaxyRuntime | `meta/runtime.yml` file exists |

### Needs COLLECTION node with parsed metadata

When `GraphBuilder` populates `galaxy.yml` / `meta/runtime.yml` content
on COLLECTION nodes (e.g., via `collection_metadata: dict`), these rules
can validate the metadata.

| Rule | Name | What it checks |
|------|------|----------------|
| L096 | MetaRuntime | `requires_ansible` is a valid version specifier |
| L105 | GalaxyRepository | `galaxy.yml` has a `repository` key |

### Needs plugin/module node with Python content

When `GraphBuilder` creates MODULE / PLUGIN nodes and exposes Python
source content (e.g., `source_content: str` or `source_lines: int`),
these rules can analyze the Python code.

| Rule | Name | What it checks |
|------|------|----------------|
| L089 | PluginTypeHints | Plugin `.py` has return type hints |
| L090 | PluginFileSize | Plugin entry file is not too large |

### Needs play/collection schema data

When `ContentNode` exposes `play_data` and/or structured `metadata`
attributes, this rule can validate schema structure.

| Rule | Name | What it checks |
|------|------|----------------|
| L095 | SchemaValidation | YAML file matches expected schema keys |

---

## Recommended Porting Order

1. ~~**Phase 2E**: Simple task-local batch 2 (9 rules)~~ **DONE**
2. ~~**Phase 2F**: Role-metadata rules (7 rules)~~ **DONE**
3. ~~**Phase 2G**: `module_options`-based rules (4 rules: L035, L046, R111, R112)~~ **DONE**
4. ~~**Phase 2H**: `yaml_lines` extension + content rules (17 rules)~~ **DONE**
5. ~~**Phase 2I**: Annotation rules via module_options (10 rules ported, 4 skipped)~~ **DONE**
6. ~~**Phase 2J+K**: Remaining rules — L056, R401, 9 stubs (11 ported, 5 skipped)~~ **DONE**
7. **Phase 3**: L039, L050 (deferred — require variable provenance resolver)

## ContentNode Extension Checklist

Fields still needed for full migration:

- [x] `yaml_lines: str` — raw YAML source fragment for the node's span
  (Phase 2H: added to `ContentNode`, populated in `GraphBuilder._build_task()` and
  `_build_handler()` from `task.yaml_lines`; 17 rules now use it)
- [x] ~~`raw_args: str | dict`~~ — **NOT NEEDED**: `module_options._raw_params`
  already available (M027, L085 use it); L035, L046 port via `module_options`
- [x] ~~`is_mutable: bool`~~ — **NOT NEEDED as a field**: simple Jinja detection
  (`"{{" in value or "{%" in value`) replaces `TaskCallArg.is_mutable`;
  R111, R112 port with a `_is_templated()` helper
- [ ] `possible_candidates: list[tuple[str,str]]` — resolution candidates (R501)
  (requires collection resolver integration in `GraphBuilder`)
- [x] ~~`annotations: dict[str, object]`~~ — **NOT NEEDED**: Phase 2I rules
  read `module_options` directly via `_module_risk_mapping.py` instead of
  consuming `RiskAnnotation` objects. The annotation pipeline is bypassed.
- [ ] `variable_use: list[VariableRef]` — variables referenced by this node
- [ ] `variable_set: list[VariableRef]` — variables defined by this node
- [ ] `COLLECTION` / `PLUGIN` node types in `NodeType` enum
- [x] ~~`role_files: dict[str, str]`~~ — **NOT NEEDED as a field**: L077 now
  checks the filesystem directly for `meta/argument_specs.yml` (or `.yaml`)
  when `role_metadata` has no `argument_specs`. Parity achieved without
  schema changes. **Assumption**: `node.file_path` for ROLE nodes is relative
  to the scan basedir; the on-disk check assumes CWD is the project root
  (same assumption as CLI and daemon entry-points). If `ContentGraph` later
  gains a `scan_root` attribute, L077 should resolve against it.

---

## ADR-044 Phase Alignment

Cross-reference against the three phases defined in ADR-044:

### Phase 1 — ContentGraph core + validation: COMPLETE

Deliverables: `ContentGraph`, `GraphBuilder`, `GraphRule`, `NodeIdentity`,
`ContentNode`, `NodeType`/`EdgeType` enums, `VariableProvenanceResolver`,
`build_hierarchy_from_graph()`, `tests/fixtures/graph-patterns/` covering
every edge type, `APME_USE_CONTENT_GRAPH` feature flag,
`test_content_graph_shadow.py` structural equivalence.

### Phase 2 — Rules + switchover: RULE PORTING COMPLETE (85 ported, 9 skipped, 2 deferred)

Rule porting follows the priority taxonomy from ADR-044:

| Category | ADR-044 count | Ported | Skipped | Deferred | Status |
|----------|---------------|--------|---------|----------|--------|
| INHERITED_PROPERTY | ~10 | 9 | — | — | Done (Phases 2A-2B) |
| SCOPE_AWARE | ~8 | 8 | — | — | Done (Phase 2C) |
| TASK_LOCAL | ~78 | 61 | 9 | 2 | Phase 2 complete; 2 deferred to Phase 3 |
| ROLE_METADATA | (subset of task-local) | 7 | — | — | Done (Phase 2F) |

End-of-Phase-2 gates (from ADR-044):
- [x] All 96 rules accounted for (85 ported, 9 skipped, 2 deferred to Phase 3)
- [ ] Remove `TreeLoader`, `AnsibleRunContext`, `RunTarget`, `ObjectList`,
      `Context.add()`, old `build_hierarchy_payload()`
- [ ] Remove `APME_USE_CONTENT_GRAPH` feature flag
- [ ] Rewrite `rule_doc_integration_test.py` to use `ContentGraphScanner`
- [ ] Single rule interface (`GraphRule`), zero adapter

### Phase 3 — Progression + provenance: NOT STARTED

Depends on Phase 2 completion. See "Outstanding Work" section below.

---

## Outstanding Work Beyond Rule Porting

### Phase 3 — Progression + Provenance (ADR-044)

After Phase 2 (all rules ported), Phase 3 adds:

1. **NodeState progression**: Record content state at each pipeline phase
   (original → formatted → scanned → transformed → rescanned).
2. **PropertyOrigin consumed by rules**: Already-ported inherited-property
   rules (R108, L047, L045, etc.) use PropertyOrigin to attribute violations
   to the defining scope (play) rather than every inheriting child task.
3. **Gateway ScanSnapshot accumulation**: Gateway persists per-node timeline
   across scans for trend queries.
4. **Enabled capabilities**: Complexity metrics, AI escalation enrichment,
   topology visualization, best-practices pattern rules, dependency quality
   scorecards.

### Rule doc integration tests (short-term gap)

**Problem**: The old `rule_doc_integration_test.py` harness runs rule `.md`
examples through the old pipeline (`run_scan_playbook_yaml`). Scope-aware
and inherited-property graph rules (L097, L093, M005, R117, L034, L086)
added violation examples with `### Violation (context-dependent)` headings
to avoid parser matching, since the old pipeline can't test them.

**Resolution**: When Phase 2 completes and the old pipeline is removed, the
rule doc harness must be rewritten to use `ContentGraphScanner`. At that
point, all `(context-dependent)` headings revert to standard
`### Example: violation` and become testable.

### `tests/fixtures/graph-patterns/` coverage

The comprehensive fixture directory was created in Phase 1 and covers:
handlers/notify, block rescue/always, import_playbook chains, static
roles, import/include tasks/roles, vars_files, host_vars, nested blocks,
collection layout with `galaxy.yml`, Python plugins with `py_imports`.

**Currently used by**: `test_content_graph_shadow.py` (structural
equivalence), `test_python_analyzer.py`.

**Not yet used for**: graph-pattern-specific `GraphBuilder` edge-type
validation tests (e.g., assert every edge type in the taxonomy is produced).

### Old pipeline removal (end of Phase 2)

Once all 96 rules are ported and `ContentGraph` is primary:

- Remove `TreeLoader`, `AnsibleRunContext`, `RunTarget`, `ObjectList`,
  `Context.add()`, old `build_hierarchy_payload()`
- Remove `APME_USE_CONTENT_GRAPH` feature flag
- Rewrite `rule_doc_integration_test.py` to use `ContentGraphScanner`
- Single rule interface (`GraphRule`), zero adapter
