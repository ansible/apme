# ADR-044: Node Identity and Progression Model

## Status

Proposed

## Date

2026-03-27

## Context

APME's convergence loop (format → scan → transform → rescan, up to 5 passes) treats each scan as a stateless snapshot. The ARI engine, integrated per ADR-003, rebuilds the entire tree from scratch on every pass. No node carries identity across passes and no node records what happened to it over time.

This creates a two-dimensional problem that the current architecture models in only one dimension:

- **Vertical (hierarchy)**: The tree of plays, roles, blocks, tasks — well-modeled by ARI.
- **Horizontal (progression)**: How each node changes through formatting, Tier 1 transforms, AI proposals, and re-scans — not modeled at all.

### Where the gap hurts today

1. **Snippet accuracy**: Source snippets must show the file content at the moment a violation was found. The current `_attach_snippets` implementation uses `File` protos from the scan pipeline, which contain post-format content. Line numbers from validators reference the same post-format files. The snippets are internally consistent but show the user transformed code they didn't write, with no way to trace back to the original.

2. **Violation identity across passes**: Violations are matched between passes by `(rule_id, file, line)` tuples. After a Tier 1 transform shifts line numbers, this heuristic breaks. The remediation engine uses YAML paths as a more stable proxy (`NodeIndex`), but this is a workaround bolted onto a model that lacks first-class identity.

3. **Remediation attribution**: "Which transform fixed which violation?" is answered by inference (diff the violation sets between passes), not by direct tracking. There is no per-node change log.

4. **Parallel representations**: The same content exists in three forms — ARI's in-memory tree (for hierarchy/scandata), `StructuredFile` (ruamel.yaml, for Tier 1 transforms), and raw bytes on disk (for validator fan-out). These are synchronized by writing to disk and re-parsing, not by a shared identity model.

5. **Feedback quality**: When a user reports a false positive, we cannot include "this node was formatted on pass 0, violation V detected on pass 1, transform T attempted on pass 2, violation persisted on pass 3" because that history doesn't exist.

6. **Inherited property attribution**: ARI accumulates inherited properties (e.g. `become`, `variable_use`) from parent scopes (play, block) onto child tasks. Rules like R108 (privilege escalation) and L050 (variable naming) detect these inherited properties but attribute violations to the child task's start line — not to the scope where the property was defined. The result: a task with no `become:` keyword is highlighted for privilege escalation because it inherits `become` from its play; a task with no variables is flagged for variable naming because play-level vars like `MyAppVersion` are in scope. The user sees a highlighted line with no visible connection to the violation message. Without node identity and parent-child relationships, there is no way to attribute a violation to the defining scope ("inherited from play at line 3") rather than every inheriting task.

### The puzzle piece analogy

Consider 100 uniquely shaped puzzle pieces handed to 100 people who each make a change and document the color. At any point the puzzle can be reassembled. When everyone is done, every piece has a history of progression. The puzzle's integrity is preserved because identity is intrinsic to each piece, not derived from its position.

APME's current model is: disassemble the puzzle, throw away all the pieces, rebuild new pieces from the table surface, and hope the positions match.

### Lessons from ansible-core's object model

Ansible-core (`lib/ansible/playbook/`) maintains a proper hierarchy with explicit parent pointers — `Task._parent → Block`, `Block._parent → Block`, `Block._play → Play`, `Block._role → Role`, `Block._dep_chain → [Role]`. Property inheritance uses `FieldAttribute` descriptors whose `__get__` calls `_get_parent_attribute()`, which walks the full parent chain on every read. This means ansible-core always knows where an inherited value originates — it just never exposes that provenance as data.

Key patterns to adopt or avoid:

1. **`_get_parent_attribute()` is the `PropertyOrigin` blueprint.** The chain walk (Task → Block → parent Block → Role → dep chain → Play) preserves provenance at read time. ARI's scandata accumulation is equivalent to ansible-core's `squash()` — it materializes inherited values and discards the chain. ADR-044's model must never squash without recording origin.

2. **`_uuid` is process-local and ephemeral.** ansible-core assigns a random-ish ID at construction (`get_unique_id()` — MAC prefix + monotonic counter). It is not stable across runs or processes. ADR-044's YAML-path-based `NodeIdentity` is strictly more stable.

3. **Static vs dynamic includes create a partially-known tree.** `import_tasks` / `import_role` expand at parse time — fully resolved before execution. `include_tasks` / `include_role` remain as placeholder nodes until execution reaches them, and are host-divergent (different hosts may include different files). The effective tree is never globally complete. APME scans content structurally, not per-host, so dynamic includes are leaf nodes with unknown children.

4. **`when:` on static imports gates the expanded tasks, not the import itself.** The file is always loaded; the condition is attached as a parent and inherited by every expanded task. For dynamic includes, `when:` is evaluated first — if false, the file is never loaded.

5. **`NonInheritableFieldAttribute` distinguishes scope-sensitive from scope-insensitive properties.** `name` and `vars` are explicitly non-inheritable in ansible-core. This maps directly to ADR-043's scope-aware severity: only inheritable properties (become, ignore_errors) should escalate severity at broader scope; non-inheritable properties (name) should not.

### Decision drivers

- Remediation convergence requires tracking "same node, different state" across passes
- User-facing features (snippets, feedback, audit trails) need temporal context
- The formatter, scan engine, and remediation engine should share one model, not three
- ARI's parsing and hierarchy logic is valuable; its stateless snapshot model is not
- Inherited properties (become, vars) must be attributable to their defining scope, not every inheriting child
- ansible-core's `_get_parent_attribute()` chain demonstrates how to preserve provenance; ARI's accumulation model destroys it

## Decision

**We will build a purpose-built Node Identity and Progression Model that wraps ARI's parsing capabilities in an entity-with-history abstraction.**

Each meaningful unit of Ansible content (task, play, block, role reference, variable declaration) receives a stable identity at parse time. That identity persists through formatting, scanning, and remediation passes. Each node accumulates a progression log of state changes.

### Core concepts

**NodeIdentity**: A stable identifier derived from the node's structural position (YAML path) in the original, pre-format content. Identity is assigned once and never changes, even as line numbers shift.

**NodeState**: The content, violations, and metadata of a node at a specific point in time. Immutable once created.

**Progression**: An ordered sequence of `NodeState` entries for a single `NodeIdentity`, representing how that node evolved through the pipeline.

**ContentGraph**: The top-level container — a directed acyclic graph (DAG) of identified nodes with their progressions, parent-child relationships, and include edges. Replaces the current pattern of disconnected ARI tree + StructuredFile + file bytes. The graph is a DAG, not a tree, because roles and task files can be included by multiple parents — a role used by three playbooks exists once in the graph with three incoming include edges, not three copies.

**NodeScope**: Each node carries an ownership scope — `owned` (inside the scan boundary, eligible for violations and remediation) or `referenced` (resolved for context and inheritance, with any detected violations treated as advisory-only and excluded from automated remediation; referenced content is never modified). When scanning a collection, the collection's own roles and playbooks are `owned`; dependencies from Galaxy are `referenced`. The scan boundary is determined by what was submitted for analysis.

**PropertyOrigin**: When a node carries an inherited property (e.g. `become`, variables), the graph tracks the `NodeIdentity` of the defining scope — modeled after ansible-core's `_get_parent_attribute()` chain walk. Violations on inherited properties reference both the affected task and the origin node, enabling messages like "Privilege escalation inherited from play at site.yml:3" rather than attributing to the task's own line.

### Pipeline with progression

```
Phase 0 — Parse original files
  → Assign NodeIdentity to every node
  → Record NodeState[0]: original content, no violations

Phase 1 — Format
  → Apply formatter to each node's content
  → Record NodeState[1]: formatted content, diff from original

Phase 2..N — Scan + Transform (convergence)
  → ARI parse of current content (reusing its hierarchy/scandata logic)
  → Map ARI results back to identified nodes (by YAML path)
  → Record NodeState[N]: violations detected
  → Apply Tier 1 transforms
  → Record NodeState[N+1]: post-transform content, which violations resolved
  → Re-scan for convergence check
  → Continue until stable

Phase Final — Classification
  → Each node's progression is complete
  → Remaining violations carry their full history
  → Snippets are trivially extracted from any NodeState in the progression
```

### Relationship to ARI

ARI remains the parser and hierarchy builder. Its `run_scan` produces the hierarchy payload and scandata that validators consume. The change is:

- **Before**: ARI's output is the truth; files on disk are synchronized to match
- **After**: The `ContentGraph` is the truth; ARI is invoked as a service to parse content and produce hierarchy, but its output is mapped back onto identified nodes rather than used as the canonical model

This preserves ARI's valuable parsing logic while decoupling APME from ARI's stateless snapshot assumption.

## Alternatives Considered

### Alternative 1: Extend ARI with identity tracking

**Description**: Modify the vendored ARI engine internals to assign stable node IDs and carry them across `evaluate()` calls.

**Pros**:
- Single model (ARI's tree gains identity)
- No new abstraction layer

**Cons**:
- Deep coupling to ARI internals that were not designed for this
- ARI's `evaluate()` rebuilds trees from scratch by design — retrofitting identity means fighting its architecture
- Makes future ARI updates (porting upstream improvements) much harder
- ARI's node model (scandata, AnsibleRunContext) is optimized for rule evaluation, not lifecycle tracking

**Why not chosen**: Retrofitting identity into a stateless-by-design system creates more complexity than building a clean abstraction. Two workarounds for the same interface means redesign the interface.

### Alternative 2: Thin identity layer between ARI and remediation

**Description**: Keep ARI as-is. Build a `NodeRegistry` that maps YAML paths to stable IDs and maintains progression logs outside ARI. The existing `NodeIndex` is a primitive version of this.

**Pros**:
- Minimal changes to ARI
- Incremental adoption — can add identity tracking without rewriting the pipeline
- Lower initial effort

**Cons**:
- Two sources of truth (ARI's tree + the registry) that must be kept in sync
- YAML-path-based identity is fragile when transforms restructure content
- The three-representation problem (ARI tree, StructuredFile, file bytes) persists
- Every new feature must bridge between ARI's model and the registry

**Why not chosen**: This is the path of least resistance but accumulates the most long-term debt. It codifies the current workaround pattern rather than resolving the underlying model mismatch. Viable as a stepping stone but not as the target architecture.

### Alternative 3: Purpose-built model wrapping ARI (chosen)

**Description**: Build a `ContentGraph` that owns node identity and progression. Use ARI's parsing as an internal service for hierarchy building and rule evaluation, but map results back onto the graph rather than treating ARI's output as the canonical model.

**Pros**:
- Clean single source of truth for node identity, content, and history
- Snippets, attribution, and feedback are natural properties of the model
- Eliminates the three-representation synchronization problem
- ARI's parsing logic is preserved without coupling to its lifecycle assumptions
- Clearer design — the model matches the problem domain

**Cons**:
- Significant implementation effort
- Requires careful migration of existing pipeline code
- ARI integration becomes an adapter layer rather than direct use
- Risk of over-engineering if progression tracking proves less valuable than anticipated

## Consequences

### Positive

- Every violation carries a snippet from the exact content state when it was detected
- Violation identity is stable across passes — no more (file, line) heuristic matching
- Remediation attribution is explicit: "Transform T resolved violation V on node N at pass P"
- Feedback issues include full node progression (original → formatted → scanned → transformed)
- Single model serves parsing, validation, remediation, and reporting
- DAG structure prevents duplicate violations and duplicate remediation for shared content (roles used by multiple playbooks)
- Ownership borders cleanly separate "your code" from "your dependencies" without excluding dependencies from the analysis context
- Formatter changes become trackable events, not invisible preprocessing
- Inherited property violations reference their defining scope, not every inheriting child
- Plays and playbooks become first-class violation targets — an R108 "this play enables privilege escalation" fires once on the play node instead of once per inheriting task, significantly reducing noise
- UI can render violations hierarchically (Playbook > Play > Block > Task) with scope-appropriate snippets (play header for play-level violations, task body for task-level)

### Negative

- Large implementation effort spanning engine, remediation, and primary server
- ARI becomes an internal service with an adapter, adding indirection
- Migration must be carefully staged to avoid breaking the existing pipeline
- Additional memory for storing node progressions (bounded by pass count × node count)

### Neutral

- ARI's parsing and hierarchy-building code is unchanged — only its consumption model changes
- The gRPC contract between Primary and validators is unaffected (validators still receive `ValidateRequest`)
- The `Violation` proto gains a stable `node_id` field but remains backward-compatible

## Implementation Notes

### Phased adoption

The migration uses three phases with zero throwaway code. No intermediate adapter layer is built — the graph and rule porting happen together so every artifact is permanent.

1. **Phase 1 — ContentGraph core + validation**: Build comprehensive graph-pattern test fixtures (`tests/fixtures/graph-patterns/`) covering every edge type (handlers, rescue/always, import_playbook, import_tasks, import_role, static roles, vars_files, host_vars, nested blocks, collection layout with `galaxy.yml`). Build the ContentGraph, `GraphBuilder`, `VariableProvenanceResolver`, `build_hierarchy_from_graph()`, and `GraphRule` base class. Run in parallel behind a feature flag, validating structural equivalence against the existing `TreeLoader` and `build_hierarchy_payload()` outputs. Add `node_id` to the `Violation` proto in `common.proto`. The old pipeline remains primary — nothing switches over.

2. **Phase 2 — Rules + switchover**: Port **all** ~96 native rules to the unified `GraphRule` interface and switch `ContentGraph` as the primary model. Annotators adapted to read from `ContentNode` directly. Once stable, remove `TreeLoader`, `AnsibleRunContext`, `RunTarget`, `ObjectList`, `Context.add()`, and the old `build_hierarchy_payload()`. One rule interface, zero adapter, zero throwaway code.

   Rule classification determines porting **priority**, not porting **scope** — all rules are ported:

   | Category | Count | Examples | Porting priority |
   |----------|-------|----------|-----------------|
   | INHERITED_PROPERTY | ~10 | R108 (become), L047 (no_log), L045 (environment), L033/M030 (when), M010 (interpreter), M026 (vars) | First — algorithmic change, high value |
   | SCOPE_AWARE | ~8 | L042 (complexity), L097 (name uniqueness), L086 (play vars), L032/L034 (variable precedence), R117 (external role) | Second — algorithmic change, medium value |
   | TASK_LOCAL | ~78 | R101–R115 (risk), L031 (permissions), L046 (free-form), P001–P004 (policy), all module-arg rules | Last — mechanical signature change (`ctx.current` → `graph.get_node(node_id)`), no algorithmic change |

3. **Phase 3 — Progression + provenance**: Record `NodeState` at each pipeline phase. `PropertyOrigin` consumed by the already-ported rules. Gateway accumulates `ScanSnapshot` per node across scans. Complexity metrics, AI escalation enrichment, topology visualization, best-practices pattern rules, dependency quality scorecards.

### NodeIdentity derivation

```
<file-path>::<yaml-path>

Examples:
  site.yml::play[0]#task[3]
  roles/web/tasks/main.yml::task[0]
  site.yml::play[1]#block[0]#task[2]
```

YAML path is structural (based on node type and position), not content-dependent. It is assigned from the original file before any formatting and remains stable through content transforms that don't restructure the document.

### Snippet extraction with progression

```python
# At any point, a violation's snippet is trivially available:
node = content_graph.get(violation.node_id)
state = node.state_at(pass_number)  # or node.state_when_detected(violation)
snippet = state.content_lines(line - 10, line + 10)
```

### Scope-level violations and noise reduction

With node identity, rules that detect inherited properties can target the defining scope:

```python
# R108 today: fires on every task that inherits become (50 violations)
Violation(rule_id="R108", node_id="site.yml::play[0]#task[3]", line=30)
Violation(rule_id="R108", node_id="site.yml::play[0]#task[4]", line=37)
# ... 48 more

# R108 with ContentGraph: fires once on the play (1 violation)
Violation(
    rule_id="R108",
    node_id="site.yml::play[0]",
    line=3,
    message="Play enables privilege escalation (become_user: deployer)",
    affected_children=50,  # informational count
)
```

The UI renders play-level violations with the play header as the snippet (hosts, vars, become directives), giving the user immediate context for why the violation exists and where to fix it.

### Graph topology: DAG with ownership borders

The `ContentGraph` is a DAG, not a tree. Include/import directives create edges, not copies:

```
site.yml::play[0]
  ├── include_role: web         ──→ roles/web/tasks/main.yml (owned)
  └── include_role: common      ──→ roles/common/tasks/main.yml (owned)

deploy.yml::play[0]
  ├── include_role: web         ──→ roles/web/tasks/main.yml (same node)
  └── include_role: monitoring  ──→ galaxy.namespace.monitoring (referenced)
```

`roles/web` appears once in the graph with two incoming edges. A violation against it is reported once. A remediation fix is applied once. Both including playbooks benefit.

**Static imports** (`import_tasks`, `import_role`) are fully resolved at parse time — their content is materialized in the graph with the import directive as the include edge.

**Dynamic includes** (`include_tasks`, `include_role`) are modeled with a worst-case complexity policy: all include/import directives produce edges unconditionally. A `when` clause on an include is an edge attribute (`conditional: true`), not a gate — the edge always exists because the condition *can* be true and the included content *can* execute. For complexity measurement and violation coverage, every reachable path must be in the graph.

Variable-path includes (e.g., `include_tasks: "{{ task_file }}"`) resolve all statically determinable targets and create an edge to each. If the possible values are known from a `loop` list or `vars` definition, all targets are included. Truly opaque variable paths (runtime facts, inventory-derived) are flagged as high-complexity nodes with a `dynamic: true` attribute.

**`import_playbook`** creates cross-playbook edges. A playbook can be both a root (standalone entry point) and an interior node (imported by another playbook). The DAG handles this naturally — `site.yml` may have incoming edges from `master.yml` and outgoing edges to its own roles. Complexity rollup is transitive: `master.yml`'s complexity includes `site.yml`'s subgraph.

**Ownership borders** determine the scan boundary:

- `owned`: content submitted for analysis. Violations reported, remediation applied, progression tracked.
- `referenced`: resolved for context (so property inheritance and variable resolution work). Violations are advisory-only (visible for dependency quality assessment but excluded from automated remediation). Content never modified.

The border is set by what's submitted: scanning a role in isolation makes the role `owned` and its dependencies `referenced`. Scanning a collection makes all sibling roles `owned`. This is a property of the scan session, not intrinsic to the node — the same role can be `owned` in one scan and `referenced` in another.

**FQCN-aware ownership for collections**: Content within an Ansible collection references siblings via fully qualified collection names (e.g., `include_role: name=acme.webstack.common` from within the `acme.webstack` collection). These look syntactically identical to external references. The graph builder must read `galaxy.yml` to determine the collection's `namespace.name` and classify FQCNs matching that identity as `owned` self-references, not `referenced` external dependencies. Without this, a collection's internal roles would be treated as external and excluded from violation reporting and remediation.

```
Scanning: acme.webstack (galaxy.yml: namespace=acme, name=webstack)

acme.webstack.deploy       → owned (same collection)
acme.webstack.common       → owned (same collection)
ansible.builtin.copy       → referenced (stdlib)
community.general.ufw      → referenced (declared dependency)
```

### Compatibility

- `NodeIndex` (current YAML-path lookup in `engine/node_index.py`) evolves into `ContentGraph`
- `StructuredFile` (ruamel.yaml) becomes the serialization layer for `NodeState`, not a parallel model
- `_attach_snippets` is replaced by node-level state queries
- The gRPC `ValidateRequest` contract is unchanged — validators still receive files and hierarchy
- `AnsibleRunContext`, `RunTarget`, and `ObjectList` are replaced by `GraphRule` + `ContentGraph` in Phase 2. No intermediate adapter is built — rules are ported directly to the graph interface as part of the switchover, eliminating the old model entirely

### Graph implementation: networkx MultiDiGraph

The `ContentGraph` will be implemented as a `networkx.MultiDiGraph`. `MultiDiGraph` is required because the same node pair can have multiple edges — a role included twice from the same play with different `when` conditions creates two distinct edges, each with its own attributes. A standard `DiGraph` only permits one edge per node pair. Adding `networkx` as a runtime dependency is part of implementing this ADR.

networkx is pure Python (~3 MB installed), has no compiled extensions, and passes the ADR-019 dependency checklist: it solves a genuinely hard problem (graph algorithms), ships `py.typed` for mypy strict, is Apache-2.0 licensed, and has no overlap with existing deps.

Built-in algorithms the ContentGraph will use:

- `is_directed_acyclic_graph()` — validation invariant (content cannot have circular includes)
- `topological_sort()` — determines processing order
- `weakly_connected_components()` — identifies independent content clusters (repos with multiple unrelated playbooks)
- `ancestors()` / `descendants()` — subgraph extraction for per-play or per-role analysis
- `in_degree()` / `out_degree()` — fan-in/fan-out metrics
- `node_link_data()` / `node_link_graph()` — JSON serialization for Gateway API and persistence

In memory, node and edge attributes may hold arbitrary Python objects for rich metadata (violations, variable provenance, quality scores). However, attributes persisted via `node_link_data()` / `node_link_graph()` must be JSON-serializable, so a normalization layer converts complex objects (dataclasses, datetimes) into JSON-friendly shapes before serialization to the Gateway.

### Node type and edge type taxonomy

**Node types** define the vocabulary of the graph. Every node has a `node_type` attribute:

| Node type | Description | Example path |
|-----------|-------------|--------------|
| `playbook` | A YAML playbook file (graph root or interior via `import_playbook`) | `site.yml` |
| `play` | A play within a playbook | `site.yml::play[0]` |
| `role` | A role (directory with `tasks/main.yml`) | `roles/nginx` |
| `taskfile` | A task file (standalone or within a role) | `roles/nginx/tasks/install.yml` |
| `task` | An individual task | `roles/nginx/tasks/main.yml::task[2]` |
| `handler` | A handler (structurally a task, semantically a deferred branch target) | `roles/nginx/handlers/main.yml::handler[0]` |
| `block` | A `block/rescue/always` group | `site.yml::play[0]#block[1]` |
| `module` | A Python module (`library/` or `plugins/modules/`) | `plugins/modules/my_module.py` |
| `action_plugin` | An action plugin (`plugins/action/`) | `plugins/action/my_action.py` |
| `filter_plugin` | A Jinja2 filter plugin (`plugins/filter/`) | `plugins/filter/my_filter.py` |
| `lookup_plugin` | A lookup plugin (`plugins/lookup/`) | `plugins/lookup/my_lookup.py` |
| `module_utils` | Shared Python code (`plugins/module_utils/`) | `plugins/module_utils/helpers.py` |
| `vars_file` | A variables file (`defaults/`, `vars/`, `group_vars/`, `host_vars/`) | `roles/nginx/defaults/main.yml` |

**Edge types** define relationships between nodes. Every edge has an `edge_type` attribute:

| Edge type | Meaning | Example |
|-----------|---------|---------|
| `import` | Static inclusion, resolved at parse time | `import_tasks`, `import_role`, `import_playbook` |
| `include` | Dynamic inclusion, resolved at runtime | `include_tasks`, `include_role` |
| `notify` | Deferred handler invocation, conditional on task `changed` status | `notify: restart nginx` |
| `listen` | Topic-based handler subscription (implicit fan-out) | `listen: "restart web services"` |
| `dependency` | Role dependency declared in `meta/main.yml` | `dependencies: [{role: common}]` |
| `data_flow` | Variable produced by one task, consumed by another (`set_fact`/`register`) | `register: result` → `when: result.rc == 0` |
| `rescue` | Exception path from block to rescue tasks | `block → rescue` |
| `always` | Unconditional path from block to always tasks | `block → always` |
| `invokes` | Task invoking a Python module or plugin | `ansible.builtin.copy` task → `copy.py` |
| `py_imports` | Python module importing from module_utils | `my_module.py` → `module_utils/helpers.py` |
| `vars_include` | Variable file inclusion (`vars_files`, `include_vars`) | play → `vars/secrets.yml` |

**Edge attributes** carried on every edge:

| Attribute | Type | Description |
|-----------|------|-------------|
| `edge_type` | str | One of the edge types above |
| `conditional` | bool | `True` if the edge has a `when` clause (never suppresses the edge) |
| `dynamic` | bool | `True` for `include_*` directives (runtime-resolved) |
| `position` | int | Sibling order within the parent (task 0, task 1, ...) |
| `when_expr` | str or None | The raw `when` expression, for display purposes |
| `tags` | list[str] | Tags on the directive, for subgraph filtering |

**Handlers as first-class graph citizens**: Handlers are nodes of type `handler`, not attributes. A task with `notify: restart nginx` creates a `notify` edge to the handler node. Multiple tasks can notify the same handler (many-to-one fan-in). Handlers can notify other handlers (chained edges). Handlers can include taskfiles (same edge types as regular tasks). The `listen` directive creates implicit fan-out — multiple handlers subscribing to a topic each receive an edge when any task notifies that topic.

### Variable provenance

During graph construction, every variable reference (`{{ var_name }}`) encountered in a node's YAML content is classified by walking the node's ancestry in the graph to find where the variable is defined:

| Provenance | Source | How detected |
|------------|--------|--------------|
| `local` | `vars:` on the same task | Variable key in task node |
| `block` | `vars:` on enclosing block | Variable key in ancestor block node |
| `role_default` | `defaults/main.yml` in the role | Variable key in role's defaults vars_file node |
| `role_var` | `vars/main.yml` in the role | Variable key in role's vars vars_file node |
| `play` | `vars:` or `vars_files:` on the play | Variable key in ancestor play node |
| `runtime` | `set_fact` or `register` in another task | `data_flow` edge from producing task |
| `inventory_file` | `group_vars/` or `host_vars/` in the repo | Variable key in inventory vars_file node |
| `external` | Not found anywhere in scanned content | Must be supplied by inventory, extra-vars, or platform |

The `external` classification is significant: it identifies variables the content requires but does not define. These form the content's **external interface** — the contract between the content and its deployment environment.

Variable provenance enables:

- **Auto-generated argument specs**: The set of `role_default` + `external` variables for a role is substantially what `meta/argument_specs.yml` should declare. Type can be inferred from usage context (`when` → bool, `loop` → list, string interpolation → string). This enables a modernization rule that generates or updates argument specs from observed usage.
- **Duplicate and collision detection**: Two roles in the same play defining the same variable name in `defaults/` is a collision — the last role included wins, which is order-dependent and fragile. Variables defined in `defaults/` but never referenced are dead defaults. Variables shadowed across precedence levels (role default overridden by play vars) without explicit intent are potential confusion sources.
- **Data-flow edges**: `set_fact` and `register` create cross-cutting data dependencies that are invisible in the YAML structure. The graph surfaces these as `data_flow` edges, making them visible for complexity analysis and impact assessment.

Variable provenance connects to the existing `PropertyOrigin` concept: `PropertyOrigin` tracks where inherited *properties* (become, ignore_errors) come from; variable provenance tracks where *data* comes from. Both walk the graph ancestry; both use `NodeIdentity` to attribute the source.

### Python file analysis

Modules, plugins, and module_utils are first-class graph nodes. When a task invokes a module, the graph builder creates an `invokes` edge from the task to the module's Python file. When a module imports from `module_utils`, an AST analysis of the module's `import` statements creates `py_imports` edges.

The engine parses Python files using `ast` (stdlib) to extract quality attributes stored as node properties:

| Attribute | Source | What it indicates |
|-----------|--------|-------------------|
| `has_documentation` | `DOCUMENTATION = r'''...'''` present | `ansible-doc` works for this module |
| `has_examples` | `EXAMPLES` string present | Usability |
| `has_return_docs` | `RETURN` string present | Return value documentation |
| `check_mode_honest` | `supports_check_mode=True` AND `module.check_mode` in a conditional branch | Module actually respects check mode |
| `argument_spec_complete` | Every param in `argument_spec` has `type` and `description` | Interface quality |
| `type_hint_coverage` | Ratio of function signatures with type annotations | Code quality |
| `docstring_coverage` | Ratio of functions with docstrings | Maintainability |
| `external_imports` | Imports outside `ansible.*` and stdlib | Hidden Python dependencies |

The analysis is identical for `owned` and `referenced` nodes — `NodeScope` determines how findings are surfaced. For `owned` content, missing documentation or dishonest check mode are violations. For `referenced` content (Galaxy dependencies), the same attributes contribute to a **dependency quality score** that correlates with ADR-040's `ProjectManifest`.

This makes Python file analysis a foundation for dependency health assessment: the `ProjectManifest` (ADR-040) identifies *what* collections a project depends on; the ContentGraph's Python analysis measures *how trustworthy* those dependencies are.

### Temporal progression via stateless engine

The engine is stateless per scan (ADR-020, ADR-029). It builds the `ContentGraph`, attaches a `ScanSnapshot` to each node as a node attribute, and serializes the graph to the Gateway. The engine does not store history — it produces the current state.

```python
@dataclass
class ScanSnapshot:
    scan_id: str
    timestamp: datetime
    violations: list[Violation]
    complexity: int
    state: str  # clean, violated, remediated, regressed
```

The Gateway persists the full timeline: each `NodeIdentity` accumulates a history of `ScanSnapshot` entries across scans. Trend queries ("when did this role go from violated to clean?", "which nodes regressed?") are Gateway concerns, not engine concerns.

**Graph topology stability is a correctness invariant.** The same content must produce an isomorphic graph across scans. If the topology changes between iterations on unchanged content, either the parser is non-deterministic or there is a bug. During remediation convergence, the graph shape should remain stable while node attributes change (violations disappear as fixes land). This invariant is assertable with attribute-aware matching that verifies both topology and structural node/edge types:

```python
from networkx.algorithms.isomorphism import (
    categorical_node_match,
    categorical_edge_match,
)

node_match = categorical_node_match(["node_type", "key"], [None, None])
edge_match = categorical_edge_match(["edge_type"], [None])

assert nx.is_isomorphic(
    graph_pass_n,
    graph_pass_n_plus_1,
    node_match=node_match,
    edge_match=edge_match,
)
```

This model simplifies ADR-044's Phase B. Instead of a separate progression store or graph diffing, the graph *is* the progression store — each node carries its own timeline as a node attribute, and the Gateway is the durable backing store.

### Multiple entry points and connected components

A repository with multiple playbooks may produce one connected graph or several discrete subgraphs depending on whether playbooks share roles or taskfiles. The `ContentGraph` handles this naturally — `weakly_connected_components()` identifies independent clusters.

```
Component 1: site.yml ──→ roles/web, roles/common
             deploy.yml ──→ roles/common, roles/monitoring
             (connected via shared roles/common)

Component 2: ci-lint.yml ──→ tasks/lint.yml
             (no shared content — independent)
```

Each connected component can be analyzed independently. Disconnected components share no nodes, so they have no cross-component impact — fixing a violation in component 1 cannot affect component 2. This also enables parallelism: independent components can be scanned concurrently.

Collections with only roles (no playbooks) use roles as graph roots. The graph builder detects the content type from the directory structure and `galaxy.yml` presence. Role-only collections have shallower graphs but the same node/edge taxonomy applies.

### Enabled capabilities

The ContentGraph unlocks capabilities beyond the core identity and progression model. These are organized as future work — enabled by the graph but not part of the initial implementation scope.

**Complexity metrics**: Cyclomatic complexity can be measured at task, play, playbook, and project level by analyzing the subgraph rooted at each node. Complexity contributors include `when` conditionals (+1 per condition), `loop`/`with_*` (+1), `block/rescue/always` (+1 per exception path), and include/import edges (+1 per target, +N for variable-path includes with N resolvable targets). Fan-in (`in_degree`) measures how many consumers depend on a node. Fan-out (`out_degree`) measures how many dependencies a node has. Depth (longest path from root to leaf) measures nesting. All are computable from the networkx graph with standard algorithms.

**AI escalation enrichment**: When a violation escalates to the AI provider (Tier 2), the graph provides context that the current snippet-only approach lacks. The AI receives the node's position in the graph, inherited variables via `PropertyOrigin`, `when` conditions on edges leading to it, and fan-in count indicating how many consumers are affected by a change. Semantic preservation can be enforced by verifying the post-remediation graph is isomorphic to the pre-remediation graph — if the AI's fix adds or removes an edge, that is a structural change that should be flagged. Remediation tiers can be defined by what graph changes are permitted: style-only (node attributes only), conservative (no edge changes), structural (edges within a role), architectural (cross-role edge changes).

**Topology visualization**: The frontend can render the ContentGraph using `@patternfly/react-topology` (consistent with the existing PatternFly UI stack). Nodes are colored or sized by violation severity or complexity score (heatmap). Edge styles distinguish relationship types (solid for import, dashed for conditional, dotted for notify). Clusters correspond to connected components or plays. Click-through from a graph node navigates to the violation detail view.

**Best-practices patterns**: The graph enables rules that detect structural anti-patterns. A playbook with cyclomatic complexity exceeding a threshold suggests splitting into AAP Controller workflow nodes — each workflow node maps to a simpler playbook, and branching is handled by the workflow engine rather than nested `when` conditions. Conditional branching buried deep in role includes (high depth, many conditional edges) suggests restructuring to use play-level branching (conditions at graph roots, not leaves). These patterns can be captured as modernization rules (e.g., `M-xxx: playbook complexity exceeds threshold, consider AAP workflow`).

**Dependency quality scorecards**: For `referenced` collection nodes, the graph aggregates Python file analysis attributes into a composite quality score: check mode honesty ratio, documentation coverage, argument spec completeness, type hint coverage. This correlates with ADR-040's `ProjectManifest` to answer "how trustworthy are my dependencies?" at the project level.

## Related Decisions

- [ADR-003](ADR-003-vendor-ari-engine.md): ARI integration model — this ADR redefines how ARI's output is consumed
- [ADR-009](ADR-009-remediation-engine.md): Remediation engine — convergence loop gains identity-aware tracking
- [ADR-019](ADR-019-dependency-governance.md): Dependency governance — ADR-019's philosophy (quality assessment of deps) extends to content dependencies via Python file analysis and dependency quality scorecards
- [ADR-023](ADR-023-per-finding-classification.md): Per-finding classification — node identity strengthens per-finding resolution tracking
- [ADR-026](ADR-026-rule-scope-metadata.md): Rule scope metadata — scope becomes a property of identified nodes
- [ADR-036](ADR-036-two-pass-remediation-engine.md): Two-pass remediation — progression model naturally supports multi-pass
- [ADR-040](ADR-040-scan-metadata-enrichment.md): Scan metadata enrichment — `ProjectManifest` identifies dependencies; the ContentGraph provides the quality assessment framework for those dependencies

## References

- [ARI-to-ContentGraph Migration Research](/.sdlc/research/ari-to-contentgraph-migration.md) — layer-by-layer assessment of ARI engine components, code pointers, pseudocode for ContentGraph engine, and phased migration path
- Conversation analysis of snippet accuracy issues (2026-03-27)
- Puzzle piece analogy for entity-with-history design
- ansible-core source analysis: `lib/ansible/playbook/base.py` (FieldAttribute descriptors, `_get_parent_attribute`, `squash`), `block.py` (parent chain walk), `task.py` (task inheritance), `attribute.py` (FieldAttribute vs NonInheritableFieldAttribute), `helpers.py` (static import vs dynamic include resolution), `play_iterator.py` (HostState, dynamic task splicing)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-27 | Bradley A. Thornton | Initial proposal |
| 2026-03-27 | Bradley A. Thornton | Added inherited property attribution, scope-level violations |
| 2026-03-27 | Bradley A. Thornton | Added ansible-core lessons, DAG topology, ownership borders |
| 2026-03-28 | Bradley A. Thornton | Refined graph model: networkx MultiDiGraph, node/edge taxonomy, worst-case include policy, variable provenance, Python file analysis, temporal progression, enabled capabilities (complexity, AI escalation, visualization, best-practices patterns, dependency quality) |
| 2026-03-30 | Bradley A. Thornton | Replaced 5-phase plan (A-E) with direct 3-phase plan (zero throwaway code); eliminated intermediate adapter layer; all ~96 native rules ported to GraphRule in Phase 2; added rule classification taxonomy |
| 2026-03-30 | Bradley A. Thornton | Added comprehensive graph-pattern test fixtures as Phase 1 deliverable; `tests/fixtures/graph-patterns/` covering every edge type in the node/edge taxonomy |
