"""Tree structures for Ansible call hierarchy (playbook -> play -> role -> task)."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from . import logger
from .keyutil import detect_type, key_delimiter, object_delimiter
from .model_loader import load_builtin_modules
from .models import (
    CallObject,
    ExecutableType,
    Load,
    LoadType,
    Module,
    Object,
    ObjectList,
    Play,
    Playbook,
    PlayCall,
    Role,
    RoleCall,
    RoleInPlay,
    Task,
    TaskCall,
    TaskFile,
    call_obj_from_spec,
)
from .risk_assessment_model import RAMClient

obj_type_dict = {
    "playbook": "playbooks",
    "play": "plays",
    "role": "roles",
    "taskfile": "taskfiles",
    "task": "tasks",
    "module": "modules",
}


@dataclass
class TreeNode:
    """Tree node for representing hierarchical (src, dst) relationships.

    Attributes:
        key: Node identifier string.
        children: List of child TreeNodes.
        definition: Arbitrary definition data for the node.
    """

    key: str = ""

    # children is a list of TreeNode
    children: list[TreeNode] = field(default_factory=list)

    definition: dict[str, object] = field(default_factory=dict)

    # load a list of (src, dst) as a tree structure
    # which is composed of multiple TreeNode
    @staticmethod
    def load(graph: list[tuple[str | None, str]] | None = None) -> TreeNode:
        """Load a tree from a list of (src, dst) pairs.

        Args:
            graph: List of (src, dst) tuples; exactly one pair must have src None.

        Returns:
            Root TreeNode built from the graph.

        Raises:
            ValueError: If graph does not have exactly one root (src is None).
        """
        if graph is None:
            graph = []
        root_key_cands = [pair[1] for pair in graph if pair[0] is None]
        if len(root_key_cands) != 1:
            raise ValueError(f"tree array must have only one top with src == None, but found {len(root_key_cands)}")
        root_key = root_key_cands[0]
        tree = TreeNode()
        tree, _ = tree.recursive_tree_load(root_key, graph)
        tree.key = root_key
        tree.children = tree.children
        tree.definition = tree.definition
        return tree

    # output list of (src, dst) to stdout or file
    def dump(self, path: str = "") -> None:
        """Output list of (src, dst) pairs to stdout or file.

        Args:
            path: File path to write JSON; if empty, prints to stdout.
        """
        src_dst_array = self.to_graph()
        if path == "":
            print(json.dumps(src_dst_array, indent=2))
        else:
            tree_json = json.dumps(src_dst_array)
            with open(path, "w") as file:
                file.write(tree_json)

    def to_str(self) -> str:
        """Serialize tree to JSON string.

        Returns:
            JSON string of (src, dst) pairs.
        """
        src_dst_array = self.to_graph()
        return json.dumps(src_dst_array)

    # return list of (src, dst)
    def to_graph(self) -> list[tuple[str | None, str]]:
        """Return list of (src, dst) pairs representing the tree.

        Returns:
            List of (src, dst) tuples.
        """
        return self.recursive_graph_dump(None, self)

    # return list of dst keys
    def to_keys(self) -> list[str]:
        """Return list of dst keys from the tree.

        Returns:
            List of destination key strings.
        """
        return [p[1] for p in self.to_graph()]

    # reutrn list of TreeNodes that are under this TreeNode
    def to_list(self) -> list[TreeNode]:
        """Return list of TreeNodes under this node (including self).

        Returns:
            List of TreeNodes in depth-first order.
        """
        return self.recursive_convert_to_list(self)

    def recursive_convert_to_list(self, node: TreeNode, nodelist: list[TreeNode] | None = None) -> list[TreeNode]:
        """Recursively build list of nodes in depth-first order.

        Args:
            node: Current node to process.
            nodelist: Accumulated list of nodes; defaults to empty list.

        Returns:
            List of TreeNodes including node and all descendants.
        """
        if nodelist is None:
            nodelist = []
        current = [pair for pair in nodelist]
        current.append(node)
        for child_node in node.children:
            current = self.recursive_convert_to_list(child_node, current)
        return current

    def recursive_tree_load(
        self,
        node_key: str,
        src_dst_array: list[tuple[str | None, str]],
        parent_keys: set[str] | None = None,
    ) -> tuple[TreeNode, set[str]]:
        """Recursively load tree from (src, dst) pairs.

        Args:
            node_key: Key of current node.
            src_dst_array: List of (src, dst) tuples.
            parent_keys: Set of ancestor keys to detect cycles; defaults to empty.

        Returns:
            Tuple of (TreeNode for node_key, updated parent_keys set).
        """
        if parent_keys is None:
            parent_keys = set()
        n = TreeNode(key=node_key)
        if node_key in parent_keys:
            return n, parent_keys
        parent_keys.add(node_key)
        new_parent_keys = parent_keys.copy()
        for src_key, dst_key in src_dst_array:
            children_keys = []
            if node_key == src_key:
                children_keys.append(dst_key)
            for c_key in children_keys:
                child_tree, sub_parent_keys = self.recursive_tree_load(c_key, src_dst_array, parent_keys)
                n.children.append(child_tree)
                new_parent_keys = new_parent_keys.union(sub_parent_keys)
        return n, new_parent_keys

    def recursive_graph_dump(
        self,
        parent_node: TreeNode | None,
        node: TreeNode,
        src_dst_array: list[tuple[str | None, str]] | None = None,
    ) -> list[tuple[str | None, str]]:
        """Recursively dump tree to list of (src, dst) pairs.

        Args:
            parent_node: Parent of node; None for root.
            node: Current node to dump.
            src_dst_array: Accumulated pairs; defaults to empty list.

        Returns:
            List of (src, dst) tuples.
        """
        if src_dst_array is None:
            src_dst_array = []
        current = [pair for pair in src_dst_array]
        src = None if parent_node is None else parent_node.key
        dst = node.key
        current.append((src, dst))
        for child_node in node.children:
            is_included = len([(src, dst) for (src, dst) in current if src == child_node.key]) > 0
            if is_included:
                continue
            current = self.recursive_graph_dump(node, child_node, current)
        return current

    # return a list of (src, dst) which ends with the "end_key"
    # this could return multiple paths
    def path_to_root(self, end_key: str) -> list[TreeNode]:
        """Return branches from root to nodes with end_key.

        Args:
            end_key: Key of target node(s).

        Returns:
            List of TreeNodes representing paths (each path is a branch).
        """
        path_array = self.search_branch_to_key(end_key, self)
        return [nodelist2branch(nodelist) for nodelist in path_array]

    def search_branch_to_key(
        self,
        search_key: str,
        node: TreeNode,
        ancestors: list[TreeNode] | None = None,
    ) -> list[list[TreeNode]]:
        """Find all branches from root to nodes matching search_key.

        Args:
            search_key: Key of target node(s).
            node: Current node to search.
            ancestors: Accumulated ancestor nodes.

        Returns:
            List of node lists, each a path from root to a matching node.
        """
        if ancestors is None:
            ancestors = []
        current = [n for n in ancestors]
        found: list[list[TreeNode]] = []
        if node.key == search_key:
            found.append(current + [node])
        for child_node in node.children:
            found_in_child = self.search_branch_to_key(search_key, child_node, current + [node])
            found.extend(found_in_child)
        return found

    def copy(self) -> TreeNode:
        """Return a deep copy of this node and its children.

        Returns:
            Deep copy of this TreeNode.

        """
        return deepcopy(self)

    @property
    def is_empty(self) -> bool:
        """Return True if node has no key and no children."""
        return self.key == "" and len(self.children) == 0

    @property
    def has_definition(self) -> bool:
        """Return True if definition dict is empty (no keys)."""
        return len(self.definition) == 0


def nodelist2branch(nodelist: list[TreeNode]) -> TreeNode:
    """Convert a list of TreeNodes into a single branch (parent-child chain).

    Args:
        nodelist: List of nodes in order from root to leaf.

    Returns:
        TreeNode branch with first node as root and rest as children.
    """
    if len(nodelist) == 0:
        return TreeNode()
    t = nodelist[0].copy()
    current = t
    for i, n in enumerate(nodelist):
        if i == 0:
            continue
        current.children = [n.copy()]
        current = current.children[0]
    return t


def _safe_list(v: object) -> list[Object | CallObject]:
    """Get a list from a value that might be list or ObjectList.

    Args:
        v: Value that may be ObjectList, list, or other.

    Returns:
        List of Object or CallObject items.
    """
    if isinstance(v, ObjectList):
        return v.items
    if isinstance(v, list):
        return [x for x in v if isinstance(x, Object | CallObject)]
    return []


def _safe_dict(v: object) -> dict[str, object]:
    """Get a dict from a value that might be dict.

    Args:
        v: Value that may be dict or other.

    Returns:
        The dict if v is dict, else empty dict.
    """
    return v if isinstance(v, dict) else {}


def _ram_match_object(match: object) -> Object | CallObject | None:
    """Extract 'object' from a RAM search result dict if it's an Object/CallObject.

    Args:
        match: RAM search result (typically a dict).

    Returns:
        Object or CallObject if present and valid, else None.
    """
    if not isinstance(match, dict):
        return None
    obj = match.get("object")
    return obj if isinstance(obj, Object | CallObject) else None


def _ram_match_defined_in(match: object) -> str:
    """Extract 'defined_in' from a RAM search result for display.

    Args:
        match: RAM search result (typically a dict).

    Returns:
        defined_in string or empty string.
    """
    if not isinstance(match, dict):
        return ""
    di = match.get("defined_in")
    return str(di) if di is not None else ""


def _ram_match_offspring(match: object) -> list[dict[str, object]]:
    """Extract 'offspring_objects' from a RAM search result as list of dicts.

    Args:
        match: RAM search result (typically a dict).

    Returns:
        List of offspring dicts.
    """
    if not isinstance(match, dict):
        return []
    raw = match.get("offspring_objects", [])
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def load_single_definition(defs: dict[str, object], key: str) -> ObjectList:
    """Load ObjectList for a single definition key.

    Args:
        defs: Definitions dict keyed by type.
        key: Type key (e.g. 'roles', 'modules').

    Returns:
        ObjectList of items for that key.
    """
    obj_list = ObjectList()
    items = _safe_list(defs.get(key, []))
    for item in items:
        if isinstance(item, Object | CallObject):
            obj_list.add(item)
    return obj_list


def load_definitions(defs: dict[str, object], types: list[str]) -> list[ObjectList]:
    """Load ObjectLists for multiple definition types.

    Args:
        defs: Definitions dict.
        types: List of type keys to load.

    Returns:
        List of ObjectLists in same order as types.
    """
    def_list = []
    for type_key in types:
        objs_per_type = load_single_definition(defs, type_key)
        def_list.append(objs_per_type)
    return def_list


def load_all_definitions(definitions: dict[str, object]) -> dict[str, ObjectList]:
    """Load all definition types from definitions structure.

    Args:
        definitions: Root definitions dict, possibly with 'mappings' or per-artifact defs.

    Returns:
        Dict mapping type keys to ObjectLists.
    """
    _definitions: dict[str, object] = {}
    _definitions = {"root": definitions} if "mappings" in definitions else definitions
    loaded: dict[str, ObjectList] = {}
    types = ["collections", "roles", "taskfiles", "modules", "playbooks", "plays", "tasks"]
    for type_key in types:
        loaded[type_key] = ObjectList()
    for _, definitions_per_artifact in _definitions.items():
        defs_raw = definitions_per_artifact.get("definitions", {}) if isinstance(definitions_per_artifact, dict) else {}
        defs = defs_raw if isinstance(defs_raw, dict) else {}
        def_list = load_definitions(defs, types)
        for i, type_key in enumerate(types):
            if type_key not in loaded:
                loaded[type_key] = def_list[i]
            else:
                loaded[type_key].merge(def_list[i])
    return loaded


def make_dicts(
    root_definitions: dict[str, ObjectList],
    ext_definitions: dict[str, ObjectList],
) -> dict[str, dict[str, object]]:
    """Merge root and ext definitions into type-keyed object dicts.

    Args:
        root_definitions: Root ObjectLists by type.
        ext_definitions: External ObjectLists by type.

    Returns:
        Dict mapping type keys to key->object dicts.
    """
    definitions: dict[str, ObjectList] = {
        "roles": ObjectList(),
        "modules": ObjectList(),
        "taskfiles": ObjectList(),
        "playbooks": ObjectList(),
    }
    for type_key in definitions:
        root_val = root_definitions.get(type_key, ObjectList())
        ext_val = ext_definitions.get(type_key, ObjectList())
        if isinstance(root_val, ObjectList):
            definitions[type_key].merge(root_val)
        if isinstance(ext_val, ObjectList):
            definitions[type_key].merge(ext_val)
    dicts: dict[str, dict[str, object]] = {k: {} for k in definitions}
    for type_key, obj_list in definitions.items():
        for obj in obj_list.items:
            obj_dict_key = obj.fqcn if hasattr(obj, "fqcn") else obj.key
            if type_key not in dicts:
                dicts[type_key] = {}
            dicts[type_key][obj_dict_key] = obj
    return dicts


def load_module_redirects(
    root_definitions: dict[str, ObjectList],
    ext_definitions: dict[str, ObjectList],
    module_dict: dict[str, object] | None = None,
) -> dict[str, str]:
    """Build short module name -> full key redirect map from collections.

    Args:
        root_definitions: Root definitions including collections.
        ext_definitions: External definitions including collections.
        module_dict: Optional module key->object dict for lookup.

    Returns:
        Dict mapping short module names to full module keys.
    """
    if module_dict is None:
        module_dict = {}
    collection_list = root_definitions.get("collections", ObjectList())
    ext_collection_list = ext_definitions.get("collections", ObjectList())
    if isinstance(collection_list, ObjectList) and isinstance(ext_collection_list, ObjectList):
        collection_list.merge(ext_collection_list)
    redirects: dict[str, str] = {}
    coll_items = collection_list.items if isinstance(collection_list, ObjectList) else []
    for coll in coll_items:
        meta = getattr(coll, "meta_runtime", None)
        if not meta:
            continue
        if not isinstance(meta, dict):
            continue
        plugin_routing = meta.get("plugin_routing", {})
        if not isinstance(plugin_routing, dict):
            continue
        modules_dict = plugin_routing.get("modules", {})
        if not isinstance(modules_dict, dict):
            continue
        for short_name, routing in modules_dict.items():
            redirect_to = routing.get("redirect", "") if isinstance(routing, dict) else ""
            if str(short_name) in redirects:
                continue
            found_module = module_dict.get(str(redirect_to))
            if found_module is not None and hasattr(found_module, "key"):
                redirects[str(short_name)] = getattr(found_module, "key", "")
    return redirects


def resolve(obj: Task | Play, dicts: dict[str, dict[str, object]]) -> tuple[Task | Play, bool]:
    """Resolve executable/role references in Task or Play.

    Args:
        obj: Task or Play to resolve.
        dicts: Type-keyed dicts of objects (modules, roles, taskfiles).

    Returns:
        Tuple of (modified obj, True if any resolution failed).
    """
    failed = False
    if isinstance(obj, Task):
        task = obj
        if task.executable != "":
            if task.executable_type == ExecutableType.MODULE_TYPE:
                task.resolved_name = resolve_module(task.executable, dicts.get("modules", {}))
            elif task.executable_type == ExecutableType.ROLE_TYPE:
                task.resolved_name = resolve_role(
                    task.executable,
                    dicts.get("roles", {}),
                    task.collection,
                    task.collections_in_play,
                )
            elif task.executable_type == ExecutableType.TASKFILE_TYPE:
                task.resolved_name = resolve_taskfile(task.executable, dicts.get("taskfiles", {}), task.key)
            if task.resolved_name == "":
                failed = True
    elif isinstance(obj, Play):
        for i in range(len(obj.roles)):
            roleinplay = obj.roles[i]
            if not isinstance(roleinplay, RoleInPlay):
                continue
            roleinplay.resolved_name = resolve_role(
                roleinplay.name,
                dicts.get("roles", {}),
                roleinplay.collection,
                roleinplay.collections_in_play,
            )
            obj.roles[i] = roleinplay
            if roleinplay.resolved_name == "":
                failed = True
    return obj, failed


def resolve_module(
    module_name: str,
    module_dict: dict[str, object] | None = None,
    module_redirects: dict[str, str] | None = None,
) -> str:
    """Resolve module name to full module key.

    Args:
        module_name: Short or FQCN module name.
        module_dict: Optional module key->object dict.
        module_redirects: Optional short name -> key redirect map.

    Returns:
        Full module key or empty string if not found.
    """
    if module_redirects is None:
        module_redirects = {}
    if module_dict is None:
        module_dict = {}
    module_key = ""
    found_module = module_dict.get(module_name)
    if found_module is not None and hasattr(found_module, "key"):
        module_key = getattr(found_module, "key", "")
    if module_key == "":
        for k in module_dict:
            suffix = f".{module_name}"
            if k.endswith(suffix):
                mod = module_dict[k]
                if hasattr(mod, "key"):
                    module_key = getattr(mod, "key", "")
                break
    if module_key == "" and module_name in module_redirects:
        module_key = module_redirects[module_name]
    return module_key


def resolve_role(
    role_name: str,
    role_dict: dict[str, object] | None = None,
    my_collection_name: str = "",
    collections_in_play: list[str] | None = None,
) -> str:
    """Resolve role name to full role key.

    Args:
        role_name: Short or FQCN role name.
        role_dict: Optional role key->object dict.
        my_collection_name: Collection context for short names.
        collections_in_play: Collections to try for short names.

    Returns:
        Full role key or empty string if not found.
    """
    if collections_in_play is None:
        collections_in_play = []
    if role_dict is None:
        role_dict = {}
    if os.sep in role_name:
        role_name = os.path.basename(role_name)

    role_key = ""
    if "." not in role_name and len(collections_in_play) > 0:
        for coll in collections_in_play:
            role_name_cand = f"{coll}.{role_name}"
            found_role = role_dict.get(role_name_cand)
            if found_role is not None and hasattr(found_role, "key"):
                role_key = getattr(found_role, "key", "")
    else:
        if "." not in role_name and my_collection_name != "":
            role_name_cand = f"{my_collection_name}.{role_name}"
            found_role = role_dict.get(role_name_cand)
            if found_role is not None and hasattr(found_role, "key"):
                role_key = getattr(found_role, "key", "")
    if role_key == "":
        found_role = role_dict.get(role_name)
        if found_role is not None and hasattr(found_role, "key"):
            role_key = getattr(found_role, "key", "")
        else:
            for k in role_dict:
                suffix = f".{role_name}"
                if k.endswith(suffix):
                    r = role_dict[k]
                    if hasattr(r, "key"):
                        role_key = getattr(r, "key", "")
                    break
    return role_key


def resolve_taskfile(
    taskfile_ref: str,
    taskfile_dict: dict[str, object] | None = None,
    task_key: str = "",
) -> str:
    """Resolve taskfile reference (path) to full taskfile key.

    Args:
        taskfile_ref: Path or reference to taskfile.
        taskfile_dict: Optional taskfile key->object dict.
        task_key: Key of calling task for context.

    Returns:
        Full taskfile key or empty string if not found.
    """
    if taskfile_dict is None:
        taskfile_dict = {}
    type_prefix = "task "
    parts = task_key[len(type_prefix) :].split(object_delimiter)
    parent_key = ""
    task_defined_path = ""
    for p in parts[::-1]:
        if p.startswith("playbook" + key_delimiter) or p.startswith("taskfile" + key_delimiter):
            task_defined_path = p.split(key_delimiter)[1]
            parent_key = task_key[len(type_prefix) :].split(p)[0]
            break

    # include/import tasks can have a path like "roles/xxxx/tasks/yyyy.yml"
    # then try to find roles directory
    if taskfile_ref.startswith("roles/") and "roles/" in task_defined_path:
        roles_parent_dir = task_defined_path.split("roles/")[0]
        fpath = os.path.join(roles_parent_dir, taskfile_ref)
        fpath = os.path.normpath(fpath)
        taskfile_key = f"taskfile {parent_key}taskfile{key_delimiter}{fpath}"
        found_tf = taskfile_dict.get(taskfile_key)
        if found_tf is not None and hasattr(found_tf, "key"):
            return str(getattr(found_tf, "key", ""))

    task_dir = os.path.dirname(task_defined_path)
    fpath = os.path.join(task_dir, taskfile_ref)
    # need to normalize path here because taskfile_ref can be
    # something like "../some_taskfile.yml".
    # it should be "tasks/some_taskfile.yml"
    fpath = os.path.normpath(fpath)
    taskfile_key = f"taskfile {parent_key}taskfile{key_delimiter}{fpath}"
    found_tf = taskfile_dict.get(taskfile_key)
    if found_tf is not None and hasattr(found_tf, "key"):
        return str(getattr(found_tf, "key", ""))

    return ""


def resolve_playbook(
    playbook_ref: str,
    playbook_dict: dict[str, object] | None = None,
    play_key: str = "",
) -> str:
    """Resolve playbook reference (path) to full playbook key.

    Args:
        playbook_ref: Path or reference to playbook.
        playbook_dict: Optional playbook key->object dict.
        play_key: Key of calling play for context.

    Returns:
        Full playbook key or empty string if not found.
    """
    if playbook_dict is None:
        playbook_dict = {}
    type_prefix = "play "
    parts = play_key[len(type_prefix) :].split(object_delimiter)
    parent_key = ""
    play_defined_path = ""
    for p in parts[::-1]:
        if p.startswith("playbook" + key_delimiter):
            play_defined_path = p.split(key_delimiter)[1]
            parent_key = play_key[len(type_prefix) :].split(p)[0]
            break

    play_dir = os.path.dirname(play_defined_path)
    fpath = os.path.join(play_dir, playbook_ref)
    # need to normalize path here because playbook_ref can be
    # something like "../some_playbook.yml"
    fpath = os.path.normpath(fpath)
    playbook_key = f"playbook {parent_key}playbook{key_delimiter}{fpath}"
    found_playbook = playbook_dict.get(playbook_key)
    if found_playbook is not None and hasattr(found_playbook, "key"):
        return str(getattr(found_playbook, "key", ""))
    return ""


def init_builtin_modules() -> list[Module]:
    """Load and return builtin Ansible modules.

    Returns:
        List of Module objects for builtin modules.
    """
    builtin_module_dict = load_builtin_modules()
    modules = list(builtin_module_dict.values())
    return modules


class TreeLoader:
    """Loads and traverses Ansible object trees from definitions."""

    def __init__(
        self,
        root_definitions: dict[str, object],
        ext_definitions: dict[str, object],
        ram_client: RAMClient | None = None,
        target_playbook_path: str | None = None,
        target_taskfile_path: str | None = None,
        load_all_taskfiles: bool = False,
    ) -> None:
        """Initialize TreeLoader.

        Args:
            root_definitions: Root definitions from ARI/parser.
            ext_definitions: External definitions.
            ram_client: Optional RAM client for external lookups.
            target_playbook_path: Optional target playbook filter.
            target_taskfile_path: Optional target taskfile filter.
            load_all_taskfiles: Whether to load all taskfiles in roles.
        """
        self.ram_client: RAMClient | None = ram_client

        self.org_root_definitions = root_definitions
        self.org_ext_definitions = ext_definitions

        self.root_definitions = load_all_definitions(root_definitions)
        self.ext_definitions = load_all_definitions(ext_definitions)
        self.add_builtin_modules()
        self._register_handler_taskfiles()

        self.dicts = make_dicts(self.root_definitions, self.ext_definitions)

        self.module_redirects = load_module_redirects(
            self.root_definitions, self.ext_definitions, self.dicts["modules"]
        )

        # use mappings just to get tree tops (playbook/role)
        # we don't load any files by this mappings here
        self.load_and_mapping = root_definitions.get("mappings")
        load_mapping = cast(Load | None, self.load_and_mapping) if self.load_and_mapping else None
        # Parser produces list of [path, key]; cast for indexing as (src, dst)
        self.playbook_mappings: list[tuple[str | None, str]] = (
            cast(list[tuple[str | None, str]], load_mapping.playbooks) if load_mapping else []
        )
        self.role_mappings: list[tuple[str | None, str]] = (
            cast(list[tuple[str | None, str]], load_mapping.roles) if load_mapping else []
        )
        self.taskfile_mappings: list[tuple[str | None, str]] = []

        # role can have child playbooks (mostly for test)
        # we add them to playbook_mappins here
        if self.role_mappings:
            for mapping in self.role_mappings:
                role_key = mapping[1]
                role = self.get_object(role_key, False)
                if not role or not isinstance(role, Role):
                    continue
                playbook_keys = role.playbooks
                playbook_mappings_in_role = [
                    (None, p_key.key if isinstance(p_key, Playbook) else str(p_key)) for p_key in playbook_keys
                ]
                self.playbook_mappings.extend(playbook_mappings_in_role)

        if target_playbook_path:
            self.playbook_mappings = [p for p in self.playbook_mappings if p[0] == target_playbook_path]
            self.role_mappings = []

        # some taskfiles might not be included from `tasks/main.yml` of a role.
        # ARI does not scan them by default, but it does when `load_all_taskfiles == True`
        if load_all_taskfiles:
            for mapping in self.role_mappings:
                role_key = mapping[1]
                role = self.get_object(role_key, False)
                if not role or not isinstance(role, Role):
                    continue
                taskfile_keys = role.taskfiles
                taskfile_mappings_in_role = [
                    (None, tf_key.key if isinstance(tf_key, TaskFile) else str(tf_key)) for tf_key in taskfile_keys
                ]
                self.taskfile_mappings.extend(taskfile_mappings_in_role)
            self.taskfile_mappings.extend(
                cast(list[tuple[str | None, str]], load_mapping.taskfiles) if load_mapping else []
            )
        # or, if the scan is for a single taskfile, ARI just scans it
        elif target_taskfile_path:
            _load_mapping = cast(Load | None, self.load_and_mapping) if self.load_and_mapping else None
            raw_taskfiles = _load_mapping.taskfiles if _load_mapping else []
            self.taskfile_mappings = cast(list[tuple[str | None, str]], raw_taskfiles)
            self.taskfile_mappings = [tf for tf in self.taskfile_mappings if tf[0] == target_taskfile_path]
            self.playbook_mappings = []
            self.role_mappings = []

        # TODO: dependency check, especially for
        # collection dependencies for role

        self.target_playbook_path = target_playbook_path
        self.load_all_taskfiles = load_all_taskfiles

        self.module_resolve_cache: dict[str, str] = {}
        self.role_resolve_cache: dict[str, str] = {}
        self.taskfile_resolve_cache: dict[str, str] = {}

        self.resolved_module_from_ram: dict[str, tuple[str, str]] = {}
        self.resolved_role_from_ram: dict[str, tuple[str, str]] = {}
        self.resolved_taskfile_from_ram: dict[str, tuple[str, str]] = {}

        self.extra_requirements: list[dict[str, object]] = []
        self.extra_requirement_obj_set: set[str] = set()

        self.trees: list[ObjectList] = []

        self.resolve_failures: dict[str, dict[str, int]] = {
            "module": {},
            "taskfile": {},
            "role": {},
        }
        return

    def run(self) -> tuple[list[ObjectList], ObjectList]:
        """Build trees from playbook and role mappings.

        Returns:
            Tuple of (list of ObjectLists per tree, additional project objects).
        """
        additional_objects = ObjectList()
        load_mapping = cast(Load | None, self.load_and_mapping) if self.load_and_mapping else None
        if load_mapping and load_mapping.target_type == LoadType.PROJECT:
            definitions = _safe_dict(self.org_root_definitions.get("definitions", {}))
            p_defs = _safe_list(definitions.get("projects", []))
            if len(p_defs) > 0:
                first = p_defs[0]
                if isinstance(first, Object | CallObject):
                    additional_objects.add(first)
        covered_taskfiles = []
        for i, mapping in enumerate(self.playbook_mappings):
            logger.debug(f"[{i + 1}/{len(self.playbook_mappings)}] {mapping[1]}")
            playbook_key = mapping[1]
            tree_objects = self._recursive_get_calls(playbook_key)
            self.trees.append(tree_objects)

            if self.load_all_taskfiles and tree_objects and tree_objects.items:
                for call_obj in tree_objects.items:
                    if not isinstance(call_obj, CallObject):
                        continue
                    spec_obj = call_obj.spec
                    if isinstance(spec_obj, TaskFile):
                        taskfile_key = spec_obj.key
                        if taskfile_key not in covered_taskfiles:
                            covered_taskfiles.append(taskfile_key)

        for i, mapping in enumerate(self.role_mappings):
            logger.debug(f"[{i + 1}/{len(self.role_mappings)}] {mapping[1]}")
            role_key = mapping[1]
            tree_objects = self._recursive_get_calls(role_key)
            self.trees.append(tree_objects)

            if self.load_all_taskfiles and tree_objects and tree_objects.items:
                for call_obj in tree_objects.items:
                    if not isinstance(call_obj, CallObject):
                        continue
                    spec_obj = call_obj.spec
                    if isinstance(spec_obj, TaskFile):
                        taskfile_key = spec_obj.key
                        if taskfile_key not in covered_taskfiles:
                            covered_taskfiles.append(taskfile_key)

        for i, mapping in enumerate(self.taskfile_mappings):
            logger.debug(f"[{i + 1}/{len(self.taskfile_mappings)}] {mapping[1]}")
            taskfile_key = mapping[1]
            if self.load_all_taskfiles and taskfile_key in covered_taskfiles:
                continue
            tree_objects = self._recursive_get_calls(taskfile_key)
            self.trees.append(tree_objects)
        return self.trees, additional_objects

    def _recursive_get_calls(
        self,
        key: str,
        caller: CallObject | None = None,
        handover: dict[str, object] | None = None,
        index: int = 0,
        history: list[str] | None = None,
    ) -> ObjectList:
        """Recursively collect call objects for a key and its children.

        Args:
            key: Object key to load.
            caller: Optional caller CallObject.
            handover: Optional handover dict for child context.
            index: Call index.
            history: Optional list of visited keys.

        Returns:
            ObjectList of CallObjects for this subtree.
        """
        if history is None:
            history = []
        if handover is None:
            handover = {}
        obj_list = ObjectList()
        obj = self.get_object(key)
        if obj is None:
            return obj_list
        if key in history:
            return obj_list
        spec_obj = obj.spec if isinstance(obj, CallObject) else obj
        _history = []
        if history:
            _history = [h for h in history]
        call_obj = call_obj_from_spec(
            spec=spec_obj,
            caller=caller,
            index=index,
        )
        if call_obj is not None:
            obj_list.add(call_obj, update_dict=False)
            _history.append(key)
        children_keys, from_ram, handover = self._get_children_keys(obj, handover_from_upper_node=handover)
        for i, c_key in enumerate(children_keys):
            loop_found = False
            loop_obj = None
            if c_key in _history:
                loop_found = True
                loop_obj = self.get_object(c_key)
                if isinstance(loop_obj, CallObject):
                    loop_obj = loop_obj.spec
            child_objects = self._recursive_get_calls(
                c_key,
                call_obj,
                handover,
                i,
                _history,
            )
            if isinstance(call_obj, TaskCall):
                taskcall = call_obj
                task_spec = cast(Task, taskcall.spec)
                if len(child_objects.items) > 0:
                    c_obj = cast(CallObject, child_objects.items[0])
                    if task_spec.executable_type == ExecutableType.MODULE_TYPE:
                        mod_spec = cast(Module, c_obj.spec)
                        taskcall.module = mod_spec
                        if c_key in from_ram:
                            req_info = from_ram[c_key]
                            task_spec.possible_candidates = [(mod_spec.fqcn, req_info)]
                        else:
                            task_spec.resolved_name = mod_spec.fqcn
                        task_spec.module_info = {
                            "collection": mod_spec.collection,
                            "short_name": mod_spec.name,
                            "fqcn": mod_spec.fqcn,
                            "key": mod_spec.key,
                        }
                    elif task_spec.executable_type == ExecutableType.ROLE_TYPE:
                        role_spec = cast(Role, c_obj.spec)
                        if c_key in from_ram:
                            req_info = from_ram[c_key]
                            task_spec.possible_candidates = [(role_spec.fqcn, req_info)]
                        else:
                            task_spec.resolved_name = role_spec.fqcn
                        task_spec.include_info = {
                            "type": "role",
                            "fqcn": role_spec.fqcn,
                            "path": role_spec.defined_in,
                            "key": role_spec.key,
                        }
                    elif task_spec.executable_type == ExecutableType.TASKFILE_TYPE:
                        tf_spec = cast(TaskFile, c_obj.spec)
                        if c_key in from_ram:
                            req_info = from_ram[c_key]
                            task_spec.possible_candidates = [(tf_spec.key, req_info)]
                        else:
                            task_spec.resolved_name = tf_spec.key
                        task_spec.include_info = {
                            "type": "taskfile",
                            "path": tf_spec.defined_in,
                            "key": tf_spec.key,
                        }
                elif loop_found and loop_obj:
                    loop_spec = loop_obj.spec if isinstance(loop_obj, CallObject) else loop_obj
                    if task_spec.executable_type == ExecutableType.ROLE_TYPE:
                        task_spec.include_info = {
                            "type": "role",
                            "path": getattr(loop_spec, "defined_in", ""),
                            "key": getattr(loop_spec, "key", ""),
                        }
                    elif task_spec.executable_type == ExecutableType.TASKFILE_TYPE:
                        task_spec.include_info = {
                            "type": "taskfile",
                            "path": getattr(loop_spec, "defined_in", ""),
                            "key": getattr(loop_spec, "key", ""),
                        }
            elif isinstance(call_obj, PlayCall):
                playcall = call_obj
                if len(child_objects.items) > 0 and "roles_info" in handover:
                    first_child = child_objects.items[0]
                    if isinstance(first_child, RoleCall):
                        role_spec = cast(Role, first_child.spec)
                        play_spec = playcall.spec
                        if isinstance(play_spec, Play):
                            roles_info_val = handover.get("roles_info")
                            roles_info_map = roles_info_val if isinstance(roles_info_val, dict) else {}
                            for rip in play_spec.roles:
                                if not isinstance(rip, RoleInPlay):
                                    continue
                                resolved_key = roles_info_map.get(rip.key, "")
                                if resolved_key and resolved_key == role_spec.key:
                                    rip.role_info = {
                                        "fqcn": role_spec.fqcn,
                                        "path": role_spec.defined_in,
                                        "key": role_spec.key,
                                    }
            for child_obj in child_objects.items:
                obj_list.add(child_obj)
        return obj_list

    def _recursive_make_graph(
        self,
        key: str,
        graph: list[list[str | None]],
        _objects: ObjectList,
        caller: CallObject | None = None,
    ) -> list[list[str | None]]:
        """Recursively build graph of [caller_key, my_key] edges.

        Args:
            key: Object key to process.
            graph: Accumulated graph edges.
            _objects: ObjectList to add call objects to.
            caller: Optional caller CallObject.

        Returns:
            Updated graph with new edges.
        """
        current_graph = [g for g in graph]
        # if this key is already in the graph src, no need to trace children
        key_in_graph_src = [g for g in current_graph if g[0] == key]
        if len(key_in_graph_src) > 0:
            return current_graph
        # otherwise, trace children
        obj = self.get_object(key)
        if obj is None:
            return current_graph
        spec_obj = obj.spec if isinstance(obj, CallObject) else obj
        call_obj = call_obj_from_spec(
            spec=spec_obj,
            caller=caller,
        )
        if call_obj is not None:
            caller_key = None if caller is None else caller.key
            my_key = call_obj.key
            current_graph.append([caller_key, my_key])
            _objects.add(call_obj, update_dict=False)
        children_keys, _, _ = self._get_children_keys(obj)
        for c_key in children_keys:
            updated_graph = self._recursive_make_graph(
                c_key,
                current_graph,
                _objects,
                call_obj,
            )
            current_graph = updated_graph
        return current_graph

    # get definition object from root/ext definitions
    def get_object(self, obj_key: str, search_ram: bool = False) -> Object | CallObject | None:
        """Get definition object by key from root/ext definitions or RAM.

        Args:
            obj_key: Object key (e.g. playbook path, role key).
            search_ram: Whether to query RAM for missing objects.

        Returns:
            Object or CallObject if found, else None.

        Raises:
            ValueError: If obj_key type cannot be detected.
        """
        obj_type = detect_type(obj_key)
        if obj_type == "":
            raise ValueError(f'failed to detect object type from key "{obj_key}"')
        type_key = obj_type_dict[obj_type]
        root_definitions = self.root_definitions.get(type_key, ObjectList())
        obj = root_definitions.find_by_key(obj_key)
        if obj is not None:
            return obj
        ext_definitions = self.ext_definitions.get(type_key, ObjectList())
        obj = ext_definitions.find_by_key(obj_key)
        if obj is not None:
            return obj

        if search_ram and self.ram_client:
            matched_obj = self.ram_client.get_object_by_key(obj_key)
            if matched_obj is not None and isinstance(matched_obj, dict):
                obj_raw = matched_obj.get("object", None)
                if obj_raw is not None and isinstance(obj_raw, Object | CallObject):
                    return obj_raw

        return None

    def add_builtin_modules(self) -> None:
        """Add builtin Ansible modules to ext_definitions."""
        builtin_module_dict = load_builtin_modules()
        builtin_modules = list(builtin_module_dict.values())
        obj_list = ObjectList(items=[cast(Object | CallObject, m) for m in builtin_modules])
        self.ext_definitions["modules"].merge(obj_list)

    def _register_handler_taskfiles(self) -> None:
        """Add handler TaskFiles (and their tasks) from roles into root_definitions.

        The ARI engine stores handler files in ``role.handlers`` but excludes
        them from the definitions dict that the tree walker uses.  This method
        copies them into ``root_definitions["taskfiles"]`` (and their child
        tasks into ``root_definitions["tasks"]``) so that ``get_object()`` can
        resolve handler keys during tree traversal.
        """
        taskfiles_list = self.root_definitions.get("taskfiles", ObjectList())
        tasks_list = self.root_definitions.get("tasks", ObjectList())
        if not isinstance(taskfiles_list, ObjectList) or not isinstance(tasks_list, ObjectList):
            return
        roles_list = self.root_definitions.get("roles", ObjectList())
        if not isinstance(roles_list, ObjectList):
            return
        for obj in roles_list.items:
            if not isinstance(obj, Role):
                continue
            for h in obj.handlers:
                if isinstance(h, TaskFile) and h.key:
                    taskfiles_list.add(cast(Object | CallObject, h))
                    for task in h.tasks:
                        if hasattr(task, "key") and task.key:
                            tasks_list.add(cast(Object | CallObject, task))

    def _get_children_keys(
        self,
        obj: Object | CallObject,
        handover_from_upper_node: dict[str, object] | None = None,
    ) -> tuple[list[str], dict[str, str], dict[str, object]]:
        """Get child keys for Playbook, Play, Role, TaskFile, or Task.

        Args:
            obj: Object to get children from.
            handover_from_upper_node: Optional handover dict from parent.

        Returns:
            Tuple of (child keys, from_ram dict, handover dict).
        """
        if handover_from_upper_node is None:
            handover_from_upper_node = {}
        if isinstance(obj, CallObject):
            return self._get_children_keys(obj.spec, handover_from_upper_node)
        children_keys = []
        from_ram = {}
        handover: dict[str, object] = {}
        if isinstance(obj, Playbook):
            children_keys = [p.key if isinstance(p, Play) else str(p) for p in obj.plays]
        elif isinstance(obj, Play):
            if obj.import_playbook != "":
                resolved_playbook_key = resolve_playbook(obj.import_playbook, self.dicts["playbooks"], obj.key)
                if resolved_playbook_key != "":
                    children_keys.append(resolved_playbook_key)
            for rip in obj.roles:
                if not isinstance(rip, RoleInPlay):
                    continue

                if rip.name in self.role_resolve_cache:
                    resolved_role_key = self.role_resolve_cache[rip.name]
                else:
                    resolved_role_key = resolve_role(
                        rip.name,
                        self.dicts["roles"],
                        obj.collection,
                        obj.collections_in_play,
                    )
                    if resolved_role_key != "":
                        self.role_resolve_cache[rip.name] = resolved_role_key

                if resolved_role_key == "" and self.ram_client is not None:
                    if rip.name in self.resolved_role_from_ram:
                        resolved_role_key, req_info = self.resolved_role_from_ram[rip.name]
                        from_ram[resolved_role_key] = req_info
                    else:
                        matched_roles = self.ram_client.search_role(rip.name)
                        if len(matched_roles) > 0:
                            m0 = matched_roles[0]
                            role_obj = _ram_match_object(m0)
                            if role_obj is not None:
                                resolved_role_key = role_obj.key
                                self.ext_definitions["roles"].add(role_obj)
                                for offspr_obj in _ram_match_offspring(m0):
                                    type_str = str(offspr_obj.get("type", "")) + "s"
                                    oo_obj = offspr_obj.get("object")
                                    if isinstance(oo_obj, Object | CallObject):
                                        self.ext_definitions[type_str].add(oo_obj)
                                if role_obj.key not in self.extra_requirement_obj_set:
                                    self.extra_requirements.append(
                                        {
                                            "type": "role",
                                            "name": getattr(role_obj, "fqcn", ""),
                                            "defined_in": _ram_match_defined_in(m0),
                                            "used_in": obj.defined_in,
                                        }
                                    )
                                    self.extra_requirement_obj_set.add(role_obj.key)
                                for offspr_obj in _ram_match_offspring(m0):
                                    oo_obj = offspr_obj.get("object")
                                    if isinstance(oo_obj, Object | CallObject) and getattr(oo_obj, "builtin", False):
                                        continue
                                    if (
                                        isinstance(oo_obj, Object | CallObject)
                                        and oo_obj.key not in self.extra_requirement_obj_set
                                    ):
                                        self.extra_requirements.append(
                                            {
                                                "type": str(offspr_obj.get("type", "")),
                                                "name": str(offspr_obj.get("name", "")),
                                                "defined_in": offspr_obj.get("defined_in", ""),
                                                "used_in": offspr_obj.get("used_in", ""),
                                            }
                                        )
                                        self.extra_requirement_obj_set.add(oo_obj.key)
                                self.resolved_role_from_ram[rip.name] = (
                                    resolved_role_key,
                                    str(_ram_match_defined_in(m0)),
                                )
                                from_ram[resolved_role_key] = str(_ram_match_defined_in(m0))

                if resolved_role_key != "":
                    children_keys.append(resolved_role_key)
                    if "roles_info" not in handover:
                        handover["roles_info"] = {}
                    ri = handover["roles_info"]
                    if isinstance(ri, dict):
                        ri[rip.key] = resolved_role_key
            for item in obj.pre_tasks:
                children_keys.append(item.key if isinstance(item, Task) else str(item))
            for item in obj.tasks:
                children_keys.append(item.key if isinstance(item, Task) else str(item))
            for item in obj.post_tasks:
                children_keys.append(item.key if isinstance(item, Task) else str(item))
            for item in obj.handlers:
                children_keys.append(item.key if isinstance(item, Task) else str(item))
        elif isinstance(obj, Role):
            target_taskfiles: list[str] = ["main.yml", "main.yaml"]
            if isinstance(handover_from_upper_node, dict) and "tasks_from" in handover_from_upper_node:
                tasks_from = handover_from_upper_node.get("tasks_from")
                if tasks_from is not None and isinstance(tasks_from, str):
                    target_taskfiles = [tasks_from]
            target_taskfile_key = [
                tf_str
                for tf in obj.taskfiles
                for tf_str in [tf.key if isinstance(tf, TaskFile) else str(tf)]
                if tf_str.split(key_delimiter)[-1].split("/")[-1] in target_taskfiles and "/handlers/" not in tf_str
            ]
            children_keys.extend(target_taskfile_key)
            for h in obj.handlers:
                h_key = h.key if isinstance(h, TaskFile) else str(h)
                if h_key:
                    children_keys.append(h_key)
        elif isinstance(obj, TaskFile):
            children_keys = [t.key if isinstance(t, Task) else str(t) for t in obj.tasks]
        elif isinstance(obj, Task):
            executable_type = obj.executable_type
            resolved_key = ""
            if obj.executable == "":
                return [], {}, {}
            target_name = obj.executable
            if executable_type == ExecutableType.MODULE_TYPE:
                if target_name in self.module_resolve_cache:
                    resolved_key = self.module_resolve_cache[target_name]
                else:
                    resolved_key = resolve_module(target_name, self.dicts["modules"], self.module_redirects)
                    if resolved_key != "":
                        self.module_resolve_cache[target_name] = resolved_key
                if resolved_key == "" and self.ram_client is not None:
                    if target_name in self.resolved_module_from_ram:
                        resolved_key, req_info = self.resolved_module_from_ram[target_name]
                        from_ram[resolved_key] = req_info
                    else:
                        matched_modules = self.ram_client.search_module(target_name)
                        if len(matched_modules) > 0:
                            m0 = matched_modules[0]
                            mod_obj = _ram_match_object(m0)
                            if mod_obj is not None:
                                resolved_key = mod_obj.key
                                self.ext_definitions["modules"].add(mod_obj)
                                if mod_obj.key not in self.extra_requirement_obj_set and not getattr(
                                    mod_obj, "builtin", False
                                ):
                                    self.extra_requirements.append(
                                        {
                                            "type": "module",
                                            "name": getattr(mod_obj, "fqcn", ""),
                                            "defined_in": _ram_match_defined_in(m0) or "",
                                            "used_in": obj.defined_in,
                                        }
                                    )
                                    self.extra_requirement_obj_set.add(mod_obj.key)
                                self.resolved_module_from_ram[target_name] = (
                                    resolved_key,
                                    str(_ram_match_defined_in(m0)),
                                )
                                from_ram[resolved_key] = str(_ram_match_defined_in(m0))
                if resolved_key == "":
                    if target_name not in self.resolve_failures["module"]:
                        self.resolve_failures["module"][target_name] = 0
                    self.resolve_failures["module"][target_name] += 1
            elif executable_type == ExecutableType.ROLE_TYPE:
                tasks_from = None
                if isinstance(obj.module_options, dict):
                    tasks_from = obj.module_options.get("tasks_from", None)
                if tasks_from:
                    handover["tasks_from"] = tasks_from

                if target_name in self.role_resolve_cache:
                    resolved_key = self.role_resolve_cache[target_name]
                else:
                    resolved_key = resolve_role(
                        target_name,
                        self.dicts["roles"],
                        obj.collection,
                        obj.collections_in_play,
                    )
                    if resolved_key != "":
                        self.role_resolve_cache[target_name] = resolved_key
                if resolved_key == "" and self.ram_client is not None:
                    if target_name in self.resolved_role_from_ram:
                        resolved_key, req_info = self.resolved_role_from_ram[target_name]
                        from_ram[resolved_key] = req_info
                    else:
                        matched_roles = self.ram_client.search_role(target_name)
                        if len(matched_roles) > 0:
                            m0 = matched_roles[0]
                            role_obj = _ram_match_object(m0)
                            if role_obj is not None:
                                resolved_key = role_obj.key
                                self.ext_definitions["roles"].add(role_obj)
                                if role_obj.key not in self.extra_requirement_obj_set:
                                    self.extra_requirements.append(
                                        {
                                            "type": "role",
                                            "name": getattr(role_obj, "fqcn", ""),
                                            "defined_in": _ram_match_defined_in(m0) or "",
                                            "used_in": obj.defined_in,
                                        }
                                    )
                                    self.extra_requirement_obj_set.add(role_obj.key)
                                for offspr_obj in _ram_match_offspring(m0):
                                    oo_obj = offspr_obj.get("object")
                                    if isinstance(oo_obj, Object | CallObject) and getattr(oo_obj, "builtin", False):
                                        continue
                                    if (
                                        isinstance(oo_obj, Object | CallObject)
                                        and oo_obj.key not in self.extra_requirement_obj_set
                                    ):
                                        self.extra_requirements.append(
                                            {
                                                "type": str(offspr_obj.get("type", "")),
                                                "name": str(offspr_obj.get("name", "")),
                                                "defined_in": offspr_obj.get("defined_in", ""),
                                                "used_in": offspr_obj.get("used_in", ""),
                                            }
                                        )
                                        self.extra_requirement_obj_set.add(oo_obj.key)
                                self.resolved_role_from_ram[target_name] = (
                                    resolved_key,
                                    str(_ram_match_defined_in(m0)),
                                )
                                from_ram[resolved_key] = str(_ram_match_defined_in(m0))
                if resolved_key == "":
                    if target_name not in self.resolve_failures["role"]:
                        self.resolve_failures["role"][target_name] = 0
                    self.resolve_failures["role"][target_name] += 1
            elif executable_type == ExecutableType.TASKFILE_TYPE:
                if is_templated(target_name):
                    target_name = render_template(target_name)
                if target_name in self.taskfile_resolve_cache:
                    resolved_key = self.taskfile_resolve_cache[target_name]
                else:
                    resolved_key = resolve_taskfile(
                        target_name,
                        self.dicts["taskfiles"],
                        obj.key,
                    )
                    if resolved_key != "":
                        self.taskfile_resolve_cache[target_name] = resolved_key
                if resolved_key == "" and self.ram_client is not None:
                    if obj.executable in self.resolved_role_from_ram:
                        resolved_key, req_info = self.resolved_role_from_ram[target_name]
                        from_ram[resolved_key] = req_info
                    else:
                        matched_taskfiles = self.ram_client.search_taskfile(
                            target_name, from_path=obj.defined_in, from_key=obj.key
                        )
                        if len(matched_taskfiles) > 0:
                            m0 = matched_taskfiles[0]
                            tf_obj = _ram_match_object(m0)
                            if tf_obj is not None:
                                resolved_key = tf_obj.key
                                self.ext_definitions["taskfiles"].add(tf_obj, update_dict=False)
                                if tf_obj.key not in self.extra_requirement_obj_set:
                                    self.extra_requirements.append(
                                        {
                                            "type": "taskfile",
                                            "name": tf_obj.key,
                                            "defined_in": _ram_match_defined_in(m0) or "",
                                            "used_in": obj.defined_in,
                                        }
                                    )
                                    self.extra_requirement_obj_set.add(tf_obj.key)
                                for offspr_obj in _ram_match_offspring(m0):
                                    oo_obj = offspr_obj.get("object")
                                    if isinstance(oo_obj, Object | CallObject) and getattr(oo_obj, "builtin", False):
                                        continue
                                    if (
                                        isinstance(oo_obj, Object | CallObject)
                                        and oo_obj.key not in self.extra_requirement_obj_set
                                    ):
                                        self.extra_requirements.append(
                                            {
                                                "type": str(offspr_obj.get("type", "")),
                                                "name": str(offspr_obj.get("name", "")),
                                                "defined_in": offspr_obj.get("defined_in", ""),
                                                "used_in": offspr_obj.get("used_in", ""),
                                            }
                                        )
                                        self.extra_requirement_obj_set.add(oo_obj.key)
                                self.resolved_taskfile_from_ram[target_name] = (
                                    resolved_key,
                                    str(_ram_match_defined_in(m0)),
                                )
                                from_ram[resolved_key] = str(_ram_match_defined_in(m0))
                if resolved_key == "":
                    if target_name not in self.resolve_failures["taskfile"]:
                        self.resolve_failures["taskfile"][target_name] = 0
                    self.resolve_failures["taskfile"][target_name] += 1

            if resolved_key != "":
                children_keys.append(resolved_key)
        return children_keys, from_ram, handover

    def node_objects(self, tree: TreeNode) -> ObjectList:
        """Load ObjectList for all keys in tree.

        Args:
            tree: TreeNode to traverse.

        Returns:
            ObjectList of objects for tree keys.
        """
        loaded: dict[str, Object | CallObject] = {}
        obj_list = ObjectList()
        for k in tree.to_keys():
            if k in loaded:
                obj_list.add(loaded[k])
                continue
            obj = self.get_object(k)
            if obj is None:
                logger.warning(f"object not found for the key {k}")
                continue
            obj_list.add(obj)
            loaded[k] = obj
        return obj_list


def is_templated(txt: str) -> bool:
    """Check if string contains Jinja2 template syntax.

    Args:
        txt: String to check.

    Returns:
        True if '{{' appears in txt.
    """
    return "{{" in txt


# TODO: need to use variable manager
def render_template(txt: str, variable_manager: object = None) -> str:
    """Render template string to concrete path (simplified heuristic).

    Args:
        txt: Template string (e.g. with Jinja2).
        variable_manager: Unused; for future variable resolution.

    Returns:
        Resolved path string.
    """
    regex = r'[\'"]([^\'"]+\.ya?ml)[\'"]'
    matched = re.search(regex, txt)
    if matched:
        return matched.group(1)
    if "{{ ansible_facts.os_family }}.yml" in txt:
        return "Debian.yml"
    if "{{ gcloud_install_type }}/main.yml" in txt:
        return "package/main.yml"
    if "{{ ansible_os_family | lower }}.yml" in txt:
        return "debian.yml"
    return txt


def dump_node_objects(obj_list: ObjectList, path: str = "") -> None:
    """Dump ObjectList to stdout or file.

    Args:
        obj_list: ObjectList to dump.
        path: File path; if empty, prints to stdout.
    """
    if path == "":
        lines = obj_list.dump()
        for line in lines:
            obj_dict = json.loads(line)
            print(json.dumps(obj_dict, indent=2))
    else:
        obj_list.dump(fpath=path)


def key_to_file_name(prefix: str, key: str) -> str:
    """Convert key to filename with prefix.

    Args:
        prefix: Filename prefix.
        key: Object key (spaces, slashes, dots replaced).

    Returns:
        Sanitized filename string.
    """
    trans_table = str.maketrans({" ": "___", "/": "---", ".": "_dot_"})
    return prefix + "___" + key.translate(trans_table) + ".json"
