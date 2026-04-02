# NodeState Progression Design

**Status**: Active
**Date**: 2026-03-30
**Related**: [ADR-044](/.sdlc/adrs/ADR-044-node-identity-progression-model.md) | [Migration Research](/.sdlc/research/ari-to-contentgraph-migration.md) | [Migration Tracker](/.sdlc/research/contentgraph-migration-tracker.md)

## Problem

ADR-044 Phase 3 calls for NodeState progression — tracking how each node
evolves through formatting, scanning, and remediation passes. The current
convergence loop rebuilds the entire ARI pipeline from disk on every pass,
writes files between passes, and has no per-node history.

Three representations of the same content are synchronized via disk writes:

1. ARI's in-memory tree (for hierarchy/scandata)
2. `StructuredFile` (ruamel.yaml CommentedMap, for Tier 1 transforms)
3. Raw bytes on disk (for validator fan-out)

This creates the problems described in ADR-044: lost line numbers after
transforms, no snippet accuracy, no remediation attribution, no progression.

## Design Decisions

### 1. ContentGraph is the mutable working copy — files never change until approval

The convergence loop operates entirely in-memory on the ContentGraph.
No files are written to disk during convergence passes. Line numbers
always reference the original file. On approval, modified nodes are
spliced back into files at their original `(line_start, line_end)` ranges.

This is the "puzzle" analogy from ADR-044: each piece (node) carries its
own identity and history. The puzzle can be reassembled at any point.

### 2. Ephemeral CommentedMap for transforms — no persistent ruamel objects

Transforms need `CommentedMap` objects (ruamel.yaml's round-trip AST) to
preserve YAML comments during mutations. But `CommentedMap`s are not
stored on `ContentNode` — they are parsed on demand from `yaml_lines`
when a transform needs to run, and serialized back to `yaml_lines`
immediately after.

The `ContentNode` stores:

- **`yaml_lines`** (str): Raw YAML text for this node's span. This is the
  persistent representation. Comments are embedded in the text.
- **Typed fields** (`module`, `when_expr`, `become`, etc.): Extracted from
  the YAML for graph rule evaluation. Rebuilt after each transform via
  `update_from_yaml()`.

Flow per transform:

```
yaml_lines  -->  ruamel.yaml.load()  -->  CommentedMap (ephemeral)
                                              |
                                         transform mutates
                                              |
CommentedMap  -->  ruamel.yaml.dump()  -->  new yaml_lines
                                              |
new yaml_lines  -->  update_from_yaml()  -->  updated typed fields
```

### 3. Transforms receive CommentedMap directly — no file-level lookup

Current transform signature:

```python
def fix_fqcn(sf: StructuredFile, violation: ViolationDict) -> bool:
    task = sf.find_task(line)  # needle-in-haystack lookup
    # ... mutate task ...
```

New signature:

```python
def fix_fqcn(task: CommentedMap, violation: ViolationDict) -> bool:
    # ... same mutation code, find_task() eliminated ...
```

The `ContentGraph.apply_transform()` method handles the parse/serialize
lifecycle. The transform is a pure data mutation — it does not know
about files, line numbers, or YAML serialization.

### 4. Scoped transforms use CommentedMap node tagging (deferred)

**Implementation status**: Deferred. All 20 current transforms are
leaf-node (single task/handler). The tagging infrastructure described
below will be built when the first scoped (play-level, block-level)
transform is implemented. No machinery without consumers.

For scoped transforms (play-level, block-level) that modify a parent
node's CommentedMap which contains nested child CommentedMaps:

**Before the transform**:

1. Parse the scope's `yaml_lines` -> CommentedMap tree
2. Walk the tree and tag each nested CommentedMap with its node ID:
   ```python
   play_cm._apme_node_id = "site.yml/plays[0]"
   play_cm["tasks"][0]._apme_node_id = "site.yml/plays[0]/tasks[0]"
   play_cm["tasks"][1]._apme_node_id = "site.yml/plays[0]/tasks[1]"
   ```
3. Snapshot each tagged CommentedMap's content hash

**After the transform**:

1. Walk all tagged CommentedMaps
2. Compare content hash to pre-transform snapshot
3. For each dirty node: serialize just that CommentedMap -> new `yaml_lines`,
   call `update_from_yaml()`, mark dirty, record `NodeState`

This avoids serializing the entire scope and re-extracting children by
line range. Each node is independently tracked. The transform doesn't
know or care about node IDs — it just mutates CommentedMaps. The engine
detects what changed.

`CommentedMap` is a regular Python object — custom attributes (like
`_apme_node_id`) persist through mutations. If a transform replaces a
CommentedMap entirely (allocates a new one), the tag is lost but this is
detectable by walking the tree and finding untagged maps where tagged
ones existed before. In practice, all current transforms mutate in place.

### 5. Re-scanning runs graph rules only — no full pipeline rebuild

The initial scan runs the full pipeline: ARI parse -> ContentGraph build ->
validator fan-out (native + OPA + Ansible + Gitleaks).

Convergence re-scans run **only graph rules on dirty nodes**:

```python
def rescan_dirty(graph, rules, dirty_node_ids) -> list[ViolationDict]:
    for node_id in dirty_node_ids:
        for rule in rules:
            if rule.match(graph, node_id):
                result = rule.process(graph, node_id)
                ...
```

This is correct because:

- Tier 1 transforms only fix native rule violations (graph rules)
- Graph rules evaluate on `ContentNode` fields, not files on disk
- External validators (OPA, Ansible, Gitleaks) don't participate in
  Tier 1 convergence
- A final full-pipeline scan can run after convergence if needed

**Performance**: convergence passes drop from O(full pipeline with ARI
rebuild + venv + validator fan-out) to O(graph rules on dirty nodes).

### 6. NodeState records progression at each phase

```python
@dataclass(frozen=True)
class NodeState:
    pass_number: int
    phase: str        # "original", "scanned", "transformed"
    yaml_lines: str   # raw YAML text at this point
    content_hash: str # sha256 of yaml_lines
    violations: tuple[str, ...]  # rule IDs active at this state
    timestamp: str    # ISO 8601
```

Each `ContentNode` gains:

```python
state: NodeState | None = None            # current state
progression: list[NodeState] = field(...)  # full history
```

Progression is recorded at:

1. After initial scan: `NodeState(pass=0, phase="scanned", violations=[...])`
2. After each transform: `NodeState(pass=N, phase="transformed", violations=[])`
3. After each re-scan: `NodeState(pass=N, phase="scanned", violations=[...])`

The full history is available for:

- Snippet accuracy (show content at the exact moment a violation was detected)
- Remediation attribution (which transform resolved which violation)
- Feedback quality (full node timeline for debugging)
- Gateway persistence (emitted via GrpcReportingSink)

### 7. File splice at the end — bottom-up to preserve offsets

On approval, modified nodes are written back to files:

1. Group modified nodes by `file_path`
2. Sort by `line_start` descending (bottom-up)
3. For each node: replace lines `[line_start:line_end]` with final `yaml_lines`
4. Write modified files to disk
5. Generate unified diffs (original vs modified)

Bottom-up ordering ensures that splicing a node doesn't shift line numbers
for nodes above it in the same file.

## Implementation Plan

### PR 1: NodeState data model + update_from_yaml — MERGED (#194)

- `NodeState` frozen dataclass with pass_number, phase, yaml_lines,
  content_hash, violations, timestamp
- `state` and `progression` fields on `ContentNode`
- `ContentNode.record_state()` and `ContentNode.update_from_yaml()`
- `_apply_parsed_fields()` rebuilds node.options and normalizes
  non-dict module_options to `{"_raw": value}`
- `_node_from_dict()` reconciles state/progression (progression is
  source of truth, state == progression[-1])
- Serialization in `to_dict()` / `from_dict()`
- 35 unit tests (29 original + 6 from Copilot review)

### PR 2: Node-level transform contract + migration — MERGED (#195)

- `NodeTransformFn = Callable[[CommentedMap, ViolationDict], bool]`
- `TransformRegistry` gains `_node` dict, `register(node=...)`,
  `apply_node()`, `get_node_transform()`
- `ContentGraph.apply_transform()` — ephemeral CommentedMap lifecycle:
  parse yaml_lines → transform → serialize (explicit_start=False) →
  update_from_yaml()
- `ContentGraph.dirty_nodes` / `clear_dirty()` for convergence tracking
- All 17 structured transforms migrated to `(task: CommentedMap, ...)`
  signature (L020 retains legacy string path)
- `apply_structured()` wraps node transforms with `find_task` for
  backward compat (existing RemediationEngine unchanged)
- 46 unit tests in test_node_state.py; 161 remediation tests pass

### PR 3: Graph-aware convergence loop — IN PROGRESS

- [x] `graph_scanner.rescan_dirty()` — incremental re-validation of
  dirty nodes only (skips full-graph scan; O(dirty nodes × rules))
- [x] `GraphRemediationEngine` — in-memory convergence loop on
  ContentGraph: scan → transform → rescan_dirty → repeat. No disk I/O.
  Records NodeState progression at each phase. Oscillation detection.
- [x] `splice_modifications()` — bottom-up line-range replacement to
  produce FilePatch diffs from original files
- [x] `_record_violations()` + `_count_modified_nodes()` helpers
- [x] 17 unit tests: 4 rescan_dirty, 8 GraphRemediationEngine, 5 splice
- [x] Lint clean (prek, mypy, pydoclint all pass)
- [ ] Primary server integration (deferred — separate PR scope)

## Out of Scope

- **Gateway ScanSnapshot accumulation** — Gateway persists per-node
  progression timelines across sessions. This is Gateway scope.
- **Proto changes for progression** — `FixCompletedEvent` gains
  progression fields once Gateway is ready to consume them.
- **AI Tier 2 integration** — AI provider receives node context from
  progression. Separate PR.
- **Enabled capabilities** — complexity metrics, topology visualization,
  best-practices patterns. These consume the graph but don't require
  progression changes.

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-30 | Bradley A. Thornton | Initial design from architecture discussion |
| 2026-04-01 | Bradley A. Thornton | PR 1 merged (#194): NodeState data model |
| 2026-04-02 | Bradley A. Thornton | PR 2 merged (#195): Node-level transform contract |
| 2026-04-02 | Bradley A. Thornton | PR 3 in progress: Graph-aware convergence loop |
