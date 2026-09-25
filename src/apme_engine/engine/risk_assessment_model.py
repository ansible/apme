"""Risk Assessment Model (RAM) client for loading, searching, and caching scan findings."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import PurePath, PureWindowsPath
from typing import cast

import jsonpickle

from .findings import Findings
from .keyutil import get_obj_info_by_key, make_imported_taskfile_key
from .model_loader import load_builtin_modules
from .models import (
    ActionGroupMetadata,
    Collection,
    ExecutableType,
    LoadType,
    Module,
    ModuleMetadata,
    ObjectList,
    Role,
    RoleMetadata,
    Task,
    TaskFile,
    TaskFileMetadata,
    YAMLDict,
    YAMLValue,
)
from .safe_glob import safe_glob
from .utils import (
    escape_url,
    is_test_object,
    lock_file,
    remove_lock_file,
    unlock_file,
)

logger = logging.getLogger(__name__)


def _findings_path_parts(findings_path: str) -> tuple[str, ...]:
    """Split a findings JSON path into components on any OS separator.

    Args:
        findings_path: Absolute or relative path to a findings.json file.

    Returns:
        Path components using the native or Windows separator as appropriate.
    """
    if os.sep == "\\" or "\\" in findings_path:
        return PureWindowsPath(findings_path).parts
    return PurePath(findings_path).parts


def _safe_str(v: YAMLValue, default: str = "") -> str:
    """Convert value to string, or return default if None.

    Args:
        v: Value to convert.
        default: Default string when v is None.

    Returns:
        str(v) or default.
    """
    return str(v) if v is not None else default


def _safe_dict(v: YAMLValue) -> YAMLDict:
    """Return v as dict, or empty dict if not a dict.

    Args:
        v: Value to coerce.

    Returns:
        v if dict, else {}.
    """
    return v if isinstance(v, dict) else {}


def _safe_list(v: YAMLValue) -> list[YAMLValue]:
    """Return v as list, or empty list if not a list.

    Args:
        v: Value to coerce.

    Returns:
        list(v) if list, else [].
    """
    return list(v) if isinstance(v, list) else []


def _get_modules_list(defs: YAMLDict) -> list[object]:
    """Get modules from definitions - can be ObjectList or list.

    Args:
        defs: Definitions dict with "modules" key.

    Returns:
        List of module objects.
    """
    raw = defs.get("modules", [])
    if isinstance(raw, ObjectList):
        return raw.items
    return list(raw) if isinstance(raw, list) else []


def _get_roles_list(defs: YAMLDict) -> list[object]:
    """Get roles from definitions - can be ObjectList or list.

    Args:
        defs: Definitions dict with "roles" key.

    Returns:
        List of role objects.
    """
    raw = defs.get("roles", [])
    if isinstance(raw, ObjectList):
        return raw.items
    return list(raw) if isinstance(raw, list) else []


def _get_taskfiles_list(defs: YAMLDict) -> list[object]:
    """Get taskfiles from definitions - can be ObjectList or list.

    Args:
        defs: Definitions dict with "taskfiles" key.

    Returns:
        List of taskfile objects.
    """
    raw = defs.get("taskfiles", [])
    if isinstance(raw, ObjectList):
        return raw.items
    return list(raw) if isinstance(raw, list) else []


def _get_tasks_list(defs: YAMLDict) -> list[object]:
    """Get tasks from definitions - can be ObjectList or list.

    Args:
        defs: Definitions dict with "tasks" key.

    Returns:
        List of task objects.
    """
    raw = defs.get("tasks", [])
    if isinstance(raw, ObjectList):
        return raw.items
    return list(raw) if isinstance(raw, list) else []


def _collect_offspring_objects(search_results: list[YAMLDict], offspring_objects: list[YAMLDict]) -> None:
    """Append unique offspring objects from search_results[0] to offspring_objects.

    Args:
        search_results: List of search result dicts; uses first element.
        offspring_objects: List to append unique offspring objects to.
    """
    if not search_results:
        return
    first = search_results[0]
    if not isinstance(first, dict):
        return
    offspr_objs = _safe_list(first.get("offspring_objects", []))
    seen_keys: set[str] = set()
    for offspr_obj in offspr_objs:
        if not isinstance(offspr_obj, dict):
            continue
        obj_instance = offspr_obj.get("object", None)
        if obj_instance is None or not hasattr(obj_instance, "key"):
            continue
        if obj_instance.key not in seen_keys:
            offspring_objects.append(offspr_obj)
            seen_keys.add(obj_instance.key)


module_index_name = "module_index.json"
role_index_name = "role_index.json"
taskfile_index_name = "taskfile_index.json"
action_group_index_name = "action_group_index.json"


@dataclass
class RAMClient:
    """Risk Assessment Model client for loading and searching findings, modules, roles, taskfiles.

    Attributes:
        root_dir: Root directory for RAM data files.
        findings_cache: Cached findings by key.
        module_search_cache: Cached module search results.
        role_search_cache: Cached role search results.
        taskfile_search_cache: Cached taskfile search results.
        task_search_cache: Cached task search results.
        builtin_modules_cache: Cached built-in module metadata.
        module_index: Index mapping module names to RAM paths.
        role_index: Index mapping role names to RAM paths.
        taskfile_index: Index mapping taskfile names to RAM paths.
        action_group_index: Index mapping action group names to modules.
        max_cache_size: Maximum number of entries in search caches.

    """

    root_dir: str = ""

    findings_cache: YAMLDict = field(default_factory=dict)
    _findings_json_list_cache: list[str] = field(default_factory=list)
    _findings_search_cache: YAMLDict = field(default_factory=dict)

    module_search_cache: YAMLDict = field(default_factory=dict)
    role_search_cache: YAMLDict = field(default_factory=dict)
    taskfile_search_cache: YAMLDict = field(default_factory=dict)
    task_search_cache: YAMLDict = field(default_factory=dict)

    builtin_modules_cache: YAMLDict = field(default_factory=dict)

    module_index: YAMLDict = field(default_factory=dict)
    role_index: YAMLDict = field(default_factory=dict)
    taskfile_index: YAMLDict = field(default_factory=dict)

    # used for grouped module_defaults such as `group/aws`
    action_group_index: YAMLDict = field(default_factory=dict)

    max_cache_size: int = 200

    def __post_init__(self) -> None:
        """Load module, role, taskfile, and action group indices from disk if present."""
        module_index_path = os.path.join(self.root_dir, "indices", module_index_name)
        if os.path.exists(module_index_path):
            with open(module_index_path) as file:
                self.module_index = json.load(file)

        role_index_path = os.path.join(self.root_dir, "indices", role_index_name)
        if os.path.exists(role_index_path):
            with open(role_index_path) as file:
                self.role_index = json.load(file)

        taskfile_index_path = os.path.join(self.root_dir, "indices", taskfile_index_name)
        if os.path.exists(taskfile_index_path):
            with open(taskfile_index_path) as file:
                self.taskfile_index = json.load(file)

        action_group_index_path = os.path.join(self.root_dir, "indices", action_group_index_name)
        if os.path.exists(action_group_index_path):
            with open(action_group_index_path) as file:
                self.action_group_index = json.load(file)

    def clear_old_cache(self) -> None:
        """Evict oldest entries from all caches when they exceed max_cache_size."""
        size = self.max_cache_size
        self._remove_old_item(self.findings_cache, size)
        self._remove_old_item(self.module_search_cache, size)
        self._remove_old_item(self.role_search_cache, size)
        self._remove_old_item(self.taskfile_search_cache, size)
        self._remove_old_item(self.task_search_cache, size)
        return

    def _remove_old_item(self, data: YAMLDict, size: int) -> None:
        """Remove oldest entries from data until len <= size.

        Args:
            data: Dict to evict from (modified in place).
            size: Target max size.
        """
        if len(data) <= size:
            return
        num = len(data) - size
        for _ in range(num):
            oldest_key = next(iter(data))
            data.pop(oldest_key)
        return

    def register(self, findings: Findings) -> None:
        """Save findings to disk and evict old cache entries.

        Args:
            findings: Findings to register.
        """
        metadata = findings.metadata

        type_str = _safe_str(metadata.get("type", ""))
        name_str = _safe_str(metadata.get("name", ""))
        version_str = _safe_str(metadata.get("version", ""))
        hash_str = _safe_str(metadata.get("hash", ""))

        out_dir = self.make_findings_dir_path(type_str, name_str, version_str, hash_str)
        self.save_findings(findings, out_dir)

        self.clear_old_cache()

    def register_indices_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Register module, role, taskfile, and action group indices from findings.

        Args:
            findings: Findings containing definitions to index.
            include_test_contents: If True, skip test content when indexing.
        """
        self.register_module_index_to_ram(findings=findings, include_test_contents=include_test_contents)
        self.register_role_index_to_ram(findings=findings, include_test_contents=include_test_contents)
        self.register_taskfile_index_to_ram(findings=findings, include_test_contents=include_test_contents)
        self.register_action_group_index_to_ram(findings=findings)

    def register_module_index_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Update module index with modules and plugin routing from findings.

        Args:
            findings: Findings containing modules and collections.
            include_test_contents: If True, skip modules in test paths.
        """
        new_data_found = False
        modules = self.load_module_index()
        definitions = _safe_dict(findings.root_definitions.get("definitions", {}))
        module_list = _safe_list(definitions.get("modules", []))
        for module in module_list:
            if not isinstance(module, Module):
                continue
            if include_test_contents and is_test_object(module.defined_in):
                continue
            m_meta = ModuleMetadata.from_module(module, findings.metadata)
            raw_current = modules.get(module.name, [])
            current = cast(list[YAMLValue], _safe_list(raw_current))
            exists = False
            for m_dict in current:
                m = None
                if isinstance(m_dict, dict):
                    m = ModuleMetadata.from_dict(m_dict)
                elif isinstance(m_dict, ModuleMetadata):
                    m = m_dict
                if not m:
                    continue
                if m == m_meta:
                    exists = True
                    break
            if not exists:
                current.append(m_meta)
                new_data_found = True
            modules.update({module.name: current})
        collection_list = _safe_list(definitions.get("collections", []))
        for collection in collection_list:
            if not isinstance(collection, Collection):
                continue
            if collection.meta_runtime and isinstance(collection.meta_runtime, dict):
                plugin_routing = _safe_dict(collection.meta_runtime.get("plugin_routing", {}))
                modules_routing = _safe_dict(plugin_routing.get("modules", {}))
                for short_name, routing in modules_routing.items():
                    redirect_to = _safe_str(_safe_dict(routing).get("redirect", ""))
                    if not redirect_to:
                        continue
                    m_meta = ModuleMetadata.from_routing(redirect_to, findings.metadata)
                    raw_current = modules.get(short_name, [])
                    current = cast(list[YAMLValue], _safe_list(raw_current))
                    exists = False
                    for m_dict in current:
                        m = None
                        if isinstance(m_dict, dict):
                            m = ModuleMetadata.from_dict(m_dict)
                        elif isinstance(m_dict, ModuleMetadata):
                            m = m_dict
                        if not m:
                            continue
                        if m == m_meta:
                            exists = True
                            break
                    if not exists:
                        current.append(m_meta)
                        new_data_found = True
                    modules.update({short_name: current})
        if new_data_found:
            self.save_module_index(modules)
        return

    def register_role_index_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Update role index with roles from findings.

        Args:
            findings: Findings containing roles.
            include_test_contents: If True, skip roles in test paths.
        """
        new_data_found = False
        roles = self.load_role_index()
        definitions = _safe_dict(findings.root_definitions.get("definitions", {}))
        role_list = _safe_list(definitions.get("roles", []))
        for role in role_list:
            if not isinstance(role, Role):
                continue
            if include_test_contents and is_test_object(role.defined_in):
                continue
            r_meta = RoleMetadata.from_role(role, findings.metadata)
            raw_current = roles.get(r_meta.fqcn, [])
            current = cast(list[YAMLValue], _safe_list(raw_current))
            exists = False
            for r_dict in current:
                r = None
                if isinstance(r_dict, dict):
                    r = RoleMetadata.from_dict(r_dict)
                elif isinstance(r_dict, RoleMetadata):
                    r = r_dict
                if not r:
                    continue
                if r == r_meta:
                    exists = True
                    break
            if not exists:
                current.append(r_meta)
                new_data_found = True
            roles.update({role.fqcn: current})
        if new_data_found:
            self.save_role_index(roles)
        return

    def register_taskfile_index_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Update taskfile index with taskfiles from findings.

        Args:
            findings: Findings containing taskfiles.
            include_test_contents: If True, skip taskfiles in test paths.
        """
        new_data_found = False
        taskfiles = self.load_taskfile_index()
        definitions = _safe_dict(findings.root_definitions.get("definitions", {}))
        taskfile_list = _safe_list(definitions.get("taskfiles", []))
        for taskfile in taskfile_list:
            if not isinstance(taskfile, TaskFile):
                continue
            if include_test_contents and is_test_object(taskfile.defined_in):
                continue
            tf_meta = TaskFileMetadata.from_taskfile(taskfile, findings.metadata)
            raw_current = taskfiles.get(tf_meta.key, [])
            current = cast(list[YAMLValue], _safe_list(raw_current))
            exists = False
            for tf_dict in current:
                tf = None
                if isinstance(tf_dict, dict):
                    tf = TaskFileMetadata.from_dict(tf_dict)
                elif isinstance(tf_dict, TaskFileMetadata):
                    tf = tf_dict
                if not tf:
                    continue
                if tf == tf_meta:
                    exists = True
                    break
            if not exists:
                current.append(tf_meta)
                new_data_found = True
            taskfiles.update({taskfile.key: current})
        if new_data_found:
            self.save_taskfile_index(taskfiles)
        return

    def register_action_group_index_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Update action group index with action groups from collection meta_runtime.

        Args:
            findings: Findings containing collections with action_groups.
            include_test_contents: Unused; kept for API consistency.
        """
        new_data_found = False
        action_groups = self.load_action_group_index()
        definitions = _safe_dict(findings.root_definitions.get("definitions", {}))
        collection_list = _safe_list(definitions.get("collections", []))

        for collection in collection_list:
            if not isinstance(collection, Collection):
                continue
            if collection.meta_runtime and isinstance(collection.meta_runtime, dict):
                action_groups_data = _safe_dict(collection.meta_runtime.get("action_groups", {}))
                for group_name, group_modules in action_groups_data.items():
                    short_group_name = f"group/{group_name}"
                    fq_group_name = f"group/{collection.name}.{group_name}"

                    agm1 = ActionGroupMetadata.from_action_group(short_group_name, group_modules, findings.metadata)
                    raw_current1 = action_groups.get(short_group_name, [])
                    current1 = cast(list[YAMLValue], _safe_list(raw_current1))
                    exists = False
                    for ag_dict in current1:
                        ag = None
                        if isinstance(ag_dict, dict):
                            ag = ActionGroupMetadata.from_dict(ag_dict)
                        elif isinstance(ag_dict, ActionGroupMetadata):
                            ag = ag_dict
                        if not ag:
                            continue
                        if ag == agm1:
                            exists = True
                            break
                    if not exists:
                        current1.append(agm1)
                        new_data_found = True
                    action_groups.update({short_group_name: current1})

                    agm2 = ActionGroupMetadata.from_action_group(fq_group_name, group_modules, findings.metadata)
                    raw_current2 = action_groups.get(fq_group_name, [])
                    current2 = cast(list[YAMLValue], _safe_list(raw_current2))
                    exists = False
                    for ag_dict in current2:
                        ag = None
                        if isinstance(ag_dict, dict):
                            ag = ActionGroupMetadata.from_dict(ag_dict)
                        elif isinstance(ag_dict, ActionGroupMetadata):
                            ag = ag_dict
                        if not ag:
                            continue
                        if ag == agm2:
                            exists = True
                            break
                    if not exists:
                        current2.append(agm2)
                        new_data_found = True
                    action_groups.update({fq_group_name: current2})
        if new_data_found:
            self.save_action_group_index(action_groups)
        return

    def make_findings_dir_path(self, type: str, name: str, version: str, hash: str) -> str:
        """Build the directory path where findings for a target are stored.

        Args:
            type: Load type (collection, role, playbook, etc.).
            name: Target name.
            version: Target version.
            hash: Target content hash.

        Returns:
            Path like root_dir/{type}s/findings/{name}/{version}/{hash}.
        """
        type_root = type + "s"
        dir_name = name
        if type in [LoadType.PROJECT, LoadType.PLAYBOOK, LoadType.TASKFILE]:
            dir_name = escape_url(name)
        ver_str = version if version != "" else "unknown"
        hash_str = hash if hash != "" else "unknown"
        out_dir = os.path.join(self.root_dir, type_root, "findings", dir_name, ver_str, hash_str)
        return out_dir

    def load_metadata_from_findings(
        self, type: str, name: str, version: str, hash: str = "*"
    ) -> tuple[bool, YAMLDict | None, list[YAMLDict] | None]:
        """Load metadata and dependencies from findings for a target.

        Args:
            type: Load type (collection, role, etc.).
            name: Target name.
            version: Target version.
            hash: Target hash; "*" to match any.

        Returns:
            Tuple of (loaded, metadata, dependencies). metadata/dependencies are None if not found.
        """
        findings = self._search_findings(name, version, type)
        if not findings:
            return False, None, None
        if not isinstance(findings, Findings):
            return False, None, None
        return True, findings.metadata, cast(list[YAMLDict], findings.dependencies)

    def load_definitions_from_findings(
        self, type: str, name: str, version: str, hash: str, allow_unresolved: bool = False
    ) -> tuple[bool, YAMLDict, YAMLDict]:
        """Load definitions and mappings from findings for a target.

        Args:
            type: Load type (collection, role, etc.).
            name: Target name.
            version: Target version.
            hash: Target content hash.
            allow_unresolved: If True, allow findings with extra_requirements.

        Returns:
            Tuple of (loaded, definitions dict, mappings dict).
        """
        findings_dir = self.make_findings_dir_path(type, name, version, hash)
        findings_path = os.path.join(findings_dir, "findings.json")
        loaded = False
        definitions = {}
        mappings = {}
        if os.path.exists(findings_path):
            findings = cast(Findings | None, Findings.load(fpath=findings_path))
            # use RAM only if no unresolved dependency
            # (RAM should be fully-resolved specs as much as possible)
            if findings and (len(findings.extra_requirements) == 0 or allow_unresolved):
                definitions = _safe_dict(findings.root_definitions.get("definitions", {}))
                mappings = _safe_dict(findings.root_definitions.get("mappings", {}))
                if mappings:
                    loaded = True
        return loaded, definitions, mappings

    def search_builtin_module(self, name: str, used_in: str = "") -> list[YAMLDict]:
        """Search for a builtin Ansible module by name.

        Args:
            name: Module name (short or FQCN).
            used_in: Path where module is used (for context).

        Returns:
            List of match dicts with type, name, object, defined_in, used_in.
        """
        builtin_modules: dict[str, Module]
        if self.builtin_modules_cache:
            builtin_modules = cast(dict[str, Module], self.builtin_modules_cache)
        else:
            builtin_modules = load_builtin_modules()
            self.builtin_modules_cache = cast(YAMLDict, builtin_modules)
        short_name = name
        if "ansible.builtin." in name:
            short_name = name.split(".")[-1]
        matched_modules: list[YAMLDict] = []
        if short_name in builtin_modules:
            m = builtin_modules[short_name]
            matched_modules.append(
                cast(
                    YAMLDict,
                    {
                        "type": "module",
                        "name": m.fqcn,
                        "object": m,
                        "defined_in": {
                            "type": "collection",
                            "name": m.collection,
                            "version": "unknown",
                            "hash": "unknown",
                        },
                        "used_in": used_in,
                    },
                )
            )
        return matched_modules

    def load_from_indice(self, short_name: str, meta: YAMLDict, used_in: str = "") -> YAMLDict:
        """Build a module wrapper dict from index metadata.

        Args:
            short_name: Short module name.
            meta: Index metadata dict (type, name, fqcn, version, hash).
            used_in: Path where module is used.

        Returns:
            Dict with type, name, object (Module), defined_in, used_in.
        """
        _type = _safe_str(meta.get("type", ""))
        _name = _safe_str(meta.get("name", ""))
        collection = ""
        role = ""
        if _type == "collection":
            collection = _name
        elif _type == "role":
            role = _name
        _version = _safe_str(meta.get("version", ""))
        _hash = _safe_str(meta.get("hash", ""))
        m = Module(
            name=short_name,
            fqcn=_safe_str(meta.get("fqcn", "")),
            collection=collection,
            role=role,
        )
        m_wrapper = {
            "type": "module",
            "name": m.fqcn,
            "object": m,
            "defined_in": {
                "type": m.type,
                "name": _name,
                "version": _version,
                "hash": _hash,
            },
            "used_in": used_in,
        }
        return cast(YAMLDict, m_wrapper)

    def search_module(
        self,
        name: str,
        exact_match: bool = False,
        max_match: int = -1,
        collection_name: str = "",
        collection_version: str = "",
        used_in: str = "",
    ) -> list[YAMLDict]:
        """Search for modules by name in builtin or RAM indices.

        Args:
            name: Module name (short or FQCN).
            exact_match: If True, require exact FQCN match.
            max_match: Max results to return; -1 for unlimited.
            collection_name: Filter by collection.
            collection_version: Filter by collection version.
            used_in: Path where module is used.

        Returns:
            List of match dicts with type, name, object, defined_in, used_in.
        """
        if max_match == 0:
            return []
        args_str = json.dumps([name, exact_match, max_match, collection_name, collection_version])
        if args_str in self.module_search_cache:
            return cast(list[YAMLDict], self.module_search_cache[args_str])

        # check if the module is builtin
        matched_builtin_modules = self.search_builtin_module(name, used_in)
        if len(matched_builtin_modules) > 0:
            self.module_search_cache[args_str] = cast(YAMLValue, matched_builtin_modules)
            return matched_builtin_modules

        short_name = name
        search_name = name
        if "." in name:
            short_name = name.split(".")[-1]

        from_indices = False
        found_index: YAMLDict | None = None
        index_list = _safe_list(self.module_index.get(short_name, []))
        if short_name in self.module_index and index_list:
            from_indices = True
            # look for the module index with FQCN (only when `name` is FQCN)
            if "." in name:
                for possible_index in index_list:
                    if not isinstance(possible_index, dict):
                        continue
                    # use the first one normally
                    if not found_index and _safe_str(possible_index.get("fqcn", "")) == name:
                        found_index = possible_index

                    # but if a non-deprecated one is found, use it
                    if _safe_str(possible_index.get("fqcn", "")) == name and not possible_index.get("deprecated"):
                        found_index = possible_index

            # if any candidates don't match with FQCN, use the first index
            if not found_index:
                non_deprecated_cands = [
                    idx for idx in index_list if isinstance(idx, dict) and not idx.get("deprecated")
                ]
                first_idx = non_deprecated_cands[0] if non_deprecated_cands else index_list[0]
                found_index = first_idx if isinstance(first_idx, dict) else None

        modules_json_list: list[str] = []
        if from_indices and found_index is not None:
            _type = _safe_str(found_index.get("type", ""))
            _name = _safe_str(found_index.get("name", ""))
            _version = _safe_str(found_index.get("version", ""))
            _hash = _safe_str(found_index.get("hash", ""))
            findings_path = os.path.join(
                self.root_dir, _type + "s", "findings", _name, _version, _hash, "findings.json"
            )
            if os.path.exists(findings_path):
                modules_json_list.append(findings_path)
            search_name = _safe_str(found_index.get("fqcn", ""))
        else:
            # Do not search a module from all findings
            # when it is not found in the module index.
            # Instead, just return nothing in the case.
            pass
        matched_modules = []
        search_end = False
        for findings_json in modules_json_list:
            if findings_json in self.findings_cache:
                definitions = _safe_dict(self.findings_cache[findings_json])
            else:
                f = cast(Findings | None, Findings.load(fpath=findings_json))
                if not isinstance(f, Findings):
                    continue
                definitions = _safe_dict(f.root_definitions.get("definitions", {}))
                self.findings_cache[findings_json] = definitions
            modules = _get_modules_list(definitions)
            for m in modules:
                if not isinstance(m, Module):
                    continue
                matched = False
                if exact_match:
                    if m.fqcn == search_name:
                        matched = True
                else:
                    if m.fqcn == search_name or m.fqcn == name or m.fqcn.endswith(f".{short_name}"):
                        matched = True
                if matched:
                    parts = _findings_path_parts(findings_json)
                    if len(parts) < 6:
                        logger.debug("Skipping module with short findings path: %r", findings_json)
                        continue

                    matched_modules.append(
                        {
                            "type": "module",
                            "name": m.fqcn,
                            "object": m,
                            "defined_in": {
                                "type": parts[-6][:-1],  # collection or role
                                "name": parts[-4],
                                "version": parts[-3],
                                "hash": parts[-2],
                            },
                            "used_in": used_in,
                        }
                    )
                if max_match > 0 and len(matched_modules) >= max_match:
                    search_end = True
                    break
            if search_end:
                break
        self.module_search_cache[args_str] = cast(list[YAMLValue], matched_modules)
        return cast(list[YAMLDict], matched_modules)

    def search_role(
        self, name: str, exact_match: bool = False, max_match: int = -1, used_in: str = ""
    ) -> list[YAMLDict]:
        """Search for roles by name in RAM indices.

        Args:
            name: Role name or FQCN.
            exact_match: If True, require exact FQCN match.
            max_match: Max results to return; -1 for unlimited.
            used_in: Path where role is used.

        Returns:
            List of match dicts with type, name, object, offspring_objects, defined_in, used_in.
        """
        if max_match == 0:
            return []
        args_str = json.dumps([name, exact_match, max_match])
        if args_str in self.role_search_cache:
            return cast(list[YAMLDict], self.role_search_cache[args_str])

        from_indices = False
        found_index: YAMLDict | None = None
        role_index_list = _safe_list(self.role_index.get(name, []))
        if name in self.role_index and role_index_list:
            from_indices = True
            first = role_index_list[0]
            found_index = first if isinstance(first, dict) else None

        roles_json_list: list[str] = []
        if from_indices and found_index is not None:
            _type = _safe_str(found_index.get("type", ""))
            _name = _safe_str(found_index.get("name", ""))
            _version = _safe_str(found_index.get("version", ""))
            _hash = _safe_str(found_index.get("hash", ""))
            findings_path = os.path.join(
                self.root_dir, _type + "s", "findings", _name, _version, _hash, "findings.json"
            )
            if os.path.exists(findings_path):
                roles_json_list.append(findings_path)
        else:
            # Do not search a role from all findings
            # when it is not found in the role index.
            # Instead, just return nothing in the case.
            pass

        matched_roles = []
        search_end = False
        for findings_json in roles_json_list:
            if findings_json in self.findings_cache:
                definitions = _safe_dict(self.findings_cache[findings_json])
            else:
                f = cast(Findings | None, Findings.load(fpath=findings_json))
                if not isinstance(f, Findings):
                    continue
                definitions = _safe_dict(f.root_definitions.get("definitions", {}))
                self.findings_cache[findings_json] = definitions
            roles = _get_roles_list(definitions)
            for r in roles:
                if not isinstance(r, Role):
                    continue
                matched = False
                if exact_match:
                    if r.fqcn == name:
                        matched = True
                else:
                    if r.fqcn == name or r.fqcn.endswith(f".{name}"):
                        matched = True
                if matched:
                    parts = _findings_path_parts(findings_json)
                    if len(parts) < 5:
                        logger.debug("Skipping role with short findings path: %r", findings_json)
                        continue
                    offspring_objects = []
                    for taskfile_key in r.taskfiles:
                        tf_key = taskfile_key.key if isinstance(taskfile_key, TaskFile) else str(taskfile_key)
                        _tmp_offspring_objects = self.search_taskfile(tf_key, is_key=True)
                        if len(_tmp_offspring_objects) > 0:
                            tf = _tmp_offspring_objects[0]
                            if tf:
                                offspring_objects.append(tf)
                            _collect_offspring_objects(_tmp_offspring_objects, offspring_objects)
                    matched_roles.append(
                        {
                            "type": "role",
                            "name": r.fqcn,
                            "object": r,
                            "offspring_objects": offspring_objects,
                            "defined_in": {
                                "type": parts[-5][:-1],  # collection or role
                                "name": parts[-4],
                                "version": parts[-3],
                                "hash": parts[-2],
                            },
                            "used_in": used_in,
                        }
                    )
                if max_match > 0 and len(matched_roles) >= max_match:
                    search_end = True
                    break
            if search_end:
                break
        self.role_search_cache[args_str] = cast(list[YAMLValue], matched_roles)
        return cast(list[YAMLDict], matched_roles)

    def make_taskfile_key_candidates(self, name: str, from_path: str, from_key: str) -> list[str]:
        """Build candidate taskfile keys for a reference from a given path.

        Args:
            name: Taskfile reference (path or name).
            from_path: Path of the file containing the reference.
            from_key: Key of the parent (role/taskfile).

        Returns:
            List of candidate keys to search in taskfile index.
        """
        key_candidates = []
        taskfile_ref = name
        if from_path:
            base_path = os.path.dirname(from_path)
            taskfile_path = os.path.normpath(os.path.join(base_path, taskfile_ref))
            candidate_key_1 = make_imported_taskfile_key(from_key, taskfile_path)
            key_candidates.append(candidate_key_1)
            if "roles/" in taskfile_ref and "roles/" in base_path:
                root_path = base_path.split("roles/")[0]
                taskfile_path = os.path.normpath(os.path.join(root_path, taskfile_ref))
                candidate_key_2 = make_imported_taskfile_key(from_key, taskfile_path)
                key_candidates.append(candidate_key_2)

        return key_candidates

    def search_taskfile(
        self,
        name: str,
        from_path: str = "",
        from_key: str = "",
        max_match: int = -1,
        is_key: bool = False,
        used_in: str = "",
    ) -> list[YAMLDict]:
        """Search for taskfiles by name or key in RAM indices.

        Args:
            name: Taskfile reference or key.
            from_path: Path of file containing reference (required if not is_key).
            from_key: Parent key for path resolution.
            max_match: Max results; -1 for unlimited.
            is_key: If True, name is already a taskfile key.
            used_in: Path where taskfile is used.

        Returns:
            List of match dicts with type, name, object, offspring_objects, defined_in, used_in.
        """
        if max_match == 0:
            return []

        # it name is not an object key, we need `from_path` to create a key to be searched
        if not is_key and not from_path:
            return []

        args_str = json.dumps([name, from_path, from_key, max_match, is_key])
        if args_str in self.taskfile_search_cache:
            return cast(list[YAMLDict], self.taskfile_search_cache[args_str])

        from_indices = False
        found_index: YAMLDict | None = None
        found_key = ""
        taskfile_key_candidates: list[str] = (
            [name] if is_key else self.make_taskfile_key_candidates(name, from_path, from_key)
        )
        for taskfile_key in taskfile_key_candidates:
            tf_index_list = _safe_list(self.taskfile_index.get(taskfile_key, []))
            if taskfile_key in self.taskfile_index and tf_index_list:
                from_indices = True
                first = tf_index_list[0]
                found_index = first if isinstance(first, dict) else None
                found_key = taskfile_key
                break

        taskfiles_json_list: list[str] = []
        content_info: YAMLDict | None = None
        if from_indices and found_index is not None:
            _type = _safe_str(found_index.get("type", ""))
            _name = _safe_str(found_index.get("name", ""))
            _version = _safe_str(found_index.get("version", ""))
            _hash = _safe_str(found_index.get("hash", ""))
            content_info = found_index
            findings_path = os.path.join(
                self.root_dir, _type + "s", "findings", _name, _version, _hash, "findings.json"
            )
            if os.path.exists(findings_path):
                taskfiles_json_list.append(findings_path)
        else:
            # Do not search a role from all findings
            # when it is not found in the role index.
            # Instead, just return nothing in the case.
            pass

        matched_taskfiles = []
        search_end = False
        for findings_json in taskfiles_json_list:
            if findings_json in self.findings_cache:
                definitions = _safe_dict(self.findings_cache[findings_json])
            else:
                f = cast(Findings | None, Findings.load(fpath=findings_json))
                if not isinstance(f, Findings):
                    continue
                definitions = _safe_dict(f.root_definitions.get("definitions", {}))
                self.findings_cache[findings_json] = definitions
            taskfiles = _get_taskfiles_list(definitions)
            for tf in taskfiles:
                if not isinstance(tf, TaskFile):
                    continue
                matched = False
                if tf.key == found_key:
                    matched = True

                # TODO: support taskfile reference with variables
                if matched:
                    parts = _findings_path_parts(findings_json)
                    if len(parts) < 5:
                        logger.debug("Skipping taskfile with short findings path: %r", findings_json)
                        continue
                    offspring_objects = []
                    for task_key in tf.tasks:
                        t_key = task_key.key if isinstance(task_key, Task) else str(task_key)
                        _tmp_offspring_objects = self.search_task(
                            t_key, is_key=True, content_info=content_info, used_in=used_in
                        )
                        if len(_tmp_offspring_objects) > 0:
                            t = _tmp_offspring_objects[0]
                            if t:
                                offspring_objects.append(t)
                            _collect_offspring_objects(_tmp_offspring_objects, offspring_objects)

                    matched_taskfiles.append(
                        {
                            "type": "taskfile",
                            "name": tf.key,
                            "object": tf,
                            "offspring_objects": offspring_objects,
                            "defined_in": {
                                "type": parts[-5][:-1],  # collection or role
                                "name": parts[-4],
                                "version": parts[-3],
                                "hash": parts[-2],
                            },
                            "used_in": used_in,
                        }
                    )
                if max_match > 0 and len(matched_taskfiles) >= max_match:
                    search_end = True
                    break
            if search_end:
                break
        return cast(list[YAMLDict], matched_taskfiles)

    def search_task(
        self,
        name: str,
        exact_match: bool = False,
        max_match: int = -1,
        is_key: bool = False,
        content_info: YAMLDict | None = None,
        used_in: str = "",
    ) -> list[YAMLDict]:
        """Search for tasks by name or key within a specific content (collection/role).

        Args:
            name: Task name or key.
            exact_match: If True, require exact name match.
            max_match: Max results; -1 for unlimited.
            is_key: If True, name is a task key.
            content_info: Dict with type, name, version, hash of the content to search.
            used_in: Path where task is used.

        Returns:
            List of match dicts with type, name, object, offspring_objects, defined_in, used_in.
        """
        if max_match == 0:
            return []
        # search task in RAM must be done for a specific content (collection/role)
        # so give up search here when no content_info is provided
        if not content_info or not isinstance(content_info, dict):
            return []

        args_str = json.dumps([name, exact_match, max_match, is_key, content_info])
        if args_str in self.task_search_cache:
            return cast(list[YAMLDict], self.task_search_cache[args_str])

        tasks_json_list: list[str] = []
        _type = _safe_str(content_info.get("type", ""))
        if _type:
            _type = _type + "s"
        _name = _safe_str(content_info.get("name", ""))
        _version = _safe_str(content_info.get("version", ""))
        _hash = _safe_str(content_info.get("hash", ""))
        findings_path = os.path.join(self.root_dir, _type, "findings", _name, _version, _hash, "findings.json")
        if os.path.exists(findings_path):
            tasks_json_list.append(findings_path)

        matched_tasks = []
        search_end = False
        for findings_json in tasks_json_list:
            if findings_json in self.findings_cache:
                definitions = _safe_dict(self.findings_cache[findings_json])
            else:
                f = cast(Findings | None, Findings.load(fpath=findings_json))
                if not isinstance(f, Findings):
                    continue
                definitions = _safe_dict(f.root_definitions.get("definitions", {}))
                self.findings_cache[findings_json] = definitions
            tasks = _get_tasks_list(definitions)
            for t in tasks:
                if not isinstance(t, Task):
                    continue
                matched = False
                if is_key:
                    if t.key == name:
                        matched = True
                else:
                    if exact_match:
                        if t.name == name:
                            matched = True
                    else:
                        if t.name == name or (t.name and name in t.name):
                            matched = True
                if matched:
                    parts = _findings_path_parts(findings_json)
                    if len(parts) < 5:
                        logger.debug("Skipping task with short findings path: %r", findings_json)
                        continue
                    offspring_objects = []
                    # Unknown executable types yield no offspring (a Task with
                    # a missing/unrecognized type must not crash the search).
                    _tmp_offspring_objects: list[YAMLDict] = []
                    if t.executable_type == ExecutableType.MODULE_TYPE:
                        _tmp_offspring_objects = self.search_module(t.executable, used_in=t.defined_in)
                    elif t.executable_type == ExecutableType.ROLE_TYPE:
                        _tmp_offspring_objects = self.search_role(t.executable, used_in=t.defined_in)
                    elif t.executable_type == ExecutableType.TASKFILE_TYPE:
                        _tmp_offspring_objects = self.search_taskfile(
                            t.executable, from_path=t.defined_in, from_key=t.key, used_in=t.defined_in
                        )
                    else:
                        logger.debug(
                            "Task %r has unrecognized executable_type %r; no offspring collected",
                            t.key,
                            t.executable_type,
                        )
                    if len(_tmp_offspring_objects) > 0:
                        child = _tmp_offspring_objects[0]
                        if child:
                            offspring_objects.append(child)
                        _collect_offspring_objects(_tmp_offspring_objects, offspring_objects)

                    matched_tasks.append(
                        {
                            "type": "task",
                            "name": t.key,
                            "object": t,
                            "offspring_objects": offspring_objects,
                            "defined_in": {
                                "type": parts[-5][:-1],  # collection or role
                                "name": parts[-4],
                                "version": parts[-3],
                                "hash": parts[-2],
                            },
                            "used_in": used_in,
                        }
                    )
                if max_match > 0 and len(matched_tasks) >= max_match:
                    search_end = True
                    break
            if search_end:
                break
        self.task_search_cache[args_str] = cast(list[YAMLValue], matched_tasks)
        return cast(list[YAMLDict], matched_tasks)

    def search_action_group(self, name: str, max_match: int = -1) -> list[YAMLDict]:
        """Search for action groups by name in action_group_index.

        Args:
            name: Action group name (e.g., group/aws).
            max_match: Max results; -1 for unlimited.

        Returns:
            List of action group match dicts.
        """
        if max_match == 0:
            return []

        found_groups = _safe_list(self.action_group_index.get(name, []))
        if max_match > 0 and len(found_groups) > max_match:
            found_groups = found_groups[:max_match]
        return cast(list[YAMLDict], found_groups)

    def get_object_by_key(self, obj_key: str) -> YAMLDict | None:
        """Find an object (module, role, taskfile, task) by its key in RAM.

        Args:
            obj_key: Object key to search for.

        Returns:
            Dict with object and defined_in, or None if not found.
        """
        obj_info = get_obj_info_by_key(obj_key)
        obj_type = str(obj_info.get("type", ""))
        parent_name = str(obj_info.get("parent_name", ""))
        type_str = obj_type + "s"

        search_patterns = os.path.join(
            self.root_dir, "collections", "findings", parent_name, "*", "*", "root", f"{type_str}.json"
        )
        obj_json_list_coll = safe_glob(search_patterns)
        obj_json_list_coll = sort_by_version(obj_json_list_coll)
        search_patterns = os.path.join(
            self.root_dir, "roles", "findings", parent_name, "*", "*", "root", f"{type_str}.json"
        )
        obj_json_list_role = safe_glob(search_patterns)
        obj_json_list_role = sort_by_version(obj_json_list_role)
        obj_json_list = obj_json_list_coll + obj_json_list_role

        matched_obj = None
        for obj_json in obj_json_list:
            objs = ObjectList.from_json(fpath=obj_json)
            obj = objs.find_by_key(obj_key)
            if obj is not None:
                parts = _findings_path_parts(obj_json)
                if len(parts) < 6:
                    logger.debug("Skipping object with short JSON path: %r", obj_json)
                    continue
                matched_obj = {
                    "object": obj,
                    "defined_in": {
                        "type": parts[-6][:-1],  # collection or role
                        "name": parts[-5],
                        "version": parts[-4],
                        "hash": parts[-3],
                    },
                }
        return cast(YAMLDict | None, matched_obj)

    def _init_findings_json_list_cache(self) -> None:
        """Populate _findings_json_list_cache with all findings.json paths (collections + roles)."""
        search_patterns = os.path.join(self.root_dir, "collections", "findings", "*", "*", "*", "findings.json")
        findings_json_list_coll = safe_glob(search_patterns)
        findings_json_list_coll = sort_by_version(findings_json_list_coll)
        search_patterns = os.path.join(self.root_dir, "roles", "findings", "*", "*", "*", "findings.json")
        findings_json_list_role = safe_glob(search_patterns)
        findings_json_list_role = sort_by_version(findings_json_list_role)
        self._findings_json_list_cache = findings_json_list_coll + findings_json_list_role

    def _search_findings(
        self,
        target_name: str,
        target_version: str,
        target_type: str | None = None,
    ) -> Findings | None:
        """Search for findings by name, version, and optional type.

        Args:
            target_name: Target name to match.
            target_version: Target version; "*" matches any.
            target_type: Optional type filter (collection, role, etc.).

        Returns:
            Most recent matching Findings, or None.

        Raises:
            ValueError: If target_name is empty.
        """
        if not self._findings_json_list_cache:
            self._init_findings_json_list_cache()
        args_str = json.dumps([target_name, target_version, target_type])
        if args_str in self._findings_search_cache:
            return cast(Findings | None, self._findings_search_cache[args_str])

        if not target_name:
            raise ValueError("target name must be specified for searching RAM data")
        if not target_version:
            target_version = "*"
        found_path_list = []
        for findings_path in self._findings_json_list_cache:
            parts = _findings_path_parts(findings_path)
            if len(parts) < 5:
                logger.debug("Skipping findings with short path: %r", findings_path)
                continue
            _type = parts[-5][:-1]
            _name = parts[-4]
            _version = parts[-3]
            if _name != target_name:
                continue
            if target_version and target_version != "*" and _version != target_version:
                continue
            if target_type and target_type != "*" and _type != target_type:
                continue
            found_path_list.append(findings_path)

        latest_findings_path = ""
        if len(found_path_list) == 1:
            latest_findings_path = found_path_list[0]
        elif len(found_path_list) > 1:
            latest_findings_path = found_path_list[0]
            mtime = os.path.getmtime(latest_findings_path)
            for fpath in found_path_list:
                tmp_mtime = os.path.getmtime(fpath)
                if tmp_mtime > mtime:
                    latest_findings_path = fpath
                    mtime = tmp_mtime
        findings: Findings | None = None
        if os.path.exists(latest_findings_path):
            findings = self._load_findings(latest_findings_path)

        self._findings_search_cache[args_str] = cast(YAMLValue, findings)
        return findings

    def _load_findings(self, path: str) -> Findings | None:
        """Load Findings from a path (file or directory containing findings.json).

        Args:
            path: Path to findings.json or its directory.

        Returns:
            Loaded Findings, or None if load fails.
        """
        basename = os.path.basename(path)
        dir_path = path
        if basename == "findings.json":
            dir_path = os.path.dirname(path)
        return cast(Findings | None, Findings.load(fpath=os.path.join(dir_path, "findings.json")))

    def save_findings(self, findings: Findings, out_dir: str) -> None:
        """Save findings to findings.json in out_dir.

        Args:
            findings: Findings to save.
            out_dir: Output directory.

        Raises:
            ValueError: If out_dir is empty.
        """
        if out_dir == "":
            raise ValueError("output dir must be a non-empty value")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        findings.dump(fpath=os.path.join(out_dir, "findings.json"))

    def save_index(self, index_objects: YAMLValue, filename: str) -> None:
        """Save index to JSON file in indices dir with file locking.

        Args:
            index_objects: Index data to serialize.
            filename: Filename (e.g., module_index.json).
        """
        out_dir = os.path.join(self.root_dir, "indices")
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        index_objects_str = jsonpickle.encode(index_objects, make_refs=False, unpicklable=False)
        fpath = os.path.join(out_dir, filename)
        lock = lock_file(fpath)
        try:
            with open(fpath, "w") as file:
                file.write(index_objects_str)
        finally:
            unlock_file(lock)
            remove_lock_file(lock)

    def load_index(self, filename: str = "") -> YAMLDict:
        """Load index from JSON file in indices dir.

        Args:
            filename: Filename (e.g., module_index.json).

        Returns:
            Loaded index dict, or empty dict if file does not exist.
        """
        path = os.path.join(self.root_dir, "indices", filename)
        index_objects = {}
        if os.path.exists(path):
            with open(path) as file:
                index_objects = json.load(file)
        return index_objects

    def save_module_index(self, modules: YAMLDict) -> None:
        """Save module index to module_index.json.

        Args:
            modules: Module index dict to save.
        """
        return self.save_index(modules, module_index_name)

    def load_module_index(self) -> YAMLDict:
        """Load module index from module_index.json.

        Returns:
            Loaded module index dict.
        """
        return self.load_index(module_index_name)

    def save_role_index(self, roles: YAMLDict) -> None:
        """Save role index to role_index.json.

        Args:
            roles: Role index dict to save.
        """
        return self.save_index(roles, role_index_name)

    def load_role_index(self) -> YAMLDict:
        """Load role index from role_index.json.

        Returns:
            Loaded role index dict.
        """
        return self.load_index(role_index_name)

    def save_taskfile_index(self, taskfiles: YAMLDict) -> None:
        """Save taskfile index to taskfile_index.json.

        Args:
            taskfiles: Taskfile index dict to save.
        """
        return self.save_index(taskfiles, taskfile_index_name)

    def load_taskfile_index(self) -> YAMLDict:
        """Load taskfile index from taskfile_index.json.

        Returns:
            Loaded taskfile index dict.
        """
        return self.load_index(taskfile_index_name)

    def save_action_group_index(self, action_groups: YAMLDict) -> None:
        """Save action group index to action_group_index.json.

        Args:
            action_groups: Action group index dict to save.
        """
        return self.save_index(action_groups, action_group_index_name)

    def load_action_group_index(self) -> YAMLDict:
        """Load action group index from action_group_index.json.

        Returns:
            Loaded action group index dict.
        """
        return self.load_index(action_group_index_name)

    def save_error(self, error: str, out_dir: str) -> None:
        """Save error message to error.log in out_dir.

        Args:
            error: Error message to save.
            out_dir: Output directory.

        Raises:
            ValueError: If out_dir is empty.
        """
        if out_dir == "":
            raise ValueError("output dir must be a non-empty value")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        with open(os.path.join(out_dir, "error.log"), "w") as file:
            file.write(error)


def _version_to_num(ver: str) -> float:
    """Convert a version string to a comparable numeric value.

    Args:
        ver: Version string (e.g. 1.2.3 or 1.2.3-suffix).

    Returns:
        Float for comparison; 0.0 for 'unknown'.
    """
    if ver == "unknown":
        return 0.0
    ver_num_part = ver.split("-")[0]
    parts = ver_num_part.split(".")
    num: float = 0.0
    if len(parts) >= 1 and parts[0].isnumeric():
        num += float(parts[0])
    if len(parts) >= 2 and parts[1].isnumeric():
        num += float(parts[1]) * (0.001**1)
    if len(parts) >= 3 and parts[2].isnumeric():
        num += float(parts[2]) * (0.001**2)
    return num


def _path_to_reversed_version_num(path: str) -> float:
    """Extract version from path and return negated version number for sort order.

    Args:
        path: Path like .../findings/{name}/{version}/{hash}/...

    Returns:
        Negated version number (higher versions sort first).
    """
    version = path.split("/findings/")[-1].split("/")[1]
    return float(-1 * _version_to_num(version))


def _path_to_collection_name(path: str) -> str:
    """Extract collection/role name from findings path.

    Args:
        path: Path like .../findings/{name}/{version}/{hash}/...

    Returns:
        Name (first path segment after findings/).
    """
    return path.split("/findings/")[-1].split("/")[0]


def sort_by_version(path_list: list[str]) -> list[str]:
    """Sort paths by collection name and version (newest first).

    Args:
        path_list: List of findings paths.

    Returns:
        Sorted list with same collection grouped, newest version first.
    """
    return sorted(path_list, key=lambda x: (_path_to_collection_name(x), _path_to_reversed_version_num(x)))
