# ARI-to-ContentGraph Migration: Engine Architecture Research

**Status**: Complete
**Date**: 2026-03-28
**Related**: [ADR-044: Node Identity and Progression Model](/.sdlc/adrs/ADR-044-node-identity-progression-model.md)

## Objective

Map every component of the vendored ARI engine to its ContentGraph replacement.
For each component, document: what it does today, what source files implement it,
whether ContentGraph keeps/ports/replaces it, and pseudocode for the target state.

The goal is to get as close as possible to the end-state architecture without
writing production code — a blueprint that ADR-044 can reference for
implementation planning.

---

## Executive Summary

ARI's value is its **Ansible domain knowledge**: YAML loading, content-type
recognition (playbook vs taskfile vs role), module argument parsing, and
executable-type classification (`import_role` → `ROLE_TYPE`). This knowledge is
encoded in `model_loader.py` and `finder.py` — roughly 2,500 lines of
battle-tested heuristics for understanding Ansible content structure.

ARI's limitation is its **data model**: a flat `ObjectList` tree with no node
identity, squashed variable inheritance, and duplication of shared content (a
role included by 3 playbooks exists 3 times). This model was designed for
single-pass, stateless evaluation — the opposite of what APME's convergence
loop, remediation engine, and progression tracking require.

**ContentGraph keeps the domain knowledge and replaces the data model** with a
DAG-backed, identity-preserving, provenance-tracking graph implemented as a
`networkx.MultiDiGraph`.

The existing 22 module annotators and all validator rules (native + OPA) require
**no algorithmic changes** — they continue to operate on the same data shapes.
The change is in how that data is produced and organized.

---

## Current ARI Pipeline

### Pipeline stages

```
  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐
  │  Load/Parse  │───▶│ Tree Construction │───▶│ Variable Resolution │
  │  parser.py   │    │    tree.py        │    │   context.py        │
  │ model_loader │    │   TreeLoader      │    │ variable_resolver   │
  └─────────────┘    └──────────────────┘    └─────────┬───────────┘
                                                       │
  ┌──────────────────┐    ┌──────────────┐    ┌────────▼──────────┐
  │  OPA Payload     │◀───│  Orchestrate │◀───│ Risk Annotation   │
  │ opa_payload.py   │    │ scanner.py   │    │ annotators/*.py   │
  │                  │    │ scan_state   │    │                   │
  └──────────────────┘    └──────────────┘    └───────────────────┘
```

### Stage-to-file mapping

All paths relative to `src/apme_engine/engine/`.

| Stage | File(s) | Key entry points | Output |
|-------|---------|------------------|--------|
| Load/Parse | `parser.py`, `model_loader.py`, `finder.py` | `Parser.run()` | `definitions: dict[str, list[Object]]` + `Load` metadata |
| Tree Construction | `tree.py` | `TreeLoader.run()` → `_recursive_get_calls()` | `list[ObjectList]` (each an ordered list of `CallObject`) |
| Variable Resolution | `context.py`, `annotators/variable_resolver.py` | `resolve_variables(tree, additional)` | `list[TaskCall]` with resolved `.args` and `.become` |
| Risk Annotation | `annotators/risk_annotator_base.py`, `annotators/ansible_builtin.py`, `annotators/ansible.builtin/*.py` | `RiskAnnotator.run_module_annotators()` | `RiskAnnotation` attached to `TaskCall.annotations` |
| OPA Payload | `opa_payload.py` | `build_hierarchy_payload()` | JSON dict with `hierarchy[].nodes[]` for OPA |
| Orchestration | `scanner.py`, `scan_state.py` | `ARIScanner.evaluate()` → `SingleScan.*` | `ScanContext` with hierarchy + optional scandata |

### Orchestration sequence (`ARIScanner.evaluate`)

```
scanner.py:408   scandata.construct_trees(ram_client)
scanner.py:414   scandata.resolve_variables(ram_client)
scanner.py:420   scandata.annotate()
scanner.py:426   scandata.apply_rules()     # builds hierarchy_payload
```

---

## Layer-by-Layer Assessment

### Layer 1: YAML Loading + Content Recognition

**Files**: `model_loader.py` (~2,500 lines), `finder.py`, `parser.py`

**Verdict**: Port/reference heavily

**What it does**: Reads YAML files from disk, classifies them (playbook,
taskfile, role, collection, module), and produces typed `Object` dataclasses.
This is where all Ansible convention knowledge lives:

- Roles have `tasks/main.yml`, `handlers/main.yml`, `defaults/`, `vars/`, `meta/main.yml`
- `import_role` / `include_role` → `ExecutableType.ROLE_TYPE`
- `import_tasks` / `include_tasks` → `ExecutableType.TASKFILE_TYPE`
- `module_options` can be string (`k=v`) or dict
- `set_fact` creates variables; `register` creates registered variables
- `loop` / `with_*` → `loop_info` dict
- `become` / `become_user` → `BecomeInfo`
- Collections have `galaxy.yml`, `meta/runtime.yml`, `MANIFEST.json`

**Key functions to port/reference**:

| Function | File | Lines | Domain knowledge encoded |
|----------|------|-------|--------------------------|
| `load_playbook()` | model_loader.py | 946–1050 | YAML list → Play objects, `import_playbook` detection |
| `load_play()` | model_loader.py | 517–893 | Play keywords (`hosts`, `tasks`, `pre_tasks`, `roles`), variable extraction, `BecomeInfo` |
| `load_task()` | model_loader.py | 1828–2046 | Module name detection, string module options parsing, executable type classification, loop/register/set_fact extraction |
| `load_role()` | model_loader.py | 1152–1412 | Role directory conventions, metadata, defaults/vars loading, handler discovery |
| `load_collection()` | model_loader.py | 2218–2382 | `galaxy.yml`, `meta/runtime.yml`, MANIFEST.json, module routing |
| `load_repository()` | model_loader.py | 121–268 | Repository root detection, playbook/role/module/inventory discovery |
| `load_taskfile()` | model_loader.py | 2049–2157 | Task list extraction from YAML |
| `could_be_playbook()` | finder.py | — | Heuristic: is this YAML file a playbook? |
| `could_be_taskfile()` | finder.py | — | Heuristic: is this YAML file a task file? |
| `find_module_name()` | finder.py | — | Extract module name from task dict keys |
| `get_task_blocks()` | finder.py | — | Flatten block/rescue/always into task list |

**What changes in ContentGraph**:

Today these functions produce `Object` subclasses (`Playbook`, `Play`, `Role`,
`Task`, etc.) that are later assembled into trees by `TreeLoader`. In
ContentGraph, these functions become the **node factory** — they produce the
same domain data but output it as graph nodes with `NodeIdentity` assigned at
creation time.

```python
# TODAY: model_loader returns Object hierarchy
pb = load_playbook(path="site.yml", basedir="/repo")
# pb.plays = [Play(...), Play(...)]  -- nested children

# CONTENTGRAPH: loader returns flat node list + edge specs
nodes, edges = load_playbook_as_graph_input(path="site.yml", basedir="/repo")
# nodes = [
#   PlaybookNode(identity="site.yml", ...),
#   PlayNode(identity="site.yml::play[0]", ...),
#   PlayNode(identity="site.yml::play[1]", ...),
# ]
# edges = [
#   Edge("site.yml", "site.yml::play[0]", type="contains", position=0),
#   Edge("site.yml", "site.yml::play[1]", type="contains", position=1),
# ]
```

The **domain knowledge** (how to parse a play dict, what `import_role` means,
how to extract `become` from options) is unchanged. Only the output shape
changes.

---

### Layer 2: Object Model

**File**: `models.py` (~5,200 lines)

**Verdict**: Redesign (keep field names and attribute semantics)

**Key dataclasses today**:

| Class | Line | Purpose | Children storage |
|-------|------|---------|------------------|
| `Object` | 324 | Base with `type`, `key` | — |
| `ObjectList` | 337 | Ordered list + key-indexed dict | `items: list[Object \| CallObject]` |
| `CallObject` | 517 | Call wrapper: `spec`, `caller`, `called_from` | No explicit children |
| `Playbook` | 3870 | Playbook file | `plays: list[Play \| str]` |
| `Play` | 3751 | Play block | `pre_tasks`, `tasks`, `post_tasks`, `handlers`, `roles` |
| `Role` | 3595 | Role directory | `taskfiles`, `handlers`, `playbooks`, `modules` |
| `TaskFile` | 3515 | Task file | `tasks: list[Task \| str]` |
| `Task` | 2202 | Single task | No children (leaf) |
| `TaskCall` | 3037 | Task execution with annotations | `annotations: list[Annotation]` |
| `AnsibleRunContext` | 3200 | Rule evaluation context | `sequence: RunTargetList`, `current: RunTarget` |
| `RiskAnnotation` | 4486+ | Structured risk fact | — |

**What to keep**:

- Field names on `Task` (`name`, `module`, `executable`, `executable_type`,
  `module_options`, `options`, `become`, `variables`, `registered_variables`,
  `set_facts`, `loop`, `line_num_in_file`, `defined_in`)
- `RiskAnnotation` and all detail types (`CommandExecDetail`,
  `FileChangeDetail`, `NetworkTransferDetail`, `PackageInstallDetail`,
  `KeyConfigChangeDetail`)
- `BecomeInfo`, `ExecutableType`, `LoadType`
- `Annotation` protocol (key/value/rule_id)

**What to replace**:

| Current | Problem | ContentGraph replacement |
|---------|---------|------------------------|
| `ObjectList` | Flat ordered list; no parent pointers; shared content duplicated | `nx.MultiDiGraph` with nodes keyed by `NodeIdentity` |
| `CallObject.caller` / `.called_from` | Single-parent assumption (tree) | Multiple incoming edges (DAG) |
| `Play.tasks` / `Role.taskfiles` etc. | Children stored as nested lists | Graph edges with `edge_type` and `position` |
| `AnsibleRunContext.sequence` | Linear sequence; no graph structure | Iterator over graph subgraph in topological order |
| Block handling | Flattened into task lists by `get_task_blocks()` | First-class `block` node type with `rescue`/`always` edges |

**ContentGraph node attributes** (stored as `nx` node data):

```python
@dataclass
class ContentNode:
    identity: NodeIdentity          # stable YAML-path ID
    node_type: str                  # playbook, play, role, taskfile, task, handler, block, ...
    scope: NodeScope                # owned | referenced
    state: NodeState                # current content + violations
    progression: list[NodeState]    # history across passes

    # Domain data (ported from Object subclasses):
    name: str
    defined_in: str
    line_num_in_file: tuple[int, int] | None
    options: dict                   # play/task options (become, when, tags, etc.)
    variables: dict                 # vars defined at this scope
    module: str                     # for tasks: module FQCN
    module_options: dict | str      # for tasks: resolved module args
    executable_type: ExecutableType # MODULE_TYPE, ROLE_TYPE, TASKFILE_TYPE
    annotations: list[Annotation]   # risk annotations, spec annotations, etc.
    become: BecomeInfo | None

    # Provenance (new):
    property_origins: dict[str, PropertyOrigin]  # inherited prop → defining node
    variable_provenance: dict[str, VariableProvenance]  # var name → source classification
```

---

### Layer 3: Tree Construction

**File**: `tree.py` (~1,586 lines)

**Verdict**: Replace

**The core problem**: `TreeLoader._recursive_get_calls()` builds an `ObjectList`
by recursively walking definitions and resolving references. When it encounters
an `include_role` or `import_tasks`, it follows the reference and adds the
target's children to the current tree. If the same role is included from
multiple places, it appears multiple times — as separate `RoleCall` entries with
separate child trees.

```python
# tree.py:933 - _recursive_get_calls()
def _recursive_get_calls(self, key, caller, ...):
    obj = self.get_object(key)           # look up definition
    call_obj = call_obj_from_spec(spec=obj, caller=caller)
    obj_list.add(call_obj)               # add to flat list
    children_keys, ... = self._get_children_keys(obj)
    for c_key in children_keys:
        child_objects = self._recursive_get_calls(c_key, call_obj, ...)
        for child_obj in child_objects.items:
            obj_list.add(child_obj)      # children appended to same flat list
```

This produces:
```
ObjectList.items = [
    PlaybookCall(site.yml),
    PlayCall(play[0]),
    RoleCall(web),           # first inclusion
    TaskFileCall(web/tasks/main.yml),
    TaskCall(task[0]),
    TaskCall(task[1]),
    RoleCall(web),           # DUPLICATE - second inclusion from deploy.yml
    TaskFileCall(web/tasks/main.yml),  # DUPLICATE
    TaskCall(task[0]),        # DUPLICATE
    TaskCall(task[1]),        # DUPLICATE
]
```

The `history` list (line 939) prevents infinite loops but not duplication across
separate call chains.

**Resolution functions** (`resolve_module`, `resolve_role`, `resolve_taskfile`,
`resolve_playbook`) are valuable — they encode how Ansible resolves short names,
FQCNs, and path-based references. These are ported into `GraphBuilder`.

**ContentGraph replacement — `GraphBuilder`**:

```python
class GraphBuilder:
    """Builds a ContentGraph (nx.MultiDiGraph) from parsed definitions.

    Unlike TreeLoader, creates each node ONCE and adds edges for each
    include/import. Shared content (roles, taskfiles) appears as a single
    node with multiple incoming edges.
    """

    def __init__(
        self,
        root_definitions: dict[str, ObjectList],
        ext_definitions: dict[str, ObjectList],
        scan_boundary: set[str] | None = None,
    ):
        self.root_defs = root_definitions
        self.ext_defs = ext_definitions
        self.scan_boundary = scan_boundary or set()

        # Resolution helpers (ported from tree.py)
        self.dicts = make_dicts(root_definitions, ext_definitions)
        self.module_redirects = load_module_redirects(
            root_definitions, ext_definitions, self.dicts["modules"]
        )

        # Caches (same pattern as TreeLoader)
        self.module_cache: dict[str, str] = {}
        self.role_cache: dict[str, str] = {}
        self.taskfile_cache: dict[str, str] = {}

    def build(self, load: Load) -> ContentGraph:
        """Build the complete ContentGraph from definitions.

        Returns:
            ContentGraph wrapping an nx.MultiDiGraph.
        """
        graph = ContentGraph()  # wraps nx.MultiDiGraph()

        # Phase 1: Add all definition nodes
        for pb in self._iter_definitions("playbooks"):
            self._add_playbook(graph, pb)

        for role in self._iter_definitions("roles"):
            self._add_role_definition(graph, role)

        for tf in self._iter_definitions("taskfiles"):
            self._add_taskfile(graph, tf)

        # Phase 2: Resolve edges (include/import/dependency)
        self._resolve_all_edges(graph)

        # Phase 3: Classify scope (owned vs referenced)
        self._classify_all_scopes(graph)

        # Phase 4: Validate DAG invariant
        assert nx.is_directed_acyclic_graph(graph.g), \
            "ContentGraph must be acyclic"

        return graph

    def _add_playbook(self, graph: ContentGraph, pb: Playbook) -> str:
        """Add playbook node and its play children.

        Returns:
            NodeIdentity string for the playbook.
        """
        pb_id = NodeIdentity.for_file(pb.defined_in)

        graph.add_node(pb_id, ContentNode(
            identity=pb_id,
            node_type="playbook",
            name=pb.name,
            defined_in=pb.defined_in,
        ))

        for i, play in enumerate(pb.plays):
            if not isinstance(play, Play):
                continue
            play_id = NodeIdentity.for_play(pb.defined_in, i)
            graph.add_node(play_id, ContentNode(
                identity=play_id,
                node_type="play",
                name=play.name,
                defined_in=play.defined_in,
                options=play.options,
                variables=play.variables,
                become=play.become,
            ))
            graph.add_edge(pb_id, play_id,
                edge_type="contains", position=i)

            # Play children: roles, pre_tasks, tasks, post_tasks, handlers
            self._add_play_children(graph, play, play_id)

        return pb_id

    def _add_play_children(
        self, graph: ContentGraph, play: Play, play_id: str
    ):
        """Add role refs, task refs for a play."""
        pos = 0

        # roles: section
        for rip in play.roles:
            if not isinstance(rip, RoleInPlay):
                continue
            resolved_key = resolve_role(
                rip.name, self.dicts["roles"],
                play.collection, play.collections_in_play,
            )
            if resolved_key:
                role_id = self._ensure_role_node(graph, resolved_key)
                graph.add_edge(play_id, role_id,
                    edge_type="dependency", position=pos)
                pos += 1

        # pre_tasks, tasks, post_tasks, handlers
        for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
            for task in getattr(play, section, []):
                if not isinstance(task, Task):
                    continue
                task_id = self._add_task_node(graph, task, play_id, pos)
                pos += 1

    def _add_task_node(
        self, graph: ContentGraph, task: Task, parent_id: str, position: int
    ) -> str:
        """Add a task node and resolve its executable reference.

        Key difference from TreeLoader: if the task references a role or
        taskfile, we create an edge to the EXISTING role/taskfile node
        rather than duplicating its entire subtree.
        """
        task_id = NodeIdentity.for_task(task.defined_in, task.index)

        graph.add_node(task_id, ContentNode(
            identity=task_id,
            node_type="task",
            name=task.name,
            defined_in=task.defined_in,
            line_num_in_file=task.line_num_in_file,
            module=task.module,
            module_options=task.module_options,
            executable_type=task.executable_type,
            options=task.options,
            variables=task.variables,
            become=task.become,
        ))
        graph.add_edge(parent_id, task_id,
            edge_type="contains", position=position)

        # Resolve executable → edge (not subtree copy)
        if task.executable_type == ExecutableType.ROLE_TYPE:
            resolved = resolve_role(
                task.executable, self.dicts["roles"],
                task.collection, task.collections_in_play,
            )
            if resolved:
                role_id = self._ensure_role_node(graph, resolved)
                edge_type = ("import" if "import_role" in task.module
                             else "include")
                graph.add_edge(task_id, role_id,
                    edge_type=edge_type,
                    conditional="when" in task.options,
                    dynamic=(edge_type == "include"),
                )

        elif task.executable_type == ExecutableType.TASKFILE_TYPE:
            resolved = resolve_taskfile(
                task.executable, self.dicts["taskfiles"], task.key,
            )
            if resolved:
                tf_id = self._ensure_taskfile_node(graph, resolved)
                edge_type = ("import" if "import_tasks" in task.module
                             else "include")
                graph.add_edge(task_id, tf_id,
                    edge_type=edge_type,
                    conditional="when" in task.options,
                    dynamic=(edge_type == "include"),
                )

        elif task.executable_type == ExecutableType.MODULE_TYPE:
            resolved = resolve_module(
                task.executable, self.dicts["modules"],
                self.module_redirects,
            )
            if resolved:
                graph.add_edge(task_id, resolved,
                    edge_type="invokes")

        return task_id

    def _ensure_role_node(self, graph: ContentGraph, role_key: str) -> str:
        """Return existing role node ID, or create one from definitions.

        This is the DAG guarantee: a role is added at most once.
        """
        role_id = NodeIdentity.from_key(role_key)
        if graph.has_node(role_id):
            return role_id

        role_obj = self._lookup(role_key, "roles")
        if role_obj and isinstance(role_obj, Role):
            self._add_role_definition(graph, role_obj)
        return role_id

    def _ensure_taskfile_node(
        self, graph: ContentGraph, tf_key: str
    ) -> str:
        """Return existing taskfile node ID, or create one."""
        tf_id = NodeIdentity.from_key(tf_key)
        if graph.has_node(tf_id):
            return tf_id

        tf_obj = self._lookup(tf_key, "taskfiles")
        if tf_obj and isinstance(tf_obj, TaskFile):
            self._add_taskfile(graph, tf_obj)
        return tf_id

    def _classify_all_scopes(self, graph: ContentGraph):
        """Classify each node as owned or referenced.

        owned:      inside the scan boundary, OR a task referencing a
                    module whose FQCN matches the collection namespace
        referenced: resolved for context, violations are advisory-only

        FQCN awareness: reads galaxy.yml to determine the collection's
        namespace.name. Tasks whose resolved module FQCN starts with
        that prefix are classified as OWNED — this marks the *task* as
        belonging to the collection even when the referenced module was
        physically resolved from a venv path. Module/plugin *nodes*
        themselves are classified by their defined_in path (Rule 1).
        """
        own_namespace = self._read_collection_namespace()

        for node_id in graph.g.nodes:
            node = graph.get_node(node_id)
            defined_in = node.defined_in or ""

            # Rule 1: physical path inside scan boundary
            if self._is_in_scan_boundary(defined_in):
                node.scope = NodeScope.OWNED
                continue

            # Rule 2: task references a module in the owned namespace
            if own_namespace and node.node_type == "task":
                fqcn = node.resolved_module_name or node.module
                if fqcn and fqcn.startswith(own_namespace + "."):
                    node.scope = NodeScope.OWNED
                    continue

            node.scope = NodeScope.REFERENCED

    def _read_collection_namespace(self) -> str | None:
        """Read namespace.name from galaxy.yml if present."""
        for name in ("galaxy.yml", "galaxy.yaml"):
            path = Path(self._scan_root) / name
            if path.exists():
                data = yaml.safe_load(path.read_text())
                ns, cname = data.get("namespace", ""), data.get("name", "")
                return f"{ns}.{cname}" if ns and cname else None
        return None
```

---

### Layer 4: Variable Resolution

**Files**: `context.py` (~923 lines), `annotators/variable_resolver.py` (~307 lines)

**Verdict**: Replace algorithm, keep domain knowledge

**How it works today**:

```python
# scan_state.py:773 - resolve()
def resolve(trees, additional):
    for tree in trees:
        taskcalls = resolve_variables(tree, additional)

# variable_resolver.py:228 - resolve_variables()
def resolve_variables(tree, additional):
    context = Context(inventories=inventories)
    for call_obj in tree.items:          # LINEAR walk of flat ObjectList
        context.add(call_obj, depth)     # merges vars into context
        if isinstance(call_obj, TaskCall):
            VariableAnnotator(context).run(call_obj)  # resolves {{ }}
```

`Context.add()` merges variables by type:

| Object type | Variables merged | Precedence tracked |
|-------------|----------------|--------------------|
| `Playbook` | `variables` | `PlaybookGroupVarsAll` |
| `Play` | `variables`, `become`, `module_defaults` | `PlayVars` |
| `Role` | `default_variables`, `variables` | `RoleDefaults`, `RoleVars` |
| `Task` | `variables`, `registered_variables`, `set_facts` | `TaskVars`, `RegisteredVars`, `SetFacts` |

The `var_set_history` dict records which key set each variable (the setter's
`key` string). But it does **not** record the graph node — just a string key.
When a task inherits `become` from a play, `Context.become` is overwritten; the
play's identity as the origin is lost.

**What to keep**: The variable precedence model (`VariableType` enum), the
`resolve_module_options()` function's Jinja2 template resolution logic, the
`extract_variable_names()` regex, and the `resolve_single_variable()` chain.
These encode how Ansible's variable precedence and template resolution work.

**What to replace**: The linear walk and `Context.add()` accumulation pattern.

**ContentGraph replacement — `VariableProvenanceResolver`**:

```python
class VariableProvenanceResolver:
    """Resolve variables by walking the graph ancestry.

    Unlike Context's linear walk, this resolver:
    1. Follows graph edges (not flat list order)
    2. Records PropertyOrigin for every inherited property
    3. Preserves the defining node identity for become, vars, etc.
    """

    def resolve(self, graph: ContentGraph) -> None:
        """Resolve variables for all task nodes in topological order."""
        for node_id in nx.topological_sort(graph.g):
            node = graph.get_node(node_id)
            if node.node_type != "task":
                continue
            self._resolve_task(graph, node_id, node)

    def _resolve_task(
        self, graph: ContentGraph, task_id: str, task_node: ContentNode
    ):
        """Resolve all variables and inherited properties for a task."""
        # Build the ancestry chain by walking parent edges
        ancestors = self._get_ancestor_chain(graph, task_id)

        # Merge variables with provenance
        merged_vars: dict[str, tuple[Any, VariableProvenance]] = {}

        for ancestor_id in ancestors:
            ancestor = graph.get_node(ancestor_id)

            if ancestor.node_type == "role":
                # Role defaults (lowest precedence)
                for var_name, var_val in ancestor.default_variables.items():
                    if var_name not in merged_vars:
                        merged_vars[var_name] = (var_val, VariableProvenance(
                            source="role_default",
                            defining_node=ancestor_id,
                        ))
                # Role vars (higher precedence, overwrites defaults)
                for var_name, var_val in ancestor.role_variables.items():
                    merged_vars[var_name] = (var_val, VariableProvenance(
                        source="role_var",
                        defining_node=ancestor_id,
                    ))

            elif ancestor.node_type == "play":
                for var_name, var_val in ancestor.variables.items():
                    merged_vars[var_name] = (var_val, VariableProvenance(
                        source="play",
                        defining_node=ancestor_id,
                    ))

            elif ancestor.node_type == "block":
                for var_name, var_val in ancestor.variables.items():
                    merged_vars[var_name] = (var_val, VariableProvenance(
                        source="block",
                        defining_node=ancestor_id,
                    ))

        # Task's own vars (highest explicit precedence)
        for var_name, var_val in task_node.variables.items():
            merged_vars[var_name] = (var_val, VariableProvenance(
                source="local",
                defining_node=task_id,
            ))

        # Store provenance on the node
        task_node.variable_provenance = {
            k: prov for k, (_, prov) in merged_vars.items()
        }

        # Resolve Jinja2 templates in module_options
        # (reuses existing resolve_module_options logic)
        resolved_context = self._build_resolution_context(merged_vars)
        task_node.resolved_module_options = resolve_templates(
            task_node.module_options, resolved_context
        )

        # Resolve inherited properties with PropertyOrigin
        self._resolve_inherited_properties(graph, task_id, task_node, ancestors)

    def _resolve_inherited_properties(
        self, graph, task_id, task_node, ancestors
    ):
        """Track PropertyOrigin for become, ignore_errors, etc.

        This is the ContentGraph equivalent of ansible-core's
        _get_parent_attribute() — but it records the origin as data
        rather than computing it on every read.
        """
        inheritable_props = ["become", "become_user", "ignore_errors",
                             "ignore_unreachable", "check_mode",
                             "no_log", "run_once"]

        for prop in inheritable_props:
            # Walk ancestors from nearest to farthest
            for ancestor_id in reversed(ancestors):
                ancestor = graph.get_node(ancestor_id)
                value = ancestor.options.get(prop)
                if value is not None:
                    task_node.property_origins[prop] = PropertyOrigin(
                        property_name=prop,
                        value=value,
                        defining_node=ancestor_id,
                        inherited=(ancestor_id != task_id),
                    )
                    break

    def _get_ancestor_chain(
        self, graph: ContentGraph, node_id: str
    ) -> list[str]:
        """Walk parent edges to build a linear ancestor chain.

        For DAG nodes with multiple parents (shared roles), compute the
        shortest path from an effective root to this node — the most
        specific context chain.  Effective roots are ancestors with no
        predecessors within this node's ancestor set.  Ties are broken
        by lexicographic ordering of node IDs for determinism.
        """
        ancestors = nx.ancestors(graph.g, node_id)
        if not ancestors:
            return []

        ancestor_set = set(ancestors)
        roots = [
            a for a in ancestors
            if all(p not in ancestor_set
                   for p in graph.g.predecessors(a))
        ]

        paths = [
            nx.shortest_path(graph.g, root, node_id)
            for root in roots
        ]
        paths.sort(key=lambda p: (len(p), tuple(p)))
        return paths[0][:-1]  # exclude node itself
```

---

### Annotation System

**Files**: `annotators/ansible.builtin/*.py` (22 modules), `annotators/risk_annotator_base.py`, `annotators/module_annotator_base.py`

**Verdict**: Keep with minimal adapter

The annotation system is **APME-owned code** (not ARI upstream). Each module
annotator extracts structured facts from resolved task arguments:

| Annotator file | Module | Risk type | Detail extracted |
|---------------|--------|-----------|-----------------|
| `shell.py` | `ansible.builtin.shell` | `cmd_exec` | `CommandExecDetail(command=...)` |
| `command.py` | `ansible.builtin.command` | `cmd_exec` | `CommandExecDetail(command=...)` |
| `file.py` | `ansible.builtin.file` | `file_change` | `FileChangeDetail(path, state, mode)` |
| `uri.py` | `ansible.builtin.uri` | `outbound_transfer` | `NetworkTransferDetail(src, dest)` |
| `get_url.py` | `ansible.builtin.get_url` | `inbound_transfer` | `NetworkTransferDetail(src, dest)` |
| `git.py` | `ansible.builtin.git` | `inbound_transfer` | `NetworkTransferDetail(src, dest)` |
| `pip.py` | `ansible.builtin.pip` | `pkg_install` | `PackageInstallDetail(pkg, version)` |
| `apt.py` | `ansible.builtin.apt` | `pkg_install` | `PackageInstallDetail(pkg, version)` |
| `dnf.py` | `ansible.builtin.dnf` | `pkg_install` | `PackageInstallDetail(pkg, version)` |
| `yum.py` | `ansible.builtin.yum` | `pkg_install` | `PackageInstallDetail(pkg, version)` |
| `lineinfile.py` | `ansible.builtin.lineinfile` | `file_change` | `FileChangeDetail(path)` |
| `blockinfile.py` | `ansible.builtin.blockinfile` | `file_change` | `FileChangeDetail(path)` |
| `replace.py` | `ansible.builtin.replace` | `file_change` | `FileChangeDetail(path)` |
| `template.py` | `ansible.builtin.template` | `file_change` | `FileChangeDetail(path)` |
| `assemble.py` | `ansible.builtin.assemble` | `file_change` | `FileChangeDetail(path)` |
| `unarchive.py` | `ansible.builtin.unarchive` | `inbound_transfer` | `NetworkTransferDetail(src)` |
| `script.py` | `ansible.builtin.script` | `cmd_exec` | `CommandExecDetail(command=...)` |
| `raw.py` | `ansible.builtin.raw` | `cmd_exec` | `CommandExecDetail(command=...)` |
| `expect.py` | `ansible.builtin.expect` | `cmd_exec` | `CommandExecDetail(command=...)` |
| `rpm_key.py` | `ansible.builtin.rpm_key` | `key_config_change` | `KeyConfigChangeDetail(key)` |
| `apt_key.py` | `ansible.builtin.apt_key` | `key_config_change` | `KeyConfigChangeDetail(key)` |
| `subversion.py` | `ansible.builtin.subversion` | `inbound_transfer` | `NetworkTransferDetail(src)` |

**How they work today**:

```python
# shell.py — representative example
class ShellAnnotator(ModuleAnnotator):
    fqcn = "ansible.builtin.shell"

    def run(self, task: TaskCall) -> ModuleAnnotatorResult:
        cmd = task.args.get("") or task.args.get("cmd") or task.args.get("argv")
        annotation = RiskAnnotation.init(
            risk_type=DefaultRiskType.CMD_EXEC,
            detail=CommandExecDetail(command=cmd),
        )
        return ModuleAnnotatorResult(annotations=[annotation])
```

The annotator's only dependency is `task.args` — the resolved module arguments.
In ContentGraph, task nodes carry the same `resolved_module_options` after
`VariableProvenanceResolver` runs. The annotator interface is unchanged:

```python
class GraphAnnotator:
    """Runs ModuleAnnotator subclasses directly on ContentGraph task nodes.

    Annotators are ported to read from ContentNode directly (22 files,
    ~20 lines each). No proxy layer — ContentNode carries the same
    attributes annotators need: module_options, resolved_module_options,
    module, resolved_module_name, name, annotations.
    """

    def __init__(self):
        self.risk_annotators = [AnsibleBuiltinRiskAnnotator()]

    def annotate(self, graph: ContentGraph) -> None:
        for node_id in graph.g.nodes:
            node = graph.get_node(node_id)
            if node.node_type != "task":
                continue

            for annotator in self.risk_annotators:
                if annotator.match(node):
                    result = annotator.run(node)
                    if result and result.annotations:
                        node.annotations.extend(result.annotations)
```

---

### Rule Consumption

**Native rules**: `validators/native/rules/` (R101–R117, L031, P001–P004, R401)

Rules consume `AnsibleRunContext` which iterates over `RunTarget` objects.
Each `RunTarget` has `.has_annotation_by_condition()` and
`.get_annotation_by_condition()`.

```python
# R101 — representative pattern
class CommandExecRule(Rule):
    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        task = ctx.current
        ac = AnnotationCondition().risk_type(RiskType.CMD_EXEC).attr("is_mutable_cmd", True)
        verdict = task.has_annotation_by_condition(ac)
        ...
```

**OPA rules**: `validators/opa/bundle/` (L003, L006, L013, L015, L022, R118, etc.)

OPA rules consume the JSON hierarchy payload built by `opa_payload.py`:

```json
{
  "hierarchy": [{
    "nodes": [{
      "type": "taskcall",
      "module": "ansible.builtin.shell",
      "annotations": [{"risk_type": "cmd_exec", "command": "..."}],
      "options": {"when": "...", "become": true}
    }]
  }]
}
```

**Impact on validators**: The `ValidateRequest` gRPC message sent to validators
is **unchanged**. Validators receive `files` (file bytes) and `hierarchy`
(JSON). The ContentGraph is internal to the engine — validators never see it.

**No adapter is built.** All ~96 native rules are ported directly to a unified
`GraphRule` interface in Phase 2. Inherited-property and scope-aware rules
gain new capabilities (PropertyOrigin, graph queries). Task-local rules
undergo a mechanical signature change (`ctx.current` → `graph.get_node(node_id)`)
with no algorithmic change — they are ported for interface consistency, not
because they need graph access. Two rule interfaces is worse than one.

Once all rules are on `GraphRule`, `AnsibleRunContext`, `RunTarget`, and
`ObjectList` are removed entirely. One interface for all rules.

See [Phase 2 — Rules + switchover](#phase-2--rules--switchover-feature-flagged)
for the full rule classification and porting strategy.

In ContentGraph, rules consume the graph directly — no intermediate
`AnsibleRunContext` conversion:

```python
class NativeValidatorServicer:
    """Runs GraphRule instances against ContentGraph nodes."""

    def validate(self, graph: ContentGraph) -> list[Violation]:
        violations = []
        for node_id in graph.g.nodes:
            node = graph.get_node(node_id)
            if node.scope != NodeScope.OWNED:
                continue
            for rule in self._rules_for_node_type(node.node_type):
                result = rule.process(graph, node_id)
                if result:
                    violations.append(result.to_violation(node_id, node))
        return violations
```

---

### OPA Hierarchy Payload

**File**: `opa_payload.py` (~356 lines)

**Verdict**: Keep contract, change input source

The `build_hierarchy_payload()` function serializes `AnsibleRunContext` objects
into JSON dicts. Key serialization functions:

| Function | Purpose |
|----------|---------|
| `node_to_dict(RunTarget)` | Serializes a single node with type, key, file, line, module, annotations |
| `annotation_to_dict(Annotation)` | Flattens `RiskAnnotation` detail fields into a JSON dict |
| `opts_for_opa(opts, keys)` | Extracts whitelisted task options for OPA input |
| `_extract_collection_set(trees_data)` | Derives `namespace.collection` pairs from FQCN modules |

In ContentGraph, this becomes:

```python
def build_hierarchy_from_graph(
    graph: ContentGraph,
    scan_type: str,
    scan_name: str,
    scan_id: str = "",
) -> dict:
    """Build OPA hierarchy payload from ContentGraph.

    Output format is IDENTICAL to current opa_payload.build_hierarchy_payload().
    OPA rules see no difference.
    """
    trees_data = []

    # Each connected component with a root (playbook/role) is a "tree"
    for root_id in graph.get_roots():
        nodes = []
        subgraph = nx.descendants(graph.g, root_id) | {root_id}

        for node_id in nx.topological_sort(graph.g.subgraph(subgraph)):
            node = graph.get_node(node_id)
            # Reuse existing node_to_dict serialization logic
            nodes.append(content_node_to_opa_dict(node))

        trees_data.append({
            "root_key": root_id,
            "root_type": node.node_type,
            "root_path": node.defined_in,
            "nodes": nodes,
        })

    return {
        "scan_id": scan_id or utc_timestamp(),
        "hierarchy": trees_data,
        "collection_set": _extract_collection_set(trees_data),
        "metadata": {"type": scan_type, "name": scan_name},
    }


def content_node_to_opa_dict(node: ContentNode) -> dict:
    """Serialize a ContentNode to the same JSON shape as node_to_dict().

    Maintains backward compatibility with existing OPA rules.
    """
    d = {
        "type": node.node_type + "call",
        "key": str(node.identity),
        "file": node.defined_in,
        "line": list(node.line_num_in_file) if node.line_num_in_file else None,
        "defined_in": node.defined_in,
    }

    if node.node_type == "play":
        d["name"] = node.name or None
        d["options"] = opts_for_opa(node.options, ["become", "become_user"])

    if node.node_type == "task":
        d["module"] = node.resolved_module_name or node.module
        d["original_module"] = node.module
        d["annotations"] = [annotation_to_dict(a) for a in node.annotations]
        d["name"] = node.name or None
        d["options"] = opts_for_opa(node.options, OPA_TASK_OPTION_KEYS)
        d["module_options"] = json_safe(node.module_options)

    return d
```

---

## End-State Pipeline Pseudocode

### Core types

```python
@dataclass(frozen=True)
class NodeIdentity:
    """Stable identifier derived from structural position in original content.

    Format: <file-path>::<yaml-path>
    Examples:
        site.yml::play[0]#task[3]
        roles/web/tasks/main.yml::task[0]
    """
    file_path: str
    yaml_path: str

    @classmethod
    def for_file(cls, path: str) -> NodeIdentity:
        return cls(file_path=path, yaml_path="")

    @classmethod
    def for_play(cls, path: str, index: int) -> NodeIdentity:
        return cls(file_path=path, yaml_path=f"play[{index}]")

    @classmethod
    def for_task(cls, path: str, index: int) -> NodeIdentity:
        return cls(file_path=path, yaml_path=f"task[{index}]")

    @classmethod
    def for_block(cls, path: str, play_idx: int, block_idx: int) -> NodeIdentity:
        return cls(file_path=path, yaml_path=f"play[{play_idx}]#block[{block_idx}]")

    def __str__(self) -> str:
        if self.yaml_path:
            return f"{self.file_path}::{self.yaml_path}"
        return self.file_path


class NodeScope(str, Enum):
    OWNED = "owned"
    REFERENCED = "referenced"


@dataclass
class PropertyOrigin:
    property_name: str
    value: object
    defining_node: NodeIdentity
    inherited: bool


@dataclass
class VariableProvenance:
    source: str  # local, block, role_default, role_var, play, runtime, external
    defining_node: NodeIdentity


@dataclass
class NodeState:
    """Immutable snapshot of a node at a specific pipeline phase."""
    pass_number: int
    phase: str  # "original", "formatted", "scanned", "transformed"
    content_hash: str
    violations: list[str]  # violation IDs
    timestamp: str


@dataclass
class ContentNode:
    """All data for a single node in the ContentGraph."""
    identity: NodeIdentity
    node_type: str
    scope: NodeScope = NodeScope.OWNED
    state: NodeState | None = None
    progression: list[NodeState] = field(default_factory=list)

    # Domain data (ported from Object subclasses)
    name: str = ""
    defined_in: str = ""
    line_num_in_file: tuple[int, int] | None = None
    options: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    module: str = ""
    module_options: dict | str = field(default_factory=dict)
    resolved_module_options: dict | str | None = None
    resolved_module_name: str = ""
    executable_type: ExecutableType = ExecutableType.MODULE_TYPE
    annotations: list = field(default_factory=list)
    become: BecomeInfo | None = None

    # Provenance (new)
    property_origins: dict[str, PropertyOrigin] = field(default_factory=dict)
    variable_provenance: dict[str, VariableProvenance] = field(default_factory=dict)

    # Role-specific
    default_variables: dict = field(default_factory=dict)
    role_variables: dict = field(default_factory=dict)

    # Python file analysis (module, action_plugin, filter_plugin, etc.)
    has_documentation: bool = False
    has_examples: bool = False
    has_return_docs: bool = False
    check_mode_honest: bool = False
    argument_spec_complete: bool = False
    type_hint_coverage: float = 0.0
    docstring_coverage: float = 0.0
    external_imports: list[str] = field(default_factory=list)


class ContentGraph:
    """DAG of Ansible content nodes backed by networkx.MultiDiGraph."""

    def __init__(self):
        self.g = nx.MultiDiGraph()

    def add_node(self, node_id: str, data: ContentNode) -> None:
        self.g.add_node(str(node_id), data=data)

    def add_edge(self, src: str, dst: str, **attrs) -> None:
        self.g.add_edge(str(src), str(dst), **attrs)

    def has_node(self, node_id: str) -> bool:
        return str(node_id) in self.g

    def get_node(self, node_id: str) -> ContentNode:
        return self.g.nodes[str(node_id)]["data"]

    def get_roots(self) -> list[str]:
        """Nodes with in_degree == 0 (playbooks, standalone roles)."""
        return [n for n in self.g.nodes if self.g.in_degree(n) == 0]

    def validate_dag(self) -> bool:
        return nx.is_directed_acyclic_graph(self.g)
```

### Pipeline orchestration

```python
class ContentGraphScanner:
    """End-state pipeline replacing ARIScanner.evaluate()."""

    def evaluate(self, load: Load) -> ScanContext:
        # Phase 0: Parse (reuses model_loader domain knowledge)
        definitions, load = Parser().run(load_data=load)

        # Phase 1: Build graph (replaces TreeLoader)
        builder = GraphBuilder(
            root_definitions=definitions,
            ext_definitions=ext_defs,
            scan_boundary=self._compute_scan_boundary(load),
        )
        graph = builder.build(load)
        # build() internally calls: _add_handler_edges, _add_block_edges,
        # _add_vars_include_edges, _add_invokes_edges, _add_data_flow_edges,
        # _classify_all_scopes (FQCN-aware)

        # Phase 2: Analyze Python files (modules, plugins, module_utils)
        file_bytes = collect_file_bytes(graph)
        py_analyzer = PythonFileAnalyzer()
        py_analyzer.analyze(graph, file_bytes)

        # Phase 3: Resolve variables with provenance (replaces Context walk)
        resolver = VariableProvenanceResolver()
        resolver.resolve(graph)

        # Phase 4: Annotate (reuses existing ModuleAnnotator subclasses)
        annotator = GraphAnnotator()
        annotator.annotate(graph)

        # Phase 5: Build outputs for validators
        # Native rules consume the graph directly via GraphRule (no adapter)
        # OPA rules consume hierarchy JSON (backward-compatible)
        hierarchy = build_hierarchy_from_graph(graph, load.target_type, load.target_name)

        return ScanContext(
            graph=graph,
            hierarchy_payload=hierarchy,
            files=file_bytes,
        )
```

### Extended graph construction

The `GraphBuilder.build()` pipeline shown in Layer 3 handles `contains`,
`import`, `include`, and `dependency` edges. The following methods extend
it with handler, block, data-flow, vars-include, and Python invocation edges.

#### Handler and block edges

```python
class GraphBuilder:
    # ... extends the GraphBuilder from Layer 3

    def _add_handler_edges(self, graph: ContentGraph, play_id: str, play: Play):
        """Create handler nodes and notify/listen edges.

        Handlers are first-class nodes (type=handler), not task attributes.
        notify: directed edge from notifying task → handler node.
        listen: implicit fan-out — multiple handlers subscribe to a topic.
        """
        handler_topics: dict[str, list[str]] = {}  # topic → [handler_node_ids]

        for i, handler in enumerate(play.handlers):
            handler_id = NodeIdentity.for_handler(handler.defined_in, i)
            graph.add_node(handler_id, ContentNode(
                identity=handler_id,
                node_type="handler",
                name=handler.name,
                defined_in=handler.defined_in,
                line_num_in_file=handler.line_num_in_file,
                module=handler.module,
                module_options=handler.module_options,
            ))
            graph.add_edge(play_id, handler_id,
                edge_type="contains", position=i)

            for topic in handler.listen_topics:
                handler_topics.setdefault(topic, []).append(str(handler_id))

        # Wire notify edges from tasks to handlers
        for task_id in self._task_nodes_in_play(graph, play_id):
            task_node = graph.get_node(task_id)
            for handler_name in task_node.options.get("notify", []):
                handler_id = self._resolve_handler(graph, play_id, handler_name)
                if handler_id:
                    graph.add_edge(task_id, handler_id, edge_type="notify")

                for subscriber_id in handler_topics.get(handler_name, []):
                    graph.add_edge(task_id, subscriber_id, edge_type="notify")

    def _add_block_edges(
        self, graph: ContentGraph, block_id: str, block: dict
    ):
        """Create rescue and always edges from a block node.

        Main block tasks use `contains` edges (already built by _add_task_node).
        Rescue tasks get `rescue` edges, always tasks get `always` edges.
        """
        for i, task in enumerate(block.get("rescue", [])):
            rescue_id = self._add_task_node(graph, task, block_id, i)
            graph.add_edge(block_id, rescue_id, edge_type="rescue", position=i)

        for i, task in enumerate(block.get("always", [])):
            always_id = self._add_task_node(graph, task, block_id, i)
            graph.add_edge(block_id, always_id, edge_type="always", position=i)
```

#### Data-flow edges

```python
    def _add_data_flow_edges(self, graph: ContentGraph):
        """Create data_flow edges from set_fact/register to consumers.

        Two passes: (1) collect all variable producers, (2) find consumers
        that reference those variables in when/loop/Jinja2 expressions.
        """
        producers: dict[str, str] = {}  # var_name → producing_node_id

        for node_id in nx.topological_sort(graph.g):
            node = graph.get_node(node_id)
            if node.node_type != "task":
                continue
            if reg := node.options.get("register"):
                producers[reg] = node_id
            if node.module in ("ansible.builtin.set_fact", "set_fact"):
                for var_name in (node.module_options or {}).keys():
                    producers[var_name] = node_id

        for node_id in graph.g.nodes:
            node = graph.get_node(node_id)
            if node.node_type != "task":
                continue
            referenced_vars = extract_jinja2_vars(node.options, node.module_options)
            for var_name in referenced_vars:
                if var_name in producers and producers[var_name] != node_id:
                    graph.add_edge(
                        producers[var_name], node_id,
                        edge_type="data_flow",
                        variable=var_name,
                    )
```

#### Vars-include and invocation edges

```python
    def _add_vars_include_edges(
        self, graph: ContentGraph, play_id: str, play: Play
    ):
        """Create vars_include edges from plays to vars files.

        Handles vars_files: on plays and include_vars tasks.
        """
        for vars_file_path in play.options.get("vars_files", []):
            vars_id = NodeIdentity.for_file(vars_file_path)
            if not graph.has_node(str(vars_id)):
                graph.add_node(vars_id, ContentNode(
                    identity=vars_id,
                    node_type="vars_file",
                    defined_in=vars_file_path,
                ))
            graph.add_edge(play_id, str(vars_id), edge_type="vars_include")

    def _add_invokes_edges(self, graph: ContentGraph):
        """Create invokes edges from tasks to Python module/plugin files.

        For each task, resolve its FQCN module to a physical Python file.
        If the module exists as a node (owned or referenced), create an
        invokes edge. Also creates the module/plugin node if it doesn't
        exist yet.
        """
        for node_id in list(graph.g.nodes):
            node = graph.get_node(node_id)
            if node.node_type != "task" or not node.resolved_module_name:
                continue
            module_path = self._resolve_module_path(node.resolved_module_name)
            if module_path:
                module_node_id = NodeIdentity.for_file(module_path)
                if not graph.has_node(str(module_node_id)):
                    graph.add_node(module_node_id, ContentNode(
                        identity=module_node_id,
                        node_type=self._classify_python_type(module_path),
                        defined_in=module_path,
                        scope=NodeScope.REFERENCED,
                    ))
                graph.add_edge(node_id, str(module_node_id), edge_type="invokes")

    def _classify_python_type(self, path: str) -> str:
        """Determine node type from Python file path convention."""
        if "plugins/modules/" in path or "library/" in path:
            return "module"
        if "plugins/action/" in path:
            return "action_plugin"
        if "plugins/filter/" in path:
            return "filter_plugin"
        if "plugins/lookup/" in path:
            return "lookup_plugin"
        if "module_utils/" in path:
            return "module_utils"
        return "module"  # default for ambiguous paths
```

### Python file analysis pipeline

Python files in the graph (modules, plugins, module_utils) are analyzed using
`ast` (stdlib) to extract quality attributes. This runs after graph construction
and before annotation, as a separate pipeline phase.

```python
import ast
from pathlib import Path

PYTHON_NODE_TYPES = {
    "module", "action_plugin", "filter_plugin",
    "lookup_plugin", "module_utils",
}

STDLIB_TOP_LEVEL = frozenset({...})  # populated from sys.stdlib_module_names


class PythonFileAnalyzer:
    """Extract quality attributes from Python files in the ContentGraph."""

    def analyze(self, graph: ContentGraph, file_bytes: dict[str, bytes]):
        for node_id in list(graph.g.nodes):
            node = graph.get_node(node_id)
            if node.node_type not in PYTHON_NODE_TYPES:
                continue
            source = file_bytes.get(node.defined_in)
            if not source:
                continue
            try:
                tree = ast.parse(source, filename=node.defined_in)
            except SyntaxError:
                continue
            self._extract_documentation(node, source)
            self._extract_check_mode(node, tree)
            self._extract_argument_spec(node, tree)
            self._extract_code_quality(node, tree)
            self._extract_imports(node, tree)
            self._add_py_imports_edges(graph, node, tree)

    def _extract_documentation(self, node: ContentNode, source: bytes):
        text = source.decode("utf-8", errors="replace")
        node.has_documentation = "DOCUMENTATION" in text
        node.has_examples = "EXAMPLES" in text
        node.has_return_docs = "RETURN" in text

    def _extract_check_mode(self, node: ContentNode, tree: ast.Module):
        has_supports, has_branch = False, False
        for n in ast.walk(tree):
            if isinstance(n, ast.keyword) and n.arg == "supports_check_mode":
                has_supports = True
            if isinstance(n, ast.Attribute) and n.attr == "check_mode":
                if self._is_in_conditional(n, tree):
                    has_branch = True
        node.check_mode_honest = has_supports and has_branch

    def _extract_argument_spec(self, node: ContentNode, tree: ast.Module):
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign)
                    and any(self._name_matches(t, "argument_spec")
                            for t in n.targets)):
                if isinstance(n.value, ast.Dict):
                    node.argument_spec_complete = self._all_params_typed(n.value)
                break

    def _extract_code_quality(self, node: ContentNode, tree: ast.Module):
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        if not functions:
            return
        typed = sum(
            1 for f in functions
            if f.returns is not None
            or any(a.annotation is not None for a in f.args.args)
        )
        node.type_hint_coverage = typed / len(functions)
        documented = sum(1 for f in functions if ast.get_docstring(f))
        node.docstring_coverage = documented / len(functions)

    def _extract_imports(self, node: ContentNode, tree: ast.Module):
        external = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for alias in n.names:
                    top = alias.name.split(".")[0]
                    if top not in STDLIB_TOP_LEVEL and top != "ansible":
                        external.append(alias.name)
            elif isinstance(n, ast.ImportFrom) and n.module:
                top = n.module.split(".")[0]
                if top not in STDLIB_TOP_LEVEL and top != "ansible":
                    external.append(n.module)
        node.external_imports = external

    def _add_py_imports_edges(
        self, graph: ContentGraph, node: ContentNode, tree: ast.Module
    ):
        """Create py_imports edges to module_utils this file imports."""
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                if "module_utils" in n.module:
                    utils_path = self._resolve_module_utils_path(n.module)
                    if utils_path:
                        utils_id = NodeIdentity.for_file(utils_path)
                        if not graph.has_node(str(utils_id)):
                            graph.add_node(utils_id, ContentNode(
                                identity=utils_id,
                                node_type="module_utils",
                                defined_in=utils_path,
                            ))
                        graph.add_edge(
                            str(node.identity), str(utils_id),
                            edge_type="py_imports",
                        )
```

---

## Phase D Enabler Architecture

Phase D capabilities depend on the ContentGraph being in place (Phases A–C).
Each enabler is independently implementable. This section provides the
algorithmic pseudocode that ADR-044's "Enabled capabilities" references.

### Complexity metrics

```python
@dataclass
class ComplexityReport:
    node_id: str
    cyclomatic: int
    fan_in: int
    fan_out: int
    depth: int
    conditional_count: int
    loop_count: int


def compute_complexity(graph: ContentGraph, node_id: str) -> ComplexityReport:
    """Compute complexity metrics for a subgraph rooted at node_id.

    Cyclomatic complexity contributors:
    - +1 base
    - +1 per when conditional on any descendant
    - +1 per loop/with_* on any descendant task
    - +1 per rescue/always exception path
    - +1 per include/import edge
    - +1 bonus for dynamic includes (runtime resolution uncertainty)
    """
    subgraph_nodes = nx.descendants(graph.g, node_id) | {node_id}
    subgraph = graph.g.subgraph(subgraph_nodes)

    cyclomatic = 1
    conditional_count = 0
    loop_count = 0

    for nid in subgraph.nodes:
        node = graph.get_node(nid)
        opts = node.options

        if opts.get("when"):
            when_val = opts["when"]
            cyclomatic += len(when_val) if isinstance(when_val, list) else 1
            conditional_count += 1

        if any(k.startswith("loop") or k.startswith("with_") for k in opts):
            cyclomatic += 1
            loop_count += 1

    for u, v, data in subgraph.edges(data=True):
        if data.get("edge_type") in ("rescue", "always"):
            cyclomatic += 1
        if data.get("edge_type") in ("include", "import"):
            cyclomatic += 1
            if data.get("dynamic"):
                cyclomatic += 1

    fan_in = graph.g.in_degree(node_id)
    fan_out = graph.g.out_degree(node_id)

    try:
        depth = nx.dag_longest_path_length(subgraph)
    except nx.NetworkXUnfeasible:
        depth = 0

    return ComplexityReport(
        node_id=node_id,
        cyclomatic=cyclomatic,
        fan_in=fan_in,
        fan_out=fan_out,
        depth=depth,
        conditional_count=conditional_count,
        loop_count=loop_count,
    )


def project_complexity_summary(graph: ContentGraph) -> dict:
    """Aggregate complexity across all roots (playbooks/roles)."""
    return {root: compute_complexity(graph, root) for root in graph.get_roots()}
```

### AI escalation enrichment

AI is not a separate phase — it is another transform source in the unified
convergence loop. When no Tier 1 (deterministic) transform exists for a
violation, the convergence loop escalates to AI. AI proposals go through
the same `TransformSession` → `submit()` → `ChangeSet` → `ApprovalGroup`
path as Tier 1 transforms. If AI fixes introduce new Tier 1-fixable
violations, the loop continues with Tier 1 before trying AI again.

This eliminates the separate `_escalate_tier2` / `_escalate_by_units`
code path in the current engine. The only difference between Tier 1 and
AI transforms is metadata (confidence, cost, auto-approvability) — not
the convergence machinery.

When a violation escalates to AI, the graph provides structured context
beyond the raw YAML snippet.

```python
@dataclass
class AIEscalationContext:
    """Graph-derived context sent to the AI provider alongside the snippet."""
    node_id: str
    node_type: str
    ancestors: list[dict]  # [{node_id, node_type, name}, ...]
    inherited_properties: dict[str, PropertyOrigin]
    incoming_edge_types: list[str]
    fan_in: int
    fan_out: int
    variable_provenance: dict[str, VariableProvenance]
    sibling_count: int
    complexity: ComplexityReport


def build_ai_context(graph: ContentGraph, node_id: str) -> AIEscalationContext:
    """Extract graph context for AI-assisted remediation."""
    node = graph.get_node(node_id)

    ancestors = []
    for pred in nx.ancestors(graph.g, node_id):
        pred_node = graph.get_node(pred)
        ancestors.append({
            "node_id": pred,
            "node_type": pred_node.node_type,
            "name": pred_node.name,
        })

    incoming = [
        data.get("edge_type", "unknown")
        for _, _, data in graph.g.in_edges(node_id, data=True)
    ]

    parents = list(graph.g.predecessors(node_id))
    sibling_count = graph.g.out_degree(parents[0]) - 1 if parents else 0

    return AIEscalationContext(
        node_id=node_id,
        node_type=node.node_type,
        ancestors=ancestors,
        inherited_properties=node.property_origins,
        incoming_edge_types=incoming,
        fan_in=graph.g.in_degree(node_id),
        fan_out=graph.g.out_degree(node_id),
        variable_provenance=node.variable_provenance,
        sibling_count=sibling_count,
        complexity=compute_complexity(graph, node_id),
    )


def verify_remediation_safety(
    graph_before: ContentGraph, graph_after: ContentGraph,
) -> list[str]:
    """Compare pre- and post-remediation graphs for structural safety.

    Engine-side structural check (topology, not semantics — per
    invariant 13). Runs during TransformSession merge for any transform
    source (Tier 1 or AI). Attribute changes (violations cleared) are
    expected; edge changes indicate structural modifications requiring
    human review.
    """
    from networkx.algorithms.isomorphism import (
        categorical_node_match,
        categorical_edge_match,
    )

    node_match = categorical_node_match(["node_type"], [None])
    edge_match = categorical_edge_match(["edge_type"], [None])

    if nx.is_isomorphic(
        graph_before.g, graph_after.g,
        node_match=node_match, edge_match=edge_match,
    ):
        return []

    warnings = []
    added_nodes = set(graph_after.g.nodes) - set(graph_before.g.nodes)
    removed_nodes = set(graph_before.g.nodes) - set(graph_after.g.nodes)
    added_edges = set(graph_after.g.edges) - set(graph_before.g.edges)
    removed_edges = set(graph_before.g.edges) - set(graph_after.g.edges)

    if added_nodes:
        warnings.append(f"AI fix added {len(added_nodes)} nodes: {added_nodes}")
    if removed_nodes:
        warnings.append(f"AI fix removed {len(removed_nodes)} nodes")
    if added_edges:
        warnings.append(f"AI fix added {len(added_edges)} edges")
    if removed_edges:
        warnings.append(f"AI fix removed {len(removed_edges)} edges")
    return warnings
```

### Topology visualization serialization

The engine serializes the ContentGraph to a format the frontend can render
with `@patternfly/react-topology`.

```python
@dataclass
class GraphVisualization:
    nodes: list[dict]
    edges: list[dict]
    components: list[list[str]]


def serialize_for_visualization(
    graph: ContentGraph,
    complexity_reports: dict[str, ComplexityReport] | None = None,
) -> GraphVisualization:
    """Serialize ContentGraph for frontend topology rendering.

    Node attributes: id, label, type, scope, severity, complexity.
    Edge attributes: source, target, type, conditional, dynamic.
    """
    nodes = []
    for node_id in graph.g.nodes:
        node = graph.get_node(node_id)
        severity = _worst_severity(node.annotations) if node.annotations else None
        cx = complexity_reports.get(node_id) if complexity_reports else None
        nodes.append({
            "id": node_id,
            "label": node.name or Path(node.defined_in).name,
            "type": node.node_type,
            "scope": node.scope.value,
            "file": node.defined_in,
            "severity": severity,
            "complexity": cx.cyclomatic if cx else None,
            "fan_in": cx.fan_in if cx else None,
            "fan_out": cx.fan_out if cx else None,
            "violation_count": len([
                a for a in node.annotations if hasattr(a, "severity")
            ]),
        })

    edges = []
    for u, v, data in graph.g.edges(data=True):
        edges.append({
            "source": u,
            "target": v,
            "type": data.get("edge_type", "contains"),
            "conditional": data.get("conditional", False),
            "dynamic": data.get("dynamic", False),
            "label": data.get("variable", data.get("edge_type", "")),
        })

    components = [list(c) for c in nx.weakly_connected_components(graph.g)]

    return GraphVisualization(nodes=nodes, edges=edges, components=components)
```

### Best-practices pattern rules

Graph-based rules detect structural anti-patterns by analyzing topology.
These are examples of the M/R-class rules the ContentGraph enables.

```python
COMPLEXITY_THRESHOLD = 20
DEPTH_THRESHOLD = 8
FAN_OUT_THRESHOLD = 15


def rule_M201_complexity_threshold(graph: ContentGraph) -> list[Violation]:
    """M201: Playbook complexity exceeds threshold.

    Suggests splitting into AAP Controller workflow nodes.
    """
    violations = []
    for root_id in graph.get_roots():
        cx = compute_complexity(graph, root_id)
        root_node = graph.get_node(root_id)
        if cx.cyclomatic > COMPLEXITY_THRESHOLD:
            violations.append(Violation(
                rule_id="M201",
                node_id=root_id,
                file=root_node.defined_in,
                message=(
                    f"Cyclomatic complexity {cx.cyclomatic} exceeds threshold "
                    f"{COMPLEXITY_THRESHOLD}. Consider splitting into AAP "
                    f"Controller workflow nodes."
                ),
                severity="medium",
            ))
    return violations


def rule_M202_deep_conditional_nesting(graph: ContentGraph) -> list[Violation]:
    """M202: Conditional branching buried deep in role includes.

    Suggests restructuring to use play-level branching.
    """
    violations = []
    for node_id in graph.g.nodes:
        node = graph.get_node(node_id)
        if node.node_type != "task" or not node.options.get("when"):
            continue

        min_depth = float("inf")
        for root in graph.get_roots():
            try:
                depth = nx.shortest_path_length(graph.g, root, node_id)
                min_depth = min(min_depth, depth)
            except nx.NetworkXNoPath:
                continue

        if min_depth > DEPTH_THRESHOLD:
            violations.append(Violation(
                rule_id="M202",
                node_id=node_id,
                file=node.defined_in,
                message=(
                    f"Conditional at depth {min_depth} (threshold "
                    f"{DEPTH_THRESHOLD}). Consider moving branching logic "
                    f"to play level or AAP workflow."
                ),
                severity="low",
            ))
    return violations


def rule_R501_high_fan_out(graph: ContentGraph) -> list[Violation]:
    """R501: Node has excessive outgoing dependencies."""
    violations = []
    for node_id in graph.g.nodes:
        fan_out = graph.g.out_degree(node_id)
        if fan_out > FAN_OUT_THRESHOLD:
            node = graph.get_node(node_id)
            violations.append(Violation(
                rule_id="R501",
                node_id=node_id,
                file=node.defined_in,
                message=(
                    f"Fan-out of {fan_out} exceeds threshold "
                    f"{FAN_OUT_THRESHOLD}. This node has too many "
                    f"dependencies, making it fragile to changes."
                ),
                severity="medium",
            ))
    return violations


def rule_R502_dead_handler(graph: ContentGraph) -> list[Violation]:
    """R502: Handler is never notified by any task."""
    violations = []
    for node_id in graph.g.nodes:
        node = graph.get_node(node_id)
        if node.node_type != "handler":
            continue
        notify_edges = [
            (u, v, d) for u, v, d in graph.g.in_edges(node_id, data=True)
            if d.get("edge_type") == "notify"
        ]
        if not notify_edges:
            violations.append(Violation(
                rule_id="R502",
                node_id=node_id,
                file=node.defined_in,
                message=f"Handler '{node.name}' is never notified.",
                severity="low",
            ))
    return violations
```

### Dependency quality scorecards

For `REFERENCED` nodes, aggregate Python file analysis attributes into
composite quality scores aligned with ADR-040's `ProjectManifest`.

```python
@dataclass
class DependencyScorecard:
    collection: str
    module_count: int
    documentation_ratio: float
    check_mode_ratio: float
    arg_spec_ratio: float
    type_hint_avg: float
    external_dep_count: int
    overall_score: float  # weighted composite [0.0, 1.0]


def compute_dependency_scorecards(
    graph: ContentGraph,
) -> list[DependencyScorecard]:
    """Aggregate quality metrics for each referenced collection."""
    collections: dict[str, list[ContentNode]] = {}

    for node_id in graph.g.nodes:
        node = graph.get_node(node_id)
        if node.scope != NodeScope.REFERENCED:
            continue
        if node.node_type not in PYTHON_NODE_TYPES:
            continue
        collection = _extract_collection(node.defined_in)
        collections.setdefault(collection, []).append(node)

    scorecards = []
    for collection, modules in collections.items():
        n = len(modules)
        if n == 0:
            continue

        doc_ratio = sum(1 for m in modules if m.has_documentation) / n
        check_ratio = sum(1 for m in modules if m.check_mode_honest) / n
        arg_ratio = sum(1 for m in modules if m.argument_spec_complete) / n
        hint_avg = sum(m.type_hint_coverage for m in modules) / n
        ext_deps = sum(len(m.external_imports) for m in modules)

        overall = (
            doc_ratio * 0.20
            + check_ratio * 0.30
            + arg_ratio * 0.25
            + hint_avg * 0.10
            + max(0, 1.0 - ext_deps * 0.01) * 0.15
        )

        scorecards.append(DependencyScorecard(
            collection=collection,
            module_count=n,
            documentation_ratio=doc_ratio,
            check_mode_ratio=check_ratio,
            arg_spec_ratio=arg_ratio,
            type_hint_avg=hint_avg,
            external_dep_count=ext_deps,
            overall_score=overall,
        ))

    return sorted(scorecards, key=lambda s: s.overall_score)
```

### Graph topology stability assertions

The same content must produce an isomorphic graph across scans. This is
assertable during remediation convergence and in CI.

```python
class TopologyDriftError(Exception):
    pass


def assert_topology_stable(
    graph_a: ContentGraph,
    graph_b: ContentGraph,
    label: str = "topology check",
) -> None:
    """Assert two graphs have identical topology.

    Attribute differences (violations, annotations) are expected —
    only node_type, defined_in, and edge_type are compared.
    """
    from networkx.algorithms.isomorphism import (
        categorical_node_match,
        categorical_edge_match,
    )

    node_match = categorical_node_match(
        ["node_type", "defined_in"], [None, None],
    )
    edge_match = categorical_edge_match(["edge_type"], [None])

    if not nx.is_isomorphic(
        graph_a.g, graph_b.g,
        node_match=node_match,
        edge_match=edge_match,
    ):
        diff = _describe_topology_diff(graph_a, graph_b)
        raise TopologyDriftError(
            f"Graph topology changed between passes ({label}): {diff}"
        )


def _describe_topology_diff(a: ContentGraph, b: ContentGraph) -> str:
    added = set(b.g.nodes) - set(a.g.nodes)
    removed = set(a.g.nodes) - set(b.g.nodes)
    e_added = set(b.g.edges) - set(a.g.edges)
    e_removed = set(a.g.edges) - set(b.g.edges)
    parts = []
    if added:
        parts.append(f"+{len(added)} nodes")
    if removed:
        parts.append(f"-{len(removed)} nodes")
    if e_added:
        parts.append(f"+{len(e_added)} edges")
    if e_removed:
        parts.append(f"-{len(e_removed)} edges")
    return ", ".join(parts) or "unknown structural change"
```

### Formatter → NodeState recording

When the formatter modifies a file, the pipeline records a `NodeState` snapshot
capturing the post-format content for every affected node.

```python
def record_format_state(
    graph: ContentGraph,
    formatted_files: dict[str, str],
    original_files: dict[str, str],
    pass_number: int,
) -> None:
    """Record NodeState for nodes in files that were formatted.

    Called after the formatter runs, before the scan phase.
    """
    changed_files = {
        path for path, content in formatted_files.items()
        if original_files.get(path) != content
    }

    for node_id in graph.g.nodes:
        node = graph.get_node(node_id)
        if node.defined_in not in changed_files:
            continue

        content = _extract_node_content(
            formatted_files[node.defined_in], node.line_num_in_file,
        )
        state = NodeState(
            pass_number=pass_number,
            phase="formatted",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            violations=[],
            timestamp=utc_timestamp(),
        )
        node.progression.append(state)
        node.state = state
```

### Remediation convergence loop integration

The ContentGraph replaces per-file tracking in the convergence loop with
per-node tracking. Transforms operate through `TransformSession` (ephemeral
copy, tracked changes). The engine merges changesets and detects inheritance
propagation. See AGENTS.md invariant 13: transforms are semantically trusted;
the engine owns state and syntax.

**Three-way contract:**

| Concern | Owner | Responsibility |
|---------|-------|----------------|
| "What's wrong" | Validators | Read-only detection (invariant 1) |
| "How to fix it" | Transforms | Domain knowledge, heuristics (invariant 13) |
| "Orchestrate + verify structure" | Engine | State, syntax, propagation, convergence |

New rules and transforms are added without engine changes.

```python
class GraphAwareRemediationEngine:
    """Unified convergence loop for Tier 1 and AI transforms.

    Key differences from current RemediationEngine:
    - Single convergence loop (no separate AI escalation phase)
    - Convergence is per-node, not per-file
    - Oscillation detection uses NodeIdentity
    - All transforms (Tier 1 + AI) operate via TransformSession
    - Approval groups built from submitted changesets + inherited propagation

    Ordering: Tier 1 (deterministic, cheap) runs first each pass.
    AI (non-deterministic, expensive) runs only for violations with no
    Tier 1 transform. If AI fixes introduce new Tier 1 violations,
    the next pass handles them with Tier 1 before trying AI again.
    """

    def __init__(
        self,
        scan_fn: Callable[[list[Path]], ScanContext],
        transform_registry: TransformRegistry,
        ai_provider: AIProvider | None = None,
        max_passes: int = 5,
        max_ai_attempts: int = 2,
    ):
        self._scan_fn = scan_fn
        self._registry = transform_registry
        self._ai = ai_provider
        self._max_passes = max_passes
        self._max_ai_attempts = max_ai_attempts

    async def converge(self, paths: list[Path]) -> ConvergenceResult:
        previous_violations: dict[str, set[str]] = {}
        all_graphs: list[ContentGraph] = []
        approval_groups: list[ApprovalGroup] = []
        ai_attempts: dict[str, int] = {}  # node_id → attempt count

        for pass_num in range(1, self._max_passes + 1):
            scan_ctx = await self._scan_fn(paths)
            graph = scan_ctx.graph
            all_graphs.append(graph)

            if len(all_graphs) > 1:
                assert_topology_stable(
                    all_graphs[-2], all_graphs[-1],
                    label=f"pass {pass_num - 1} → {pass_num}",
                )

            current: dict[str, set[str]] = {}
            for nid in graph.g.nodes:
                node = graph.get_node(nid)
                rule_ids = {
                    a.rule_id for a in node.annotations
                    if hasattr(a, "rule_id")
                }
                if rule_ids:
                    current[nid] = rule_ids

            if not current:
                return ConvergenceResult(
                    converged=True, passes=pass_num, graph=graph,
                    approval_groups=approval_groups,
                )

            oscillating = {
                nid for nid, rules in current.items()
                if rules & previous_violations.get(nid, set())
            }

            # Phase 1: Tier 1 deterministic transforms
            tier1_applied = False
            for nid, rules in current.items():
                if nid in oscillating:
                    continue
                for rule_id in rules:
                    transform_fn = self._registry.get(rule_id)
                    if not transform_fn:
                        continue
                    session = TransformSession(graph, self._structured_files)
                    changeset = transform_fn(session, nid)
                    if changeset and changeset.direct_changes:
                        group = self._merge_and_group(graph, changeset, pass_num)
                        approval_groups.append(group)
                        tier1_applied = True

            # Phase 2: AI for remaining violations (only if Tier 1 had nothing)
            if not tier1_applied and self._ai:
                for nid, rules in current.items():
                    if nid in oscillating:
                        continue
                    if ai_attempts.get(nid, 0) >= self._max_ai_attempts:
                        continue
                    for rule_id in rules:
                        if self._registry.get(rule_id):
                            continue  # Tier 1 exists, skip AI
                        session = TransformSession(graph, self._structured_files)
                        changeset = await ai_as_transform(
                            session, nid, self._ai,
                        )
                        if changeset and changeset.direct_changes:
                            group = self._merge_and_group(
                                graph, changeset, pass_num,
                            )
                            group.source = "ai"
                            approval_groups.append(group)
                            ai_attempts[nid] = ai_attempts.get(nid, 0) + 1
                            tier1_applied = True  # triggers re-scan

            if not tier1_applied:
                return ConvergenceResult(
                    converged=False, passes=pass_num,
                    remaining=current, graph=graph,
                    approval_groups=approval_groups,
                )

            previous_violations = current

        return ConvergenceResult(
            converged=False, passes=self._max_passes,
            remaining=current, graph=graph,
            approval_groups=approval_groups,
        )

    def _merge_and_group(
        self, graph: ContentGraph, changeset: ChangeSet, pass_num: int,
    ) -> ApprovalGroup:
        self._merge_changeset(changeset)
        group = compute_approval_group(
            graph, self._rebuild_graph(), changeset,
        )
        group.pass_number = pass_num
        return group
```

#### Progression model during convergence

Progression is tracked **in the graph** (via `NodeState` snapshots on each
`ContentNode`), not in the files. Files on disk are the mutable working copy;
the graph records immutable snapshots at each pipeline phase.

Each convergence pass builds on the **previous pass's output**, not the original
source. The sequence is:

1. Scan current files → build graph → record `NodeState` (pass 1, phase "scanned")
2. Apply transforms → files on disk change → record `NodeState` (pass 1, phase "transformed")
3. Re-scan modified files → build new graph → record `NodeState` (pass 2, phase "scanned")
4. Repeat until converged, oscillating, or max passes exhausted

The original file content is preserved only as the `content_hash` in the pass-1
`NodeState` snapshot. The Gateway persists the full `NodeState` progression
across scans, enabling reconstruction of any node's state at any point in its
history — the "puzzle" can be assembled at any pass.

#### Per-unit approval model (future, not day-one)

The convergence loop runs to completion without approval gates. The full
result is presented to the user, who can then approve or reject changes.
This section documents the approval architecture so the convergence model
and data structures support it when needed.

**Approval granularity:**

- **Within a pass**: transforms are independently approvable per node.
  All transforms in a pass were computed from the same scan state, so
  rejecting node A's pass-1 change does not affect node B's pass-1 change.
  Today all 20 transforms are single-unit (one task via `sf.find_task()`).
- **Across passes for the same node**: cascade. Rejecting node X's pass-N
  change also rejects pass N+1, N+2, etc. for that node, because each
  pass built on the previous. The user gets node X at its pass N-1 state.
- **Across passes for different nodes**: independent for current single-unit
  transforms. Rejecting node A's pass-1 does not invalidate node B's pass-2.

**Dirty-node grouping:**

After a transform completes, the engine runs `diff_nodes` (see below)
to identify every node whose effective state changed — both directly
modified nodes and descendants affected via inheritance. **Every dirty
node forms one approval group.** The group is atomic: approve or reject
it as a unit.

For a parent-level transform (e.g., removing `become` from a play), the
dirty set includes:

- The play itself (direct change)
- Tasks the transform explicitly modified (compensating `become: true`)
- Tasks the transform deliberately skipped (inherited `become` removed,
  no compensation — heuristic decision by the transform author)

All of these are dirty. Rejecting the play change rejects the entire
group — compensated tasks and uncompensated tasks alike — plus any
subsequent convergence passes that built on this state.

**Transform session model:**

Transforms operate through a constrained API on an ephemeral copy of
the graph and files — a transaction model analogous to a git branch.
Changes are tracked by the API (not detected after the fact), and
`submit()` produces an explicit changeset.

```python
class TransformSession:
    """Ephemeral workspace for a transform. Changes are tracked, not detected."""

    def __init__(self, graph: ContentGraph, files: dict[str, StructuredFile]):
        self._graph = graph.copy()
        self._files = {k: v.copy() for k, v in files.items()}
        self._submitted: list[NodeChange] = []

    def get_node(self, node_id: str) -> ContentNode:
        return self._graph.get_node(node_id)

    def get_file(self, path: str) -> StructuredFile:
        return self._files[path]

    def modify_node(self, node_id: str, file_path: str, fn: Callable) -> None:
        """Apply a modification function to a node's YAML. Tracked."""
        sf = self._files[file_path]
        fn(sf)
        self._submitted.append(NodeChange(node_id=node_id, file_path=file_path))

    def descendants(self, node_id: str, node_type: str = None) -> list[str]:
        """Query descendants of a node, optionally filtered by type."""
        descs = nx.descendants(self._graph.g, node_id)
        if node_type:
            return [d for d in descs
                    if self._graph.get_node(d).node_type == node_type]
        return list(descs)

    def submit(self) -> ChangeSet:
        """Return the explicit changeset."""
        return ChangeSet(
            direct_changes=self._submitted,
            files={p: sf.serialize() for p, sf in self._files.items() if sf.dirty},
        )
```

**Everything in one `submit()` is one approval group.** The engine does
not analyze which task changes were "caused by" the play change. The
transform author submitted them together — they are linked. A transform
that submits out-of-scope changes is a poorly written transform, not an
engine problem.

Example transform:

```python
def fix_play_become(session: TransformSession, play_id: str):
    play = session.get_node(play_id)

    session.modify_node(play_id, play.defined_in,
        lambda sf: remove_key(sf, play, "become"))

    for child_id in session.descendants(play_id, node_type="task"):
        child = session.get_node(child_id)
        if child.module in SKIP_BECOME_MODULES:
            continue
        session.modify_node(child_id, child.defined_in,
            lambda sf: add_key(sf, child, "become", True))

    return session.submit()
```

**Domain knowledge lives in the transform.** The transform author writes
the heuristic for which children need compensation. Rules detect
problems, not solutions. The engine orchestrates but has no module-level
knowledge. Annotation coverage will never be comprehensive across all
collections — we will scan content using modules we've never seen.

Transforms use heuristics because comprehensive module knowledge is
impossible. For the `become` example:

- Remove `become` from the play
- Add explicit `become: true` to children that likely need privilege
- Skip known no-ops: `set_fact`, `debug`, `assert`, `meta`, `fail`
- Skip info-gathering patterns: `state: gathered`, modules ending in
  `_info` or `_facts`
- Default to adding `become` for unknown modules (conservative)

**Engine-side propagation detection:**

The engine's only post-transform job is identifying descendants the
transform did NOT submit but whose inherited properties changed because
of what it DID submit. These are added to the approval group
automatically.

```python
def compute_approval_group(
    graph_before: ContentGraph,
    graph_after: ContentGraph,
    changeset: ChangeSet,
) -> ApprovalGroup:
    """Build the approval group: submitted nodes + inherited impact.

    The submitted nodes are already known (tracked by TransformSession).
    The engine walks descendants of submitted nodes to find inherited
    property changes that the transform didn't explicitly handle.
    Propagation depth is determined by the graph structure.
    """
    submitted_ids = {c.node_id for c in changeset.direct_changes}
    inherited_impact: set[str] = set()

    for node_id in submitted_ids:
        for desc_id in nx.descendants(graph_after.g, node_id):
            if desc_id in submitted_ids:
                continue  # already in the changeset
            before = graph_before.get_node(desc_id)
            after = graph_after.get_node(desc_id)
            for prop, origin in after.property_origins.items():
                if not origin.inherited:
                    continue
                before_origin = before.property_origins.get(prop)
                if before_origin is None or before_origin.value != origin.value:
                    inherited_impact.add(desc_id)
                    break

    return ApprovalGroup(
        submitted=submitted_ids,
        inherited=inherited_impact,
        all_dirty=submitted_ids | inherited_impact,
    )
```

Examples:

- Transform changes a play name: changeset has one node (the play).
  No descendants have inherited property changes. Approval group = just
  the play.
- Transform removes `become` from a play + adds `become` to 3 tasks:
  changeset has 4 nodes (play + 3 tasks). Engine finds 2 more tasks
  whose inherited `become` changed (the ones the transform skipped).
  Approval group = 6 nodes. Reject any = reject all + cascade.

The approval UI shows the full group. The user sees what was explicitly
changed (submitted) and what was implicitly affected (inherited). A
transform that submits unrelated changes bundles them into the group —
that is a transform quality issue, not an engine concern.

**UI presentation:**

```
Pass 1:  [✓] task[0]  — FQCN: shell → ansible.builtin.shell
         [✓] task[1]  — FQCN: copy → ansible.builtin.copy
         [✓] task[2]  — add changed_when: false

Pass 2:  [✓] task[0]  — add changed_when: false
         [ ] (approval group — all-or-nothing)
             play[0]  — remove play-level become              [SUBMITTED]
             task[0]  — add explicit become: true              [SUBMITTED]
             task[1]  — lost inherited become (set_fact)       [INHERITED]
             task[2]  — lost inherited become (debug)          [INHERITED]
             task[3]  — add explicit become: true              [SUBMITTED]
```

SUBMITTED = transform explicitly called `modify_node()` for this node.
INHERITED = engine detected inherited property change during merge.
Both are part of the same approval group.

Rejecting the group rolls ALL nodes back to their pass-1 state.
Independent changes in pass 2 (task[0]'s `changed_when`) are unaffected
— that transform produced its own single-node approval group.

Rejecting the group also cascades to pass 3+ for all nodes in it.

---

## Migration Path

The migration uses three phases with zero throwaway code. No intermediate
adapter layer (`graph_to_run_context()`, `TaskCallProxy`, `node_to_spec_object()`)
is built — the graph and rule porting happen together so every artifact is
permanent.

### Phase 1 — ContentGraph core + validation (parallel, feature-flagged)

**Goal**: Build all permanent infrastructure and validate it against the
existing pipeline. The old pipeline remains primary — nothing switches over.

**Changes**:

1. Add comprehensive graph-pattern test fixtures in
   `tests/fixtures/graph-patterns/` covering every edge type the
   `GraphBuilder` must handle: handlers/notify, block rescue/always,
   import_playbook, import_tasks, import_role, static `roles:` list,
   vars_files, host_vars, nested blocks, and a minimal collection layout
   with `galaxy.yml`. These fixtures are the validation corpus for all
   subsequent implementation.
2. Add `node_id` field to `Violation` proto in `common.proto`
   (backward-compatible)
3. Add `networkx` dependency (pure Python, ~3 MB, Apache-2.0)
4. Implement `ContentGraph`, `ContentNode`, `NodeIdentity`, `GraphBuilder`
   in new module `engine/content_graph.py`
5. Implement `VariableProvenanceResolver`, `PropertyOrigin`,
   `VariableProvenance` in new module `engine/variable_provenance.py`
6. Implement `build_hierarchy_from_graph()`, `content_node_to_opa_dict()`
   in new module `engine/graph_opa_payload.py`
7. Implement `GraphRule` base class in new module
   `validators/native/rules/graph_rule_base.py`
8. Implement `PythonFileAnalyzer` in new module `engine/python_analyzer.py`
9. In `SingleScan.construct_trees()`, after existing `TreeLoader.run()`:
   ```python
   if os.environ.get("APME_USE_CONTENT_GRAPH"):
       self.content_graph = GraphBuilder(root_defs, ext_defs).build(load)
       self._validate_graph_tree_equivalence(self.trees, self.content_graph)
   ```

**What is NOT built**: No `node_id` on `Object`, `CallObject`, `TaskCall`,
or `RunTarget`. No `graph_to_run_context()`. No `TaskCallProxy`. No
`node_to_spec_object()`. None of these are needed because we never adapt
the graph back to the old model — rules are ported directly in Phase 2.

**Validation** (runs in parallel behind feature flag):
- `GraphBuilder` output compared to `TreeLoader` output for structural
  equivalence (graph has fewer nodes due to deduplication — expected)
- `build_hierarchy_from_graph()` output compared to existing
  `build_hierarchy_payload()` — OPA JSON must be identical
- `VariableProvenanceResolver` output compared to existing `Context.add()`
  variable resolution — resolved values must match

This validation is test infrastructure, not throwaway — it becomes permanent
integration tests that verify the graph builder works correctly.

**Risk**: Low — existing pipeline unchanged; graph construction is a parallel
code path behind a feature flag.

### Phase 2 — Rules + switchover (feature-flagged)

**Goal**: Port all ~96 native rules to `GraphRule`, adapt annotators to read
from `ContentNode` directly, and switch `ContentGraph` as the primary model.
Every line of code is permanent.

**Rule porting** (priority order):

#### INHERITED_PROPERTY rules (~10) — algorithmic change, high value

These rules detect properties that can be inherited from parent scopes
(play → block → task). Today they fire on every inheriting child. With
`PropertyOrigin`, they fire once on the defining scope.

| Rule | File | Property | Porting impact |
|------|------|----------|----------------|
| R108 | `R108_privilege_escalation.py` | `become` | 50 violations → 1 on the play |
| L047 | `L047_no_log_password.py` | `no_log` | Fire at defining scope |
| L045 | `L045_inline_env_var.py` | `environment` | Fire at defining scope |
| L033 | `L033_unconditional_override.py` | `when` | Effective `when` from block/play |
| M030 | `M030_broken_conditional_expressions.py` | `when` | Effective `when` from ancestry |
| L049 | `L049_loop_var_prefix.py` | `loop_control` | Scoped above the task |
| M010 | `M010_python2_interpreter.py` | play vars | `ansible_python_interpreter` from play |
| M022 | `M022_tree___oneline_callback_plugins.py` | `environment` | Inheritable from play |
| M026 | `M026_invalid_inventory_variable_names.py` | `vars` | `vars` inherited from block/play |

**Porting pattern** for inherited-property rules:

```python
# BEFORE (AnsibleRunContext): fires on every task with become=true
class PrivilegeEscalationRule(Rule):
    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        task = ctx.current
        if task.become.enabled:
            return RuleResult(...)

# AFTER (ContentGraph): fires once on the defining node
class PrivilegeEscalationRule(GraphRule):
    def process(self, graph: ContentGraph, node_id: str) -> RuleResult | None:
        node = graph.get_node(node_id)
        origin = node.property_origins.get("become")
        if origin and not origin.inherited:
            affected = len([
                d for d in nx.descendants(graph.g, node_id)
                if graph.get_node(d).node_type == "task"
            ])
            return RuleResult(
                message=f"Play enables privilege escalation "
                        f"(affects {affected} tasks)",
            )
        return None
```

#### SCOPE_AWARE rules (~8) — algorithmic change, medium value

These rules reason about ordering, siblings, or cross-scope data where
graph structure improves accuracy.

| Rule | File | Graph benefit |
|------|------|---------------|
| L042 | `L042_complexity.py` | Subgraph node count replaces `ctx.sequence` counting |
| L097 | `L097_name_unique.py` | Graph siblings vs flat sequence ordering |
| L086 | `L086_play_vars_usage.py` | Play node type + vars in graph |
| L032 | `L032_changed_data_dependence.py` | `data_flow` edges for variable precedence |
| L034 | `L034_unused_override.py` | Variable precedence via graph ancestry |
| L093 | `L093_set_fact_override.py` | Role defaults/vars via graph edges |
| M005 | `M005_data_tagging.py` | `data_flow` edges for register → consume |
| R117 | `R117_external_role.py` | Role position via graph edges |

#### TASK_LOCAL rules (~78) — mechanical signature change

These rules examine only the current task's own attributes. They gain no
new capability from graph access but are ported for interface consistency.

```python
# BEFORE (AnsibleRunContext)
class CommandExecRule(Rule):
    def process(self, ctx: AnsibleRunContext) -> RuleResult | None:
        task = ctx.current
        ac = AnnotationCondition().risk_type(RiskType.CMD_EXEC).attr("is_mutable_cmd", True)
        verdict = task.has_annotation_by_condition(ac)
        ...

# AFTER (GraphRule) — same logic, different entry point
class CommandExecRule(GraphRule):
    def process(self, graph: ContentGraph, node_id: str) -> RuleResult | None:
        node = graph.get_node(node_id)
        # same annotation check on node.annotations
        ...
```

Examples: R101–R115 (risk annotation rules), L031 (file permissions),
L046 (free-form), L040 (tabs), L060 (line length), P001–P004 (policy),
L073 (indentation), all collection/role metadata rules (L052–L055,
L087–L090, L096, L103–L105).

**Switchover**:

1. `ContentGraphScanner` replaces `ARIScanner.evaluate()` (feature-flagged)
2. Annotators adapted to read from `ContentNode` directly (22 files,
   ~20 lines each — no `TaskCallProxy` needed)
3. All rules consume `ContentGraph` directly via `GraphRule`
4. OPA payload produced by `build_hierarchy_from_graph()`
5. `StructuredFile` (ruamel.yaml) becomes the serialization layer for
   `NodeState`, not a parallel model

**Removal** (once flag is stable):

1. `TreeLoader`, `_recursive_get_calls()` from `engine/tree.py`
2. `Context.add()` linear walk from `engine/context.py`
3. `AnsibleRunContext`, `RunTarget`, `ObjectList` from `engine/models.py`
4. Old `build_hierarchy_payload()` from `engine/opa_payload.py`
5. Old `resolve_variables()` from `engine/annotators/variable_resolver.py`

**Risk**: Largest phase — graph switchover + rule porting. Mitigated by:
- Feature flag (`APME_USE_CONTENT_GRAPH=1`) during transition
- Rules are independent — each can be ported and tested individually
- TASK_LOCAL porting is mechanical (search-and-replace)
- OPA hierarchy JSON shape is preserved (validated in Phase 1)
- Old pipeline runs in parallel until validation passes

**Validation**:
- INHERITED_PROPERTY/SCOPE_AWARE: test against `terrible-playbook/`
  fixture. Same issues detected, fewer violations with better attribution.
- TASK_LOCAL: exact violation parity — graph output must match old output
  identically. Any divergence is a porting bug.
- OPA rules: hierarchy JSON diff between old and new must be empty.
- Full test suite passes with feature flag enabled.
- Final gate: full test suite passes with old code removed.

### Phase 3 — Progression + Provenance

**Goal**: Unlock the capabilities that motivated ContentGraph. `PropertyOrigin`
is consumed by the already-ported rules from Phase 2.

**Changes**:

1. `NodeState` recorded at each pipeline phase (format, scan, transform)
   — see [Formatter → NodeState recording](#formatter--nodestate-recording)
2. `variable_provenance` enables:
   - Auto-generated argument specs (M-rule)
   - Duplicate/collision detection across roles
   - External interface identification (`external` provenance)
3. Gateway accumulates `ScanSnapshot` per node across scans
   (temporal progression per ADR-044)
4. Snippets trivially extracted from `NodeState.content` at any pass
5. Complexity metrics — see [Complexity metrics](#complexity-metrics)
6. AI escalation enrichment — see [AI escalation enrichment](#ai-escalation-enrichment)
7. Topology visualization — see [Topology visualization serialization](#topology-visualization-serialization)
8. Best-practices pattern rules (M201, M202, R501, R502) —
   see [Best-practices pattern rules](#best-practices-pattern-rules)
9. Dependency quality scorecards —
   see [Dependency quality scorecards](#dependency-quality-scorecards)
10. Graph topology stability assertions —
    see [Graph topology stability assertions](#graph-topology-stability-assertions)
11. Remediation convergence loop integration —
    see [Remediation convergence loop integration](#remediation-convergence-loop-integration)

**Risk**: Medium — new features, not behavioral changes. Each capability
can be enabled incrementally. Full pseudocode for all enablers is in
the [Phase D Enabler Architecture](#phase-d-enabler-architecture) section.

---

## Risk Analysis

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| `model_loader.py` output format change breaks downstream | High | Medium | Graph builder consumes existing `Object` output; no loader changes needed in Phase 1 |
| OPA payload shape change breaks OPA rules | High | Low | `content_node_to_opa_dict()` produces identical JSON; validated by diffing output in Phase 1 |
| Graph deduplication changes violation counts | Medium | High | Expected and desired — shared roles produce 1 violation instead of N duplicates. Document in changelog. |
| `networkx` dependency adds weight | Low | Certain | ~3 MB pure Python, no compiled extensions, passes ADR-019 checklist |
| Variable resolution produces different results | Medium | Medium | Shadow-run in Phase 1, compare every resolved value, fix discrepancies before Phase 2 |
| Performance regression (graph construction overhead) | Medium | Low | `networkx` graph construction is O(V+E); current `TreeLoader` is also O(V+E) but with higher constant factor due to duplication |
| Python AST analysis adds scan time | Low | Medium | Only scans `owned` Python files by default; `referenced` analysis opt-in. `ast.parse` is fast for typical module sizes |
| Complexity metrics produce unexpected thresholds | Low | Medium | Thresholds (M201: 20, R501: 15) are configurable; start conservative and tune based on real-world playbook data |
| AI context extraction overhead per escalation | Low | Low | `build_ai_context` is O(ancestors) per node; graph is already in memory. Negligible vs AI inference cost |
| Graph-based rules create noisy findings | Medium | Medium | New rules (M201, M202, R501, R502) ship disabled by default; enable via policy config. Tune thresholds per-organization |
| Per-unit approval rejection cascades across passes | Medium | Medium | By design: within a pass, changes are independently approvable; across passes for the same node, rejection cascades. Inheritance-aware grouping detects cross-node impact via `PropertyOrigin`. See [Per-unit approval model](#per-unit-approval-model-future-not-day-one) |
| Native rule porting changes violation counts | Medium | Certain | INHERITED_PROPERTY/SCOPE_AWARE rules produce fewer, more accurate violations (expected, documented per rule). TASK_LOCAL rules produce identical output (exact parity verified). |
| All ~96 rules ported simultaneously in Phase 2 | Medium | Certain | Rules are independent — each ported and tested individually. TASK_LOCAL porting is mechanical. Old pipeline runs in parallel behind feature flag until validation passes. |
| `AnsibleRunContext` removal breaks external consumers | Low | Low | `AnsibleRunContext` is internal to the native validator. No external API exposure. Removal is a cleanup, not a breaking change. |

---

## File Change Impact Summary

| File | Phase | Change type | Description |
|------|-------|-------------|-------------|
| `tests/fixtures/graph-patterns/` | 1 | New | Comprehensive fixtures for every ContentGraph edge type |
| `proto/apme/v1/common.proto` | 1 | Additive | `node_id` field on `Violation` message |
| `engine/content_graph.py` | 1 | New | `ContentGraph`, `ContentNode`, `GraphBuilder`, `NodeIdentity` |
| `engine/variable_provenance.py` | 1 | New | `VariableProvenanceResolver`, `PropertyOrigin`, `VariableProvenance` |
| `engine/graph_opa_payload.py` | 1 | New | `build_hierarchy_from_graph()`, `content_node_to_opa_dict()` |
| `engine/python_analyzer.py` | 1 | New | `PythonFileAnalyzer` — AST-based quality extraction, `py_imports` edge construction |
| `validators/native/rules/graph_rule_base.py` | 1 | New | `GraphRule` base class — `process(graph, node_id)` interface |
| `engine/graph_scanner.py` | 2 | New | `ContentGraphScanner` (replaces `ARIScanner.evaluate`) |
| `engine/scanner.py` | 2 | Modified | Delegates to `ContentGraphScanner` when flag enabled |
| `engine/scan_state.py` | 2 | Modified | `SingleScan` uses graph when flag enabled |
| `engine/annotators/risk_annotator_base.py` | 2 | Modified | Annotators read from `ContentNode` directly |
| `engine/annotators/ansible.builtin/*.py` | 2 | Modified | 22 module annotators adapted to `ContentNode` (minimal change) |
| `engine/annotators/variable_resolver.py` | 2 | Replaced | `resolve_variables()` replaced by `VariableProvenanceResolver` |
| `validators/native/rules/R108_privilege_escalation.py` | 2 | Ported | Fires on defining scope via `PropertyOrigin` |
| `validators/native/rules/L047_no_log_password.py` | 2 | Ported | `no_log` attributed to defining scope |
| `validators/native/rules/L045_inline_env_var.py` | 2 | Ported | `environment` attributed to defining scope |
| `validators/native/rules/L033_unconditional_override.py` | 2 | Ported | Effective `when` from ancestry |
| `validators/native/rules/M030_broken_conditional_expressions.py` | 2 | Ported | Effective `when` from ancestry |
| `validators/native/rules/L049_loop_var_prefix.py` | 2 | Ported | `loop_control` scoped to defining node |
| `validators/native/rules/M010_python2_interpreter.py` | 2 | Ported | Play vars via graph ancestry |
| `validators/native/rules/M022_tree___oneline_callback_plugins.py` | 2 | Ported | `environment` via graph ancestry |
| `validators/native/rules/M026_invalid_inventory_variable_names.py` | 2 | Ported | `vars` inheritance via graph |
| `validators/native/rules/L042_complexity.py` | 2 | Ported | Subgraph metrics replace `ctx.sequence` |
| `validators/native/rules/L097_name_unique.py` | 2 | Ported | Graph siblings replace flat sequence |
| `validators/native/rules/L086_play_vars_usage.py` | 2 | Ported | Play node type in graph |
| `validators/native/rules/L032_changed_data_dependence.py` | 2 | Ported | `data_flow` edges for variable chains |
| `validators/native/rules/L034_unused_override.py` | 2 | Ported | Variable precedence via graph ancestry |
| `validators/native/rules/L093_set_fact_override.py` | 2 | Ported | Role defaults/vars via graph edges |
| `validators/native/rules/M005_data_tagging.py` | 2 | Ported | `data_flow` edges replace `ctx.previous_tasks` |
| `validators/native/rules/R117_external_role.py` | 2 | Ported | Role position via graph edges |
| `validators/native/rules/*.py` (TASK_LOCAL, ~78) | 2 | Ported (mechanical) | `ctx.current` → `graph.get_node(node_id)`. No algorithmic change. |
| `engine/tree.py` | 2 | Removed | `TreeLoader` removed after validation |
| `engine/context.py` | 2 | Removed | `Context.add()` linear walk removed |
| `engine/opa_payload.py` | 2 | Removed | Replaced by `engine/graph_opa_payload.py` |
| `engine/models.py` (`AnsibleRunContext`, `RunTarget`, `ObjectList`) | 2 | Removed | Replaced by `GraphRule` + `ContentGraph` |
| `validators/opa/bundle/*.rego` | — | Unchanged | OPA rules consume same hierarchy JSON |
| `engine/complexity.py` | 3 | New | `compute_complexity()`, `ComplexityReport`, `project_complexity_summary()` |
| `engine/ai_context.py` | 3 | New | `build_ai_context()`, `AIEscalationContext` |
| `engine/topology_stability.py` | 3 | New | `assert_topology_stable()`, `verify_remediation_safety()`, `TopologyDriftError` |
| `engine/graph_visualization.py` | 3 | New | `serialize_for_visualization()`, `GraphVisualization` |
| `engine/graph_rules.py` | 3 | New | Graph-based rules: M201, M202, R501, R502 |
| `engine/dependency_scorecard.py` | 3 | New | `compute_dependency_scorecards()`, `DependencyScorecard` |
| `remediation/graph_engine.py` | 3 | New | `GraphAwareRemediationEngine` — per-node convergence with TransformSession |
| `remediation/transform_session.py` | 3 | New | `TransformSession` — ephemeral copy, tracked changes, `submit()` → `ChangeSet` |
| `remediation/approval.py` | 3 | New | `compute_approval_group()`, `ApprovalGroup` — submitted + inherited propagation |

---

## References

- [ADR-044: Node Identity and Progression Model](/.sdlc/adrs/ADR-044-node-identity-progression-model.md)
- [ADR-003: Vendored ARI Engine](/.sdlc/adrs/ADR-003-vendor-ari-engine.md)
- [ADR-009: Remediation Engine](/.sdlc/adrs/ADR-009-remediation-engine.md)
- [ADR-019: Dependency Governance](/.sdlc/adrs/ADR-019-dependency-governance.md)
- [ADR-040: Scan Metadata Enrichment](/.sdlc/adrs/ADR-040-scan-metadata-enrichment.md) — `ProjectManifest` dependency identification; ContentGraph provides quality assessment
- [ADR-036: Two-Pass Remediation Engine](/.sdlc/adrs/ADR-036-two-pass-remediation-engine.md) — convergence loop gains graph-aware tracking
- [AGENTS.md invariant 13](/AGENTS.md) — transforms are semantically trusted; engine owns state and syntax
- ansible-core source: `lib/ansible/playbook/base.py` (`_get_parent_attribute`, `FieldAttribute`)
- [ansible-core deprecation mining research](/.sdlc/research/ansible-core-deprecation-mining.md)
