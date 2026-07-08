"""GraphBuilder — constructs a ContentGraph from parsed project definitions (ADR-059).

Extracted from content_graph.py.  This module lives in engine/ because
it depends on ARI model types (Task, Play, Role, etc.).  The
ContentGraph data structure lives in apme_engine.graph.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, cast

from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
    _as_str_list,
    _detect_indent,
)
from apme_engine.graph.types import YAMLDict

if TYPE_CHECKING:
    from .models import (
        Collection,
        Module,
        ObjectList,
        Play,
        Playbook,
        Role,
        RoleInPlay,
        Task,
        TaskFile,
    )

_JINJA_VAR = re.compile(r"\{\{.*?\}\}")


def _has_template(value: str) -> bool:
    """Return True if the string contains Jinja2 template syntax.

    Args:
        value: String to inspect (e.g. task file path or argument).

    Returns:
        ``True`` if ``value`` contains ``{{`` (likely templating).
    """
    return "{{" in value


class GraphBuilder:
    """Constructs a ``ContentGraph`` from parsed project definitions.

    Consumes ``root_definitions`` and ``ext_definitions`` dicts produced
    by the project parser.  After ``.build()`` completes, ``resolve_failures``
    is populated with resolution bookkeeping.  ``extra_requirements`` is
    reserved for future use and currently remains empty.
    """

    def __init__(
        self,
        root_definitions: dict[str, object],
        ext_definitions: dict[str, object],
        *,
        scan_root: str = "",
    ) -> None:
        """Create a builder for graph construction from project definition maps.

        Args:
            root_definitions: Primary project definitions from the project parser.
            ext_definitions: External/referenced definitions merged after roots.
            scan_root: Optional filesystem root for path normalization (reserved).
        """
        self._root_defs = root_definitions
        self._ext_defs = ext_definitions
        self._scan_root = scan_root
        self._graph = ContentGraph()
        self._visited: set[str] = set()
        self._object_by_key: dict[str, object] = {}

        self.extra_requirements: list[dict[str, object]] = []
        self.resolve_failures: dict[str, dict[str, int]] = {
            "module": {},
            "role": {},
            "taskfile": {},
        }

    def build(self) -> ContentGraph:
        """Build and return the ContentGraph.

        Builds a key-to-object lookup from all loaded definitions, then processes playbooks, roles, and
        taskfiles.  String keys in child lists (``Playbook.plays``,
        ``Play.tasks``, ``TaskFile.tasks``, etc.) are resolved through this
        lookup.

        Returns:
            Fully wired ``ContentGraph`` instance.
        """
        from .models import ObjectList, Role, TaskFile

        root_loaded = _load_all_definitions(self._root_defs)
        ext_loaded = _load_all_definitions(self._ext_defs)

        # Build flat key → object lookup for string-key resolution.
        types = ["collections", "roles", "taskfiles", "modules", "playbooks", "plays", "tasks"]
        for type_key in types:
            for loaded in (root_loaded, ext_loaded):
                obj_list = loaded.get(type_key, ObjectList())
                if isinstance(obj_list, ObjectList):
                    for item in obj_list.items:
                        if hasattr(item, "key") and item.key:
                            self._object_by_key[item.key] = item

        # Register handler taskfiles (handlers are stored on Role objects
        # but excluded from the flat definitions dict by the project loader).
        roles_list = root_loaded.get("roles", ObjectList())
        if isinstance(roles_list, ObjectList):
            for obj in roles_list.items:
                if not isinstance(obj, Role):
                    continue
                for h in obj.handlers:
                    if isinstance(h, TaskFile) and h.key:
                        self._object_by_key[h.key] = h
                        for task in h.tasks:
                            if hasattr(task, "key") and task.key:
                                self._object_by_key[task.key] = task

        self._build_from_loaded(root_loaded, NodeScope.OWNED)
        self._build_from_loaded(ext_loaded, NodeScope.REFERENCED)

        self._wire_notify_listen()
        self._wire_data_flow()

        return self._graph

    def _resolve_key(self, key: str, expected_type: type | None = None) -> object | None:
        """Resolve a definition key string to the actual definition object.

        Args:
            key: Definition key string (e.g. ``play playbook:site.yml#play:[0]``).
            expected_type: If set, only return the object when it matches.

        Returns:
            The definition object, or ``None`` if not found or wrong type.
        """
        obj = self._object_by_key.get(key)
        if obj is None:
            return None
        if expected_type is not None and not isinstance(obj, expected_type):
            return None
        return obj

    def _build_from_loaded(
        self,
        loaded: dict[str, ObjectList],
        scope: NodeScope,
    ) -> None:
        """Process loaded definitions (playbooks, roles, taskfiles).

        Args:
            loaded: Output of ``load_all_definitions`` (playbooks, roles, taskfiles lists).
            scope: Whether nodes are owned project content or referenced externals.
        """
        from .models import Collection, ObjectList, Playbook, Role, TaskFile

        collections = loaded.get("collections", ObjectList())
        if isinstance(collections, ObjectList):
            for item in collections.items:
                if isinstance(item, Collection):
                    self._build_collection(item, scope)

        roles = loaded.get("roles", ObjectList())
        if isinstance(roles, ObjectList):
            for item in roles.items:
                if isinstance(item, Role):
                    self._build_role(item, scope)

        playbooks = loaded.get("playbooks", ObjectList())
        if isinstance(playbooks, ObjectList):
            for item in playbooks.items:
                if isinstance(item, Playbook):
                    self._build_playbook(item, scope)

        taskfiles = loaded.get("taskfiles", ObjectList())
        if isinstance(taskfiles, ObjectList):
            for item in taskfiles.items:
                if isinstance(item, TaskFile):
                    self._build_taskfile(item, scope=scope)

    # -- Collection ---------------------------------------------------------

    def _build_collection(self, coll: Collection, scope: NodeScope) -> str:
        """Build a COLLECTION graph node from a parsed Collection object.

        Normalizes the parser's raw data structures:

        - ``coll.metadata`` may be ``MANIFEST.json`` (galaxy.yml fields nested
          under ``collection_info``) or a flat ``galaxy.yml`` dict.  We store
          the raw dict as ``collection_metadata`` and extract ``namespace``/
          ``name`` from whichever level they appear.
        - ``coll.files`` may be ``FILES.json`` (a dict with a ``files`` list
          of ``{"name": ...}`` entries) or a flat list/dict of paths.  We
          normalize to a flat ``list[str]`` of relative paths.
        - ``coll.meta_runtime`` is already parsed ``meta/runtime.yml``.

        Args:
            coll: Parsed collection object.
            scope: Ownership scope for the created node.

        Returns:
            Collection node id.
        """
        coll_name = getattr(coll, "name", "") or ""
        coll_path = getattr(coll, "path", "") or coll_name
        identity = NodeIdentity(path=coll_path, node_type=NodeType.COLLECTION)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        metadata = _safe_dict(getattr(coll, "metadata", {}))
        meta_runtime = _safe_dict(getattr(coll, "meta_runtime", {}))
        collection_files = _normalize_collection_files(getattr(coll, "files", {}))

        ci = metadata.get("collection_info", {})
        if isinstance(ci, dict) and ci:
            ns = ci.get("namespace", "") or ""
            name = ci.get("name", "") or coll_name
        else:
            ns = metadata.get("namespace", "") or ""
            name = metadata.get("name", "") or coll_name

        node = ContentNode(
            identity=identity,
            file_path=coll_path,
            name=coll_name or None,
            collection_namespace=str(ns),
            collection_name=str(name),
            collection_metadata=metadata,
            collection_meta_runtime=meta_runtime,
            collection_files=collection_files,
            scope=scope,
        )
        self._graph.add_node(node)

        from .models import Module

        for mod_or_key in getattr(coll, "modules", []) or []:
            mod: Module | None = None
            if isinstance(mod_or_key, Module):
                mod = mod_or_key
            elif isinstance(mod_or_key, str):
                resolved = self._resolve_key(mod_or_key, Module)
                mod = cast("Module", resolved) if resolved else None
            if mod is not None:
                mod_nid = self._build_module(mod, scope)
                self._graph.add_edge(nid, mod_nid, EdgeType.CONTAINS)

        return nid

    def _build_module(self, mod: Module, scope: NodeScope) -> str:
        """Build a MODULE graph node from a parsed Module object.

        Reads the plugin ``.py`` file (if accessible) to populate
        ``module_line_count`` and ``module_functions_without_return_type``
        for L089/L090 rules.

        Args:
            mod: Parsed module object.
            scope: Ownership scope for the created node.

        Returns:
            Module node id.
        """
        mod_name = getattr(mod, "fqcn", "") or getattr(mod, "name", "") or ""
        defined_in = getattr(mod, "defined_in", "") or ""
        identity = NodeIdentity(path=defined_in or mod_name, node_type=NodeType.MODULE)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        resolved_path = defined_in
        if defined_in and not os.path.isabs(defined_in) and not os.path.isfile(defined_in) and self._scan_root:
            candidate = os.path.join(self._scan_root, defined_in)
            if os.path.isfile(candidate):
                resolved_path = candidate

        line_count, funcs_missing_return = _analyze_python_file(resolved_path)

        node = ContentNode(
            identity=identity,
            file_path=defined_in,
            name=mod_name or None,
            module_line_count=line_count,
            module_functions_without_return_type=funcs_missing_return,
            scope=scope,
        )
        self._graph.add_node(node)
        return nid

    # -- Playbook -----------------------------------------------------------

    def _build_playbook(self, pb: Playbook, scope: NodeScope) -> str:
        """Build graph nodes for a playbook and its plays.

        Args:
            pb: Parsed playbook object.
            scope: Ownership scope for created nodes.

        Returns:
            Playbook node id (its path identity).
        """
        from .models import Play

        file_path = getattr(pb, "defined_in", "") or ""
        identity = NodeIdentity(path=file_path, node_type=NodeType.PLAYBOOK)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            name=getattr(pb, "name", "") or os.path.basename(file_path),
            variables=_safe_dict(getattr(pb, "variables", {})),
            options=_safe_dict(getattr(pb, "options", {})),
            scope=scope,
        )
        self._graph.add_node(node)

        for i, play_or_key in enumerate(pb.plays):
            play: Play | None = None
            if isinstance(play_or_key, Play):
                play = play_or_key
            elif isinstance(play_or_key, str):
                resolved = self._resolve_key(play_or_key, Play)
                play = cast("Play", resolved) if resolved else None
            if play is None:
                continue

            if play.import_playbook:
                imported_nid = self._handle_import_playbook(play, nid, file_path, i, scope)
                if imported_nid:
                    continue

            play_nid = self._build_play(play, nid, file_path, i, scope, file_content=pb.yaml_lines)
            self._graph.add_edge(nid, play_nid, EdgeType.CONTAINS, position=i)

        return nid

    def _handle_import_playbook(
        self, play: Play, parent_nid: str, parent_file: str, position: int, scope: NodeScope
    ) -> str | None:
        """Handle import_playbook directive — creates an import edge.

        Args:
            play: Play declaring ``import_playbook``.
            parent_nid: Containing playbook node id.
            parent_file: Filesystem path of the parent playbook.
            position: Index among parent's children for edge ordering.
            scope: Ownership scope for a stub imported playbook node if created.

        Returns:
            Target playbook node id, or ``None`` if no import path.
        """
        import_path = play.import_playbook
        if not import_path:
            return None
        parent_dir = os.path.dirname(parent_file)
        resolved_path = os.path.normpath(os.path.join(parent_dir, import_path))
        target_nid = resolved_path
        if target_nid not in self._graph.g:
            target_identity = NodeIdentity(path=resolved_path, node_type=NodeType.PLAYBOOK)
            target_node = ContentNode(
                identity=target_identity,
                file_path=resolved_path,
                name=os.path.basename(resolved_path),
                scope=scope,
            )
            self._graph.add_node(target_node)
        self._graph.add_edge(parent_nid, target_nid, EdgeType.IMPORT, position=position)
        return target_nid

    # -- Play ---------------------------------------------------------------

    def _build_play(
        self,
        play: Play,
        playbook_nid: str,
        file_path: str,
        play_index: int,
        scope: NodeScope,
        *,
        file_content: str = "",
    ) -> str:
        """Build graph nodes for a play and its children.

        Args:
            play: Parsed play object.
            playbook_nid: Parent playbook node id.
            file_path: Playbook file path on disk.
            play_index: Zero-based index in ``pb.plays``.
            scope: Ownership scope for created nodes.
            file_content: Full playbook file text for extracting the
                play header YAML.

        Returns:
            Play node id (YAML-path identity under the playbook).
        """
        from .models import RoleInPlay, Task

        play_path = f"{file_path}/plays[{play_index}]"
        identity = NodeIdentity(path=play_path, node_type=NodeType.PLAY)
        nid = identity.path

        line_start, line_end = _extract_lines(play)
        if line_start == 0 and file_content:
            line_start, line_end = _find_play_lines(file_content, play_index)

        play_options = _safe_dict(getattr(play, "options", {}))

        when_raw = play_options.get("when")
        when_expr: str | list[str] | None
        if isinstance(when_raw, str):
            when_expr = when_raw
        elif isinstance(when_raw, list):
            when_expr = [str(x) for x in when_raw]
        else:
            when_expr = None

        environment_raw = play_options.get("environment")
        environment: YAMLDict | None = environment_raw if isinstance(environment_raw, dict) else None

        no_log_raw = play_options.get("no_log")
        no_log = no_log_raw if isinstance(no_log_raw, bool) else None

        ignore_errors_raw = play_options.get("ignore_errors")
        ignore_errors = ignore_errors_raw if isinstance(ignore_errors_raw, bool) else None

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(play, "name", None),
            variables=_safe_dict(getattr(play, "variables", {})),
            options=play_options,
            become=_extract_become(play),
            when_expr=when_expr,
            tags=_as_str_list(play_options.get("tags")),
            environment=environment,
            no_log=no_log,
            ignore_errors=ignore_errors,
            scope=scope,
        )
        self._graph.add_node(node)

        position = 0

        # vars_files
        for vf in getattr(play, "vars_files", []) or []:
            if isinstance(vf, str):
                vf_path = os.path.normpath(os.path.join(os.path.dirname(file_path), vf))
                vf_nid = self._ensure_vars_file(vf_path, scope)
                self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
                position += 1

        # static roles
        for rip_or_key in getattr(play, "roles", []) or []:
            if isinstance(rip_or_key, RoleInPlay):
                role_nid = self._resolve_role_nid(rip_or_key)
                if role_nid:
                    self._graph.add_edge(nid, role_nid, EdgeType.DEPENDENCY, position=position)
                    position += 1

        # pre_tasks, tasks, post_tasks
        for task_list_attr in ("pre_tasks", "tasks", "post_tasks"):
            task_list = getattr(play, task_list_attr, []) or []
            for task_or_key in task_list:
                task_obj: Task | None = None
                if isinstance(task_or_key, Task):
                    task_obj = task_or_key
                elif isinstance(task_or_key, str):
                    resolved = self._resolve_key(task_or_key, Task)
                    task_obj = cast("Task", resolved) if resolved else None
                if task_obj is not None:
                    task_nid = self._build_task(task_obj, nid, file_path, play_index, position, scope)
                    self._graph.add_edge(nid, task_nid, EdgeType.CONTAINS, position=position)
                    position += 1

        # handlers
        handler_list = getattr(play, "handlers", []) or []
        for h_idx, handler_or_key in enumerate(handler_list):
            handler_obj: Task | None = None
            if isinstance(handler_or_key, Task):
                handler_obj = handler_or_key
            elif isinstance(handler_or_key, str):
                resolved = self._resolve_key(handler_or_key, Task)
                handler_obj = cast("Task", resolved) if resolved else None
            if handler_obj is not None:
                h_nid = self._build_handler(handler_obj, nid, file_path, play_index, h_idx, scope)
                self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                position += 1

        if file_content and line_start > 0:
            node.yaml_lines = _extract_play_header(file_content, line_start, line_end, self._graph, nid)
            node.indent_depth = _detect_indent(node.yaml_lines)

        return nid

    # -- Task ---------------------------------------------------------------

    def _build_task(
        self,
        task: Task,
        parent_nid: str,
        file_path: str,
        play_index: int,
        position: int,
        scope: NodeScope,
        *,
        path_prefix: str = "",
    ) -> str:
        """Build a task node and wire executable edges.

        Args:
            task: Parsed task object.
            parent_nid: Immediate parent node id (play, block, or taskfile).
            file_path: Source file path for location metadata.
            play_index: Play index when under a play (used for line context).
            position: Sibling index for default path when ``path_prefix`` is empty.
            scope: Ownership scope for the new node.
            path_prefix: Override identity path (for nested block children).

        Returns:
            New task or block node id.
        """
        from .models import ExecutableType

        if not path_prefix:
            path_prefix = f"{parent_nid}/tasks[{position}]"

        is_block = bool(getattr(task, "module", "") == "" and _has_block_children(task))
        node_type = NodeType.BLOCK if is_block else NodeType.TASK
        identity = NodeIdentity(path=path_prefix, node_type=node_type)
        nid = identity.path

        line_start, line_end = _extract_lines(task)
        raw_options = _safe_dict(getattr(task, "options", {}))
        module_options = _safe_dict(getattr(task, "module_options", {}))

        # Strip block/rescue/always from node options — children are
        # already wired as graph edges via _wire_block_children().
        # Keeping Task objects here would cause JSON serialization
        # failures downstream.
        options = {k: v for k, v in raw_options.items() if k not in ("block", "rescue", "always")}

        when_raw = raw_options.get("when")
        when_expr: str | list[str] | None
        if isinstance(when_raw, str):
            when_expr = when_raw
        elif isinstance(when_raw, list):
            when_expr = [str(x) for x in when_raw]
        else:
            when_expr = None

        loop_control_raw = raw_options.get("loop_control")
        loop_control: YAMLDict | None = loop_control_raw if isinstance(loop_control_raw, dict) else None

        register_raw = raw_options.get("register")
        register = register_raw if isinstance(register_raw, str) else None

        environment_raw = raw_options.get("environment")
        environment: YAMLDict | None = environment_raw if isinstance(environment_raw, dict) else None

        no_log_raw = raw_options.get("no_log")
        no_log = no_log_raw if isinstance(no_log_raw, bool) else None

        ignore_errors_raw = raw_options.get("ignore_errors")
        ignore_errors = ignore_errors_raw if isinstance(ignore_errors_raw, bool) else None

        delegate_raw = raw_options.get("delegate_to")
        delegate_to = delegate_raw if isinstance(delegate_raw, str) else None

        exec_type = getattr(task, "executable_type", None)

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(task, "name", None),
            module=getattr(task, "module", "") or "",
            module_options=module_options,
            options=options,
            variables=_safe_dict(getattr(task, "variables", {})),
            become=_extract_become(task),
            when_expr=when_expr,
            tags=_as_str_list(options.get("tags")),
            loop=options.get("loop")
            or next(
                (options[k] for k in options if k.startswith("with_")),
                None,
            ),
            loop_control=loop_control,
            register=register,
            set_facts=_safe_dict(getattr(task, "set_facts", {})),
            notify=_as_str_list(options.get("notify")),
            environment=environment,
            no_log=no_log,
            ignore_errors=ignore_errors,
            changed_when=options.get("changed_when"),
            failed_when=options.get("failed_when"),
            yaml_lines=getattr(task, "yaml_lines", "") or "",
            indent_depth=_detect_indent(getattr(task, "yaml_lines", "") or ""),
            delegate_to=delegate_to,
            scope=scope,
        )
        self._graph.add_node(node)

        # Block children (rescue, always, block tasks)
        if is_block:
            self._wire_block_children(task, nid, file_path, play_index, scope)

        # Executable edges (import_tasks, include_tasks, import_role, include_role, module)
        executable = getattr(task, "executable", "") or ""
        if executable and exec_type:
            is_dynamic = _has_template(executable)
            if exec_type == ExecutableType.TASKFILE_TYPE:
                is_import = getattr(task, "module", "") in ("ansible.builtin.import_tasks", "import_tasks")
                edge_type = EdgeType.IMPORT if is_import else EdgeType.INCLUDE
                resolved = self._resolve_taskfile_path(executable, file_path)
                if resolved:
                    if resolved not in self._graph.g:
                        self._ensure_taskfile_node(resolved, scope)
                    self._graph.add_edge(
                        nid,
                        resolved,
                        edge_type,
                        dynamic=is_dynamic,
                        conditional=node.when_expr is not None,
                        when_expr=str(node.when_expr) if node.when_expr else None,
                    )
                else:
                    self.resolve_failures["taskfile"][executable] = (
                        self.resolve_failures["taskfile"].get(executable, 0) + 1
                    )
            elif exec_type == ExecutableType.ROLE_TYPE:
                is_import = getattr(task, "module", "") in ("ansible.builtin.import_role", "import_role")
                edge_type = EdgeType.IMPORT if is_import else EdgeType.INCLUDE
                role_nid = self._resolve_role_nid_by_name(executable)
                if role_nid:
                    self._graph.add_edge(
                        nid,
                        role_nid,
                        edge_type,
                        dynamic=is_dynamic,
                        conditional=node.when_expr is not None,
                    )
                else:
                    self.resolve_failures["role"][executable] = self.resolve_failures["role"].get(executable, 0) + 1

        return nid

    def _build_handler(
        self,
        task: Task,
        parent_nid: str,
        file_path: str,
        play_index: int,
        handler_index: int,
        scope: NodeScope,
    ) -> str:
        """Build a handler node.

        Args:
            task: Parsed handler task object.
            parent_nid: Containing play or role node id.
            file_path: Source file path.
            play_index: Play index when the parent is a play.
            handler_index: Index in the play's ``handlers`` list.
            scope: Ownership scope for the handler node.

        Returns:
            Handler node id.
        """
        path_prefix = f"{parent_nid}/handlers[{handler_index}]"
        identity = NodeIdentity(path=path_prefix, node_type=NodeType.HANDLER)
        nid = identity.path

        line_start, line_end = _extract_lines(task)
        options = _safe_dict(getattr(task, "options", {}))

        node = ContentNode(
            identity=identity,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            name=getattr(task, "name", None),
            module=getattr(task, "module", "") or "",
            module_options=_safe_dict(getattr(task, "module_options", {})),
            options=options,
            notify=_as_str_list(options.get("notify")),
            listen=_as_str_list(options.get("listen")),
            yaml_lines=getattr(task, "yaml_lines", "") or "",
            indent_depth=_detect_indent(getattr(task, "yaml_lines", "") or ""),
            scope=scope,
        )
        self._graph.add_node(node)
        return nid

    # -- Block children (rescue / always) -----------------------------------

    def _wire_block_children(
        self,
        task: Task,
        block_nid: str,
        file_path: str,
        play_index: int,
        scope: NodeScope,
    ) -> None:
        """Wire block → rescue and block → always edges.

        Args:
            task: Block task whose ``options`` hold nested task lists.
            block_nid: Node id of the block.
            file_path: Source file path for nested task construction.
            play_index: Play index for nested task construction.
            scope: Ownership scope for child tasks.
        """
        from .models import Task as TaskModel

        options = _safe_dict(getattr(task, "options", {}))

        for section, edge_type in [("rescue", EdgeType.RESCUE), ("always", EdgeType.ALWAYS)]:
            section_tasks = options.get(section)
            if not isinstance(section_tasks, list):
                continue
            for i, child_or_key in enumerate(section_tasks):
                child_task: TaskModel | None = None
                if isinstance(child_or_key, TaskModel):
                    child_task = child_or_key
                elif isinstance(child_or_key, str):
                    resolved = self._resolve_key(child_or_key, TaskModel)
                    child_task = cast(TaskModel, resolved) if resolved else None
                if child_task is not None:
                    child_path = f"{block_nid}/{section}[{i}]"
                    child_nid = self._build_task(
                        child_task, block_nid, file_path, play_index, i, scope, path_prefix=child_path
                    )
                    self._graph.add_edge(block_nid, child_nid, EdgeType.CONTAINS, position=i)
                    self._graph.add_edge(block_nid, child_nid, edge_type, position=i)

        block_tasks = options.get("block")
        if isinstance(block_tasks, list):
            from .models import Task as TaskModel

            for i, child_or_key in enumerate(block_tasks):
                child_task_b: TaskModel | None = None
                if isinstance(child_or_key, TaskModel):
                    child_task_b = child_or_key
                elif isinstance(child_or_key, str):
                    resolved = self._resolve_key(child_or_key, TaskModel)
                    child_task_b = cast(TaskModel, resolved) if resolved else None
                if child_task_b is not None:
                    child_path = f"{block_nid}/block[{i}]"
                    child_nid = self._build_task(
                        child_task_b, block_nid, file_path, play_index, i, scope, path_prefix=child_path
                    )
                    self._graph.add_edge(block_nid, child_nid, EdgeType.CONTAINS, position=i)

    # -- Role ---------------------------------------------------------------

    def _build_role(self, role: Role, scope: NodeScope) -> str:
        """Build graph nodes for a role.

        Args:
            role: Parsed role object.
            scope: Ownership scope for role and child nodes.

        Returns:
            Role node id (role path / ``defined_in`` identity).
        """
        from .models import Task, TaskFile

        role_fqcn = getattr(role, "fqcn", "") or getattr(role, "name", "") or ""
        defined_in = getattr(role, "defined_in", "") or ""
        role_path = defined_in or f"roles/{role_fqcn}"
        identity = NodeIdentity(path=role_path, node_type=NodeType.ROLE)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        raw_metadata = getattr(role, "metadata", None)
        role_metadata = _safe_dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

        node = ContentNode(
            identity=identity,
            file_path=defined_in,
            name=role_fqcn,
            role_fqcn=role_fqcn,
            default_variables=_safe_dict(getattr(role, "default_variables", {})),
            role_variables=_safe_dict(getattr(role, "variables", {})),
            role_metadata=role_metadata,
            scope=scope,
        )
        self._graph.add_node(node)

        # Taskfiles in this role
        position = 0
        for tf_or_key in getattr(role, "taskfiles", []) or []:
            tf_obj: TaskFile | None = None
            if isinstance(tf_or_key, TaskFile):
                tf_obj = tf_or_key
            elif isinstance(tf_or_key, str):
                resolved = self._resolve_key(tf_or_key, TaskFile)
                tf_obj = cast("TaskFile", resolved) if resolved else None
            if tf_obj is not None:
                tf_nid = self._build_taskfile(tf_obj, parent_nid=nid, scope=scope)
                if tf_nid:
                    self._graph.add_edge(nid, tf_nid, EdgeType.CONTAINS, position=position)
                    position += 1

        # Handlers
        for h_idx, handler_or_key in enumerate(getattr(role, "handlers", []) or []):
            handler: TaskFile | Task | None = None
            if isinstance(handler_or_key, TaskFile | Task):
                handler = handler_or_key
            elif isinstance(handler_or_key, str):
                resolved = self._resolve_key(handler_or_key)
                handler = resolved if isinstance(resolved, TaskFile | Task) else None
            if isinstance(handler, TaskFile):
                h_nid = self._build_taskfile(handler, parent_nid=nid, scope=scope, is_handler_file=True)
                if h_nid:
                    self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                    position += 1
            elif isinstance(handler, Task):
                h_nid = self._build_handler(handler, nid, defined_in, 0, h_idx, scope)
                self._graph.add_edge(nid, h_nid, EdgeType.CONTAINS, position=position)
                position += 1

        # Role defaults and vars as vars_file nodes
        if node.default_variables:
            defaults_path = os.path.join(role_path, "defaults/main.yml")
            vf_nid = self._ensure_vars_file(defaults_path, scope, node.default_variables)
            self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
            position += 1

        if node.role_variables:
            vars_path = os.path.join(role_path, "vars/main.yml")
            vf_nid = self._ensure_vars_file(vars_path, scope, node.role_variables)
            self._graph.add_edge(nid, vf_nid, EdgeType.VARS_INCLUDE, position=position)
            position += 1

        # Role dependencies
        for dep in getattr(role, "dependency", []) or []:
            if isinstance(dep, dict):
                dep_name = dep.get("role", "") or dep.get("name", "")
                if isinstance(dep_name, str) and dep_name:
                    dep_nid = self._resolve_role_nid_by_name(dep_name)
                    if dep_nid:
                        self._graph.add_edge(nid, dep_nid, EdgeType.DEPENDENCY)

        return nid

    # -- TaskFile -----------------------------------------------------------

    def _build_taskfile(
        self,
        tf: TaskFile,
        *,
        parent_nid: str = "",
        scope: NodeScope = NodeScope.OWNED,
        is_handler_file: bool = False,
    ) -> str:
        """Build graph nodes for a taskfile and its tasks.

        Args:
            tf: Parsed task file object.
            parent_nid: Optional parent role/play node for containment edges from caller.
            scope: Ownership scope for the taskfile and tasks.
            is_handler_file: If True, children are built as handlers not play tasks.

        Returns:
            Taskfile node id (``defined_in`` path).
        """
        from .models import Task

        defined_in = getattr(tf, "defined_in", "") or ""
        identity = NodeIdentity(path=defined_in, node_type=NodeType.TASKFILE)
        nid = identity.path

        if nid in self._visited:
            return nid
        self._visited.add(nid)

        node = ContentNode(
            identity=identity,
            file_path=defined_in,
            name=os.path.basename(defined_in) if defined_in else "",
            variables=_safe_dict(getattr(tf, "variables", {})),
            scope=scope,
        )
        self._graph.add_node(node)

        for i, task_or_key in enumerate(getattr(tf, "tasks", []) or []):
            task_obj: Task | None = None
            if isinstance(task_or_key, Task):
                task_obj = task_or_key
            elif isinstance(task_or_key, str):
                resolved = self._resolve_key(task_or_key, Task)
                task_obj = cast("Task", resolved) if resolved else None
            if task_obj is not None:
                if is_handler_file:
                    child_nid = self._build_handler(task_obj, nid, defined_in, 0, i, scope)
                else:
                    child_path = f"{nid}/tasks[{i}]"
                    child_nid = self._build_task(task_obj, nid, defined_in, 0, i, scope, path_prefix=child_path)
                self._graph.add_edge(nid, child_nid, EdgeType.CONTAINS, position=i)

        return nid

    # -- Helpers ------------------------------------------------------------

    def _ensure_vars_file(self, path: str, scope: NodeScope, variables: YAMLDict | None = None) -> str:
        """Get or create a vars_file node.

        Args:
            path: Normalized path used as node identity.
            scope: Ownership scope for a newly created node.
            variables: Optional variable snapshot stored on the node.

        Returns:
            Vars-file node id (same as ``path``).
        """
        nid = path
        if nid not in self._graph.g:
            identity = NodeIdentity(path=path, node_type=NodeType.VARS_FILE)
            node = ContentNode(
                identity=identity,
                file_path=path,
                name=os.path.basename(path),
                variables=variables or {},
                scope=scope,
            )
            self._graph.add_node(node)
        return nid

    def _ensure_taskfile_node(self, path: str, scope: NodeScope) -> str:
        """Create a minimal taskfile node if not already present.

        Args:
            path: Taskfile path used as node identity.
            scope: Ownership scope for a newly created stub node.

        Returns:
            Taskfile node id (same as ``path``).
        """
        nid = path
        if nid not in self._graph.g:
            identity = NodeIdentity(path=path, node_type=NodeType.TASKFILE)
            node = ContentNode(
                identity=identity,
                file_path=path,
                name=os.path.basename(path),
                scope=scope,
            )
            self._graph.add_node(node)
        return nid

    def _resolve_taskfile_path(self, reference: str, from_file: str) -> str:
        """Resolve a relative taskfile reference to a normalized path.

        Args:
            reference: Path as written in the task (may be relative).
            from_file: YAML file containing the reference.

        Returns:
            ``os.path.normpath`` of ``reference`` resolved from ``from_file``'s directory.
        """
        parent_dir = os.path.dirname(from_file)
        resolved = os.path.normpath(os.path.join(parent_dir, reference))
        return resolved

    def _resolve_role_nid(self, rip: RoleInPlay) -> str | None:
        """Resolve a RoleInPlay to a role node ID.

        Args:
            rip: Role-in-play declaration from a play's ``roles`` list.

        Returns:
            Matching role node id if already present in the graph, else ``None``.
        """
        name = getattr(rip, "name", "") or ""
        return self._resolve_role_nid_by_name(name)

    def _resolve_role_nid_by_name(self, name: str) -> str | None:
        """Resolve a role name to an existing role node ID.

        Args:
            name: Role name or FQCN as referenced from YAML.

        Returns:
            Role node id if a role node matches by FQCN, name, or short name.
        """
        if not name:
            return None
        for node in self._graph.nodes(NodeType.ROLE):
            if node.role_fqcn == name or node.name == name:
                return node.node_id
            basename = node.role_fqcn.rsplit(".", 1)[-1] if "." in node.role_fqcn else node.role_fqcn
            if basename == name:
                return node.node_id
        return None

    def _wire_notify_listen(self) -> None:
        """Create notify edges from tasks/handlers to handler nodes.

        Scans all handler nodes for names and ``listen`` topics, then links
        tasks and handlers that reference those names via ``notify``.
        """
        handlers_by_name: dict[str, list[str]] = {}
        handlers_by_listen: dict[str, list[str]] = {}

        for node in self._graph.nodes():
            if node.node_type == NodeType.HANDLER:
                if node.name:
                    handlers_by_name.setdefault(node.name, []).append(node.node_id)
                for topic in node.listen:
                    handlers_by_listen.setdefault(topic, []).append(node.node_id)

        for node in self._graph.nodes():
            if node.node_type not in (NodeType.TASK, NodeType.HANDLER):
                continue
            for handler_name in node.notify:
                targets = handlers_by_name.get(handler_name, [])
                for target_id in targets:
                    self._graph.add_edge(node.node_id, target_id, EdgeType.NOTIFY)
                listen_targets = handlers_by_listen.get(handler_name, [])
                for target_id in listen_targets:
                    self._graph.add_edge(node.node_id, target_id, EdgeType.LISTEN)

    def _wire_data_flow(self) -> None:
        """Create data_flow edges for register → consumers.

        Uses topological order to map ``register`` and ``set_facts`` producers
        to later tasks that reference those names in ``when`` or module args.
        """
        registered: dict[str, str] = {}
        set_fact_producers: dict[str, str] = {}

        for nid in self._graph.topological_order():
            node = self._graph.get_node(nid)
            if node is None:
                continue
            if node.register:
                registered[node.register] = nid
            for fact_name in node.set_facts:
                set_fact_producers[fact_name] = nid

        for nid in self._graph.topological_order():
            node = self._graph.get_node(nid)
            if node is None:
                continue
            referenced_vars = _extract_variable_references(node)
            for var_name in referenced_vars:
                producer = registered.get(var_name) or set_fact_producers.get(var_name)
                if producer and producer != nid:
                    self._graph.add_edge(producer, nid, EdgeType.DATA_FLOW)

            if node.register:
                registered[node.register] = nid
            for fact_name in node.set_facts:
                set_fact_producers[fact_name] = nid

        for nid in self._graph.topological_order():
            node = self._graph.get_node(nid)
            if node is None:
                continue
            referenced_vars = _extract_variable_references(node)
            for var_name in referenced_vars:
                producer = registered.get(var_name) or set_fact_producers.get(var_name)
                if producer and producer != nid:
                    self._graph.add_edge(producer, nid, EdgeType.DATA_FLOW)


# ---------------------------------------------------------------------------
# Definition loading
# ---------------------------------------------------------------------------


def _safe_object_list(v: object) -> list[object]:
    """Coerce a value to a list of model objects for definition loading.

    Accepts ``ObjectList``, plain ``list``, or returns empty list.

    Args:
        v: Value that may be ObjectList, list, or other.

    Returns:
        List of items suitable for definition registration.
    """
    from .models import CallObject, Object, ObjectList

    if isinstance(v, ObjectList):
        return list(v.items)
    if isinstance(v, list):
        return [x for x in v if isinstance(x, Object | CallObject)]
    return []


def _load_single_definition(defs: dict[str, object], key: str) -> ObjectList:
    """Load an ``ObjectList`` for one definition type key.

    Args:
        defs: Definitions dict keyed by type (e.g. ``roles``, ``tasks``).
        key: Type key to load.

    Returns:
        ``ObjectList`` containing items for that key.
    """
    from .models import CallObject, Object, ObjectList

    obj_list = ObjectList()
    items = _safe_object_list(defs.get(key, []))
    for item in items:
        if isinstance(item, Object | CallObject):
            obj_list.add(item)
    return obj_list


_DEFINITION_TYPES = ["collections", "roles", "taskfiles", "modules", "playbooks", "plays", "tasks"]


def _load_all_definitions(definitions: dict[str, object]) -> dict[str, ObjectList]:
    """Load all definition types from a project definitions structure.

    Normalizes the input (handles ``mappings`` wrapper vs flat dict),
    then merges per-artifact definitions into a single ``ObjectList``
    per type key.

    Args:
        definitions: Root definitions dict from project loader output.

    Returns:
        Dict mapping type keys to merged ``ObjectList`` instances.
    """
    from .models import ObjectList

    _definitions: dict[str, object] = {}
    _definitions = {"root": definitions} if "mappings" in definitions else definitions
    loaded: dict[str, ObjectList] = {}
    for type_key in _DEFINITION_TYPES:
        loaded[type_key] = ObjectList()
    for _, definitions_per_artifact in _definitions.items():
        defs_raw = definitions_per_artifact.get("definitions", {}) if isinstance(definitions_per_artifact, dict) else {}
        defs = defs_raw if isinstance(defs_raw, dict) else {}
        for type_key in _DEFINITION_TYPES:
            obj_list = _load_single_definition(defs, type_key)
            if type_key not in loaded:
                loaded[type_key] = obj_list
            else:
                loaded[type_key].merge(obj_list)
    return loaded


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _analyze_python_file(path: str) -> tuple[int, list[str]]:
    """Read a Python file and extract line count + functions missing return types.

    Uses ``ast.parse`` for reliable function-signature analysis.  Returns
    ``(0, [])`` when the file is unreadable or unparseable.

    Args:
        path: Filesystem path to a ``.py`` file.

    Returns:
        Tuple of ``(line_count, functions_without_return_type)``.
    """
    import ast

    if not path or not os.path.isfile(path):
        return 0, []

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError:
        return 0, []

    line_count = len(source.splitlines())

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return line_count, []

    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is None:
            missing.append(node.name)

    return line_count, missing


def _normalize_collection_files(files_raw: object) -> list[str]:
    """Normalize the parser's ``Collection.files`` into a flat list of relative paths.

    ``FILES.json`` is a dict like ``{"files": [{"name": "...", ...}, ...], "format": 1}``.
    A source-tree collection may instead have a plain list of strings or a dict
    whose keys are paths.  We handle all variants.

    Args:
        files_raw: The raw ``Collection.files`` value from the parsed collection.

    Returns:
        Sorted list of relative file-path strings.
    """
    if not files_raw:
        return []

    if isinstance(files_raw, dict):
        entries = files_raw.get("files", None)
        if isinstance(entries, list):
            paths: list[str] = []
            for entry in entries:
                if isinstance(entry, dict):
                    n = entry.get("name")
                    if isinstance(n, str):
                        paths.append(n)
                elif isinstance(entry, str):
                    paths.append(entry)
            return sorted(paths)
        return sorted(str(k) for k in files_raw if k != "format")

    if isinstance(files_raw, list):
        return sorted(str(f) for f in files_raw)

    return []


def _safe_dict(v: object) -> YAMLDict:
    """Return ``v`` if it is a dict, otherwise an empty dict.

    Args:
        v: Arbitrary value from project or YAML parsing.

    Returns:
        ``v`` when it is a ``dict``, else ``{}``.
    """
    return cast(YAMLDict, v) if isinstance(v, dict) else {}


def _find_play_lines(file_content: str, play_index: int) -> tuple[int, int]:
    """Derive 1-based line range for the nth play from playbook YAML.

    Scans for top-level list items (lines starting with ``- ``) which
    correspond to individual plays in a standard playbook.

    Args:
        file_content: Full playbook file text.
        play_index: Zero-based play index.

    Returns:
        ``(start, end)`` 1-based line numbers, or ``(0, 0)`` if the
        play index cannot be located.
    """
    lines = file_content.splitlines()
    play_starts: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith("- ") or line == "-":
            play_starts.append(i + 1)

    if play_index >= len(play_starts):
        return 0, 0

    start = play_starts[play_index]
    end = play_starts[play_index + 1] - 1 if play_index + 1 < len(play_starts) else len(lines)
    return start, end


def _extract_play_header(
    file_content: str,
    play_line_start: int,
    play_line_end: int,
    graph: ContentGraph,
    play_nid: str,
) -> str:
    """Extract the play header YAML (structural keys, without child bodies).

    Returns the lines from ``play_line_start`` up to (but not including)
    the first child node's start line.  If the play has no children with
    valid line numbers, the full play span is returned.

    Args:
        file_content: Full playbook file text.
        play_line_start: 1-based start line of the play.
        play_line_end: 1-based end line of the play.
        graph: ContentGraph (children already added).
        play_nid: Play node ID to query children from.

    Returns:
        Play header YAML string.
    """
    if not file_content or play_line_start < 1:
        return ""

    file_lines = file_content.splitlines(keepends=True)

    first_child_line = 0
    for child in graph.children(play_nid):
        if child.line_start > 0 and (first_child_line == 0 or child.line_start < first_child_line):
            first_child_line = child.line_start

    end = play_line_end if play_line_end > 0 else len(file_lines)
    if first_child_line > play_line_start:
        end = first_child_line - 1

    header = "".join(file_lines[play_line_start - 1 : end])
    return header.rstrip("\n") + "\n" if header.strip() else ""


def _extract_lines(obj: object) -> tuple[int, int]:
    """Extract start and end line numbers from a parsed object.

    Args:
        obj: Model instance that may expose ``line_num_in_file``.

    Returns:
        ``(start, end)`` line numbers, or ``(0, 0)`` if unavailable.
    """
    line_num = getattr(obj, "line_num_in_file", None)
    if isinstance(line_num, list | tuple) and len(line_num) >= 2:
        return int(line_num[0]), int(line_num[1])
    return 0, 0


def _extract_become(obj: object) -> YAMLDict | None:
    """Extract become info as a dict.

    Args:
        obj: Model instance that may expose ``become`` (dict or object).

    Returns:
        Normalized become mapping, or ``None`` if unset or not convertible.
    """
    become = getattr(obj, "become", None)
    if become is None:
        return None
    if isinstance(become, dict):
        return cast(YAMLDict, become)
    if hasattr(become, "__dict__"):
        return cast(
            YAMLDict,
            {k: v for k, v in become.__dict__.items() if not k.startswith("_")},
        )
    return None


def _has_block_children(task: object) -> bool:
    """Check if a task object has block/rescue/always children.

    Args:
        task: Task model whose ``options`` may list nested tasks.

    Returns:
        ``True`` if any of ``block``, ``rescue``, or ``always`` is a non-empty list.
    """
    options = _safe_dict(getattr(task, "options", {}))
    return any(isinstance(options.get(k), list) for k in ("block", "rescue", "always"))


def _extract_variable_references(node: ContentNode) -> set[str]:
    """Extract Jinja2 variable names from a node's content.

    Args:
        node: Task-like node with ``when_expr`` and ``module_options`` strings.

    Returns:
        Set of simple identifier names referenced in Jinja (best-effort).
    """
    refs: set[str] = set()
    texts: list[str] = []

    if node.when_expr:
        if isinstance(node.when_expr, list):
            texts.extend(str(w) for w in node.when_expr)
        else:
            texts.append(str(node.when_expr))

    for v in node.module_options.values():
        if isinstance(v, str):
            texts.append(v)

    for match in _JINJA_VAR.findall(" ".join(texts)):
        cleaned = match.strip("{} ").split("|")[0].split(".")[0].strip()
        if cleaned and cleaned.isidentifier():
            refs.add(cleaned)

    return refs
