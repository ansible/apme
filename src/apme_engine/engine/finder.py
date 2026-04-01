"""Find modules, task blocks, playbooks, and roles in Ansible content."""

from __future__ import annotations

import json
import os
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from .models import YAMLDict, YAMLValue
from .yaml_utils import FormattedYAML

try:
    # if `libyaml` is available, use C based loader for performance
    import _yaml  # type: ignore[import-not-found]  # noqa: F401
    from yaml import CSafeLoader as Loader
except Exception:
    from yaml import SafeLoader as Loader  # type: ignore[assignment]
import contextlib

from . import logger
from .awx_utils import could_be_playbook, search_playbooks
from .safe_glob import safe_glob

fqcn_module_name_re = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")
module_name_re = re.compile(r"^[a-z0-9_.]+$")
taskfile_path_in_role_re = r".*roles/[^/]+/(tasks|handlers)/.*\.ya?ml"

module_dir_patterns = [
    "library",
    "plugins/modules",
    "plugins/actions",
]

playbook_taskfile_dir_patterns = ["tasks", "playbooks"]

github_workflows_dir = ".github/workflows"


class Singleton(type):
    """Metaclass that ensures a single instance per class."""

    _instances: dict[type, object] = {}

    def __call__(cls, *args: object, **kwargs: object) -> object:
        """Return the single instance for this class.

        Args:
            *args: Positional arguments forwarded to the constructor.
            **kwargs: Keyword arguments forwarded to the constructor.

        Returns:
            The singleton instance for cls.

        """
        if cls not in Singleton._instances:
            Singleton._instances[cls] = super().__call__(*args, **kwargs)
        return Singleton._instances[cls]


@dataclass(frozen=True)
class TaskKeywordSet(metaclass=Singleton):
    """Singleton set of Ansible task keywords (block, include, etc.).

    Attributes:
        task_keywords: Frozen set of recognized task keyword strings.
    """

    task_keywords: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class BuiltinModuleSet(metaclass=Singleton):
    """Singleton set of Ansible builtin module names.

    Attributes:
        builtin_modules: Set of builtin module name strings.
    """

    builtin_modules: set[str] = field(default_factory=set)


p = Path(__file__).resolve().parent
with open(p / "task_keywords.txt") as f:
    TaskKeywordSet(task_keywords=set(f.read().splitlines()))

with open(p / "builtin-modules.txt") as f:
    BuiltinModuleSet(builtin_modules=set(f.read().splitlines()))


def get_builtin_module_names() -> set[str]:
    """Return the set of known Ansible builtin module names.

    Returns:
        Set of module name strings.

    """
    return BuiltinModuleSet().builtin_modules


def find_module_name(data_block: YAMLDict) -> str:
    """Determine the module name from a task dict (FQCN or short name).

    Args:
        data_block: Single task dict (module key -> args).

    Returns:
        Module name string, or empty if not found.

    """
    keys = [k for k in data_block]
    task_keywords = TaskKeywordSet().task_keywords
    builtin_modules = BuiltinModuleSet().builtin_modules
    for k in keys:
        if type(k) is not str:
            continue
        if k.startswith("ansible.builtin"):
            return k
        if k in builtin_modules:
            return k
        if fqcn_module_name_re.match(k):
            return k
    for k in keys:
        if type(k) is not str:
            continue
        if k in task_keywords:
            continue
        if k.startswith("with_"):
            continue
        if module_name_re.match(k):
            return k
    if "local_action" in keys:
        local_action_value = data_block["local_action"]
        module_name = ""
        if isinstance(local_action_value, str):
            module_name = local_action_value.split(" ")[0]
        elif isinstance(local_action_value, dict):
            module_name = str(local_action_value.get("module", "") or "")
        if module_name:
            return module_name
    return ""


def get_task_blocks(
    fpath: str = "",
    yaml_str: str = "",
    task_dict_list: list[YAMLDict] | None = None,
    jsonpath_prefix: str = "",
) -> tuple[list[tuple[YAMLDict, str]] | None, str | None]:
    """Load YAML and extract flattened task blocks with jsonpath.

    Args:
        fpath: Path to a playbook/tasks file (optional).
        yaml_str: Raw YAML string (optional).
        task_dict_list: Pre-parsed task list (optional).
        jsonpath_prefix: Prefix for jsonpath in results.

    Returns:
        Tuple of (list of (task_dict, jsonpath) or None, yaml_str or None).

    """
    d = None
    yaml_lines = ""
    if yaml_str:
        try:
            d = yaml.load(yaml_str, Loader=Loader)
            yaml_lines = yaml_str
        except Exception as e:
            logger.debug(f"failed to load this yaml string to get task blocks; {e.args[0]}")
            return None, None
    elif fpath:
        if not os.path.exists(fpath):
            return None, None
        with open(fpath) as file:
            try:
                yaml_lines = file.read()
                d = yaml.load(yaml_lines, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load this yaml file to get task blocks; {e.args[0]}")
                return None, None
    elif task_dict_list is not None:
        d = task_dict_list
    else:
        return None, None
    if d is None:
        return None, None
    if not isinstance(d, list):
        return None, None
    tasks = []
    for i, task_dict in enumerate(d):
        jsonpath = f"{jsonpath_prefix}.{i}"
        task_dict_loop = flatten_block_tasks(task_dict=task_dict, jsonpath_prefix=jsonpath)
        tasks.extend(task_dict_loop)
    return tasks, yaml_lines


def flatten_block_tasks(
    task_dict: YAMLDict | None,
    jsonpath_prefix: str = "",
    module_defaults: YAMLDict | None = None,
) -> list[tuple[YAMLDict, str]]:
    """Return task dicts preserving block wrappers intact.

    Block wrappers (dicts with ``block:``, ``rescue:``, or ``always:``
    keys) are emitted as single entries.  ``load_task()`` handles
    recursive child loading.  Non-block dicts are returned as-is.

    Args:
        task_dict: Single task dict (may contain block/rescue/always).
        jsonpath_prefix: Prefix for jsonpath.
        module_defaults: Unused (kept for API compat); block-level
            ``module_defaults`` are now preserved on the wrapper dict
            and handled by ``load_task``.

    Returns:
        List of ``(task_dict, jsonpath)`` tuples.

    """
    if task_dict is None:
        return []
    return [(task_dict, jsonpath_prefix)]


def identify_lines_with_jsonpath(
    fpath: str = "", yaml_str: str = "", jsonpath: str = ""
) -> tuple[str | None, tuple[int, int] | None]:
    """Resolve a jsonpath into the YAML source and return the line range.

    Args:
        fpath: Path to YAML file (optional).
        yaml_str: Raw YAML string (optional).
        jsonpath: Jsonpath to the block (e.g. .0.tasks.1).

    Returns:
        Tuple of (yaml_fragment, (start_line, end_line)) or (None, None).

    """
    if not jsonpath:
        return None, None

    d = None
    yaml_lines = ""
    if yaml_str:
        try:
            d = yaml.load(yaml_str, Loader=Loader)
            yaml_lines = yaml_str
        except Exception as e:
            logger.debug(f"failed to load this yaml string to identify lines; {e.args[0]}")
            return None, None
    elif fpath:
        if not os.path.exists(fpath):
            return None, None
        with open(fpath) as file:
            try:
                yaml_lines = file.read()
                d = yaml.load(yaml_lines, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load this yaml file to identify lines; {e.args[0]}")
                return None, None
    if not d:
        return None, None

    path_parts = jsonpath.strip(".").split(".")
    current_lines = yaml_lines
    current_line_num = 1
    line_num_tuple = None
    for p in path_parts:
        if p == "plays":
            pass
        elif p in ["pre_tasks", "tasks", "post_tasks", "handlers", "block", "rescue", "always"]:
            blocks = find_child_yaml_block(current_lines, key=p, line_num_offset=current_line_num)
            if not blocks:
                logger.debug(f"no blocks found for key '{p}' in jsonpath '{jsonpath}'")
                return None, None
            current_lines, line_num_tuple = blocks[0]
            current_line_num = line_num_tuple[0]
        else:
            try:
                p_num = int(p)
                blocks = find_child_yaml_block(current_lines, line_num_offset=current_line_num)
                if not blocks or p_num < 0 or p_num >= len(blocks):
                    n_blocks = len(blocks) if blocks else 0
                    logger.debug(f"no block at index {p_num} for jsonpath '{jsonpath}' (blocks count: {n_blocks})")
                    return None, None
                current_lines, line_num_tuple = blocks[p_num]
                current_line_num = line_num_tuple[0]
            except ValueError as e:
                logger.debug(f"error occurred while detecting line number: {e}")
                return None, None
    return current_lines, line_num_tuple


def find_child_yaml_block(yaml_str: str, key: str = "", line_num_offset: int = -1) -> list[tuple[str, tuple[int, int]]]:
    """Split YAML by top-level key or list items and return (block_str, (start, end)).

    Args:
        yaml_str: Raw YAML string.
        key: Optional key (e.g. tasks, block) to split on; else split on list items.
        line_num_offset: Line number offset for the returned ranges.

    Returns:
        List of (block_yaml, (start_line, end_line)).

    """
    skip_condition_funcs: list[Callable[[str], bool]] = [
        # for YAML separator
        lambda x: x.strip() == "---",
        # for empty line
        lambda x: x.strip() == "",
        # for comment line
        lambda x: x.strip().startswith("#"),
    ]

    def match_condition_func(x: str) -> bool:
        if key:
            return x.strip().startswith(f"{key}:")
        else:
            return x.strip().startswith("- ")

    def is_yaml_end_separator(x: str) -> bool:
        return x.strip() == "..."

    def get_indent_level(x: str) -> int:
        return len(x) - len(x.lstrip())

    top_level_indent = 100
    for line in yaml_str.splitlines():
        skip = False
        for skip_cond_func in skip_condition_funcs:
            if skip_cond_func(line):
                skip = True
                break
        if skip:
            continue
        if match_condition_func(line):
            current_indent = get_indent_level(line)
            if current_indent < top_level_indent:
                top_level_indent = current_indent
    if top_level_indent == 100:
        return []

    blocks: list[tuple[str, tuple[int, int]]] = []
    line_buffer: list[str] = []
    isolated_line_buffer: list[str] = []
    buffer_begin = -1
    if key:
        for i, line in enumerate(yaml_str.splitlines()):
            line_num = i + 1
            current_indent = get_indent_level(line)
            if current_indent == top_level_indent:
                if line_buffer and not blocks:
                    block_str = "\n".join(line_buffer)
                    begin = buffer_begin
                    end = line_num - 1
                    if line_num_offset > 0:
                        begin += line_num_offset - 1
                        end += line_num_offset - 1
                    line_num_tuple = (begin, end)
                    blocks.append((block_str, line_num_tuple))
                if match_condition_func(line):
                    buffer_begin = line_num + 1
            if buffer_begin > 0 and line_num >= buffer_begin:
                line_buffer.append(line)
        if line_buffer and not blocks:
            block_str = "\n".join(line_buffer)
            begin = buffer_begin
            end = line_num
            if line_num_offset > 0:
                begin += line_num_offset - 1
                end += line_num_offset - 1
            line_num_tuple = (begin, end)
            blocks.append((block_str, line_num_tuple))
    else:
        for i, line in enumerate(yaml_str.splitlines()):
            line_num = i + 1
            current_indent = get_indent_level(line)
            new_block = False
            if current_indent == top_level_indent and match_condition_func(line):
                skip = False
                for skip_cond_func in skip_condition_funcs:
                    if skip_cond_func(line):
                        skip = True
                        break
                if not skip:
                    new_block = True
            if new_block:
                if line_buffer:
                    block_str = ""
                    block_str += "\n".join(line_buffer)
                    begin = buffer_begin
                    end = line_num - 1
                    if line_num_offset > 0:
                        begin += line_num_offset - 1
                        end += line_num_offset - 1
                    line_num_tuple = (begin, end)
                    blocks.append((block_str, line_num_tuple))
                    line_buffer = []
                    isolated_line_buffer = []
                buffer_begin = line_num
                line_buffer.append(line)
            else:
                if buffer_begin < 0:
                    isolated_line_buffer.append(line)
                else:
                    line_buffer.append(line)
        if line_buffer:
            block_str = ""
            block_str += "\n".join(line_buffer)
            begin = buffer_begin
            end = line_num
            if is_yaml_end_separator(line_buffer[-1]):
                end = line_num - 1
            if line_num_offset > 0:
                begin += line_num_offset - 1
                end += line_num_offset - 1
            line_num_tuple = (begin, end)
            blocks.append((block_str, line_num_tuple))
    return blocks


def search_module_files(path: str, module_dir_paths: list[str] | None = None) -> list[str]:
    """Find Python module files (with DOCUMENTATION) under path and optional dirs.

    Args:
        path: Root path to search.
        module_dir_paths: Additional directories to search.

    Returns:
        Sorted list of module file paths.

    """
    if module_dir_paths is None:
        module_dir_paths = []
    file_list = []
    # must copy the input here; otherwise, the added items are kept forever
    search_targets = [p for p in module_dir_paths]
    for module_dir_pattern in module_dir_patterns:
        search_targets.append(os.path.join(path, module_dir_pattern))
    for search_target in search_targets:
        for dirpath, _folders, files in os.walk(search_target):
            for file in files:
                basename, ext = os.path.splitext(file)
                if basename == "__init__":
                    continue
                if ext == ".py" or ext == "":
                    fpath = os.path.join(dirpath, file)

                    # check if "DOCUMENTATION" is found in the file
                    skip = False
                    with open(fpath) as f:
                        body = f.read()
                        if "DOCUMENTATION" not in body:
                            # if not, it is not a module file, so skip it
                            skip = True
                    if skip:
                        continue

                    file_list.append(fpath)
    file_list = sorted(file_list)
    return file_list


def find_module_dirs(role_root_dir: str) -> list[str]:
    """Return existing library/plugins/modules dirs under a role root.

    Args:
        role_root_dir: Path to the role root.

    Returns:
        List of existing module directory paths.

    """
    module_dirs = []
    for module_dir_pattern in module_dir_patterns:
        moddir = os.path.join(role_root_dir, module_dir_pattern)
        if os.path.exists(moddir):
            module_dirs.append(moddir)
    return module_dirs


def search_taskfiles_for_playbooks(path: str, taskfile_dir_paths: list[str] | None = None) -> list[str]:
    """Find YAML files that look like task files (not playbooks) under path.

    Args:
        path: Root path to search.
        taskfile_dir_paths: Additional task/playbooks dirs to search.

    Returns:
        List of candidate taskfile paths.

    """
    # must copy the input here; otherwise, the added items are kept forever
    if taskfile_dir_paths is None:
        taskfile_dir_paths = []
    search_targets = [p for p in taskfile_dir_paths]
    for playbook_taskfile_dir_pattern in playbook_taskfile_dir_patterns:
        search_targets.append(os.path.join(path, playbook_taskfile_dir_pattern))
    candidates = []
    for search_target in search_targets:
        patterns = [search_target + "/**/*.ya?ml"]
        found = safe_glob(patterns, recursive=True)
        for f in found:
            # taskfiles in role will be loaded when the role is loaded, so skip
            if re.match(taskfile_path_in_role_re, f):
                continue
            # if it is a playbook, skip it
            if could_be_playbook(f):
                continue
            d = None
            with open(f) as file:
                try:
                    d = yaml.load(file, Loader=Loader)
                except Exception as e:
                    logger.debug(f"failed to load this yaml file to search task files; {e.args[0]}")
            # if d cannot be loaded as tasks yaml file, skip it
            if d is None or not isinstance(d, list):
                continue
            candidates.append(f)
    return candidates


def search_inventory_files(path: str) -> list[str]:
    """Find group_vars and host_vars files under path.

    Args:
        path: Root path to search.

    Returns:
        List of inventory var file paths.

    """
    inventory_file_patterns = [
        os.path.join(path, "**/group_vars", "*"),
        os.path.join(path, "**/host_vars", "*"),
    ]
    return safe_glob(patterns=inventory_file_patterns, recursive=True)


def find_best_repo_root_path(path: str) -> str:
    """Infer repository root from MANIFEST/galaxy/meta or playbook locations.

    Args:
        path: Directory to start from.

    Returns:
        Path to the inferred repo root.

    Raises:
        ValueError: If no playbook files are found.

    """
    base_path = path

    manifest_json_path = os.path.join(base_path, "MANIFEST.json")
    galaxy_yml_path = os.path.join(base_path, "galaxy.yml")
    meta_main_yml_path = os.path.join(base_path, "meta/main.yml")
    if os.path.exists(manifest_json_path) or os.path.exists(galaxy_yml_path) or os.path.exists(meta_main_yml_path):
        return base_path

    # get all possible playbooks
    playbooks = search_playbooks(path)
    # sort by directory depth to find the most top playbook
    playbooks = sorted(playbooks, key=lambda x: len(x.split(os.sep)))
    # still "repo/xxxxx/sample1.yml" may come before
    # "repo/playbooks/sample2.yml" because the depth are same,
    # so specifically put "playbooks" or "playbook" ones first
    if len(playbooks) > 0:
        most_shallow_depth = len(playbooks[0].split(os.sep))
        playbooks_ordered = []
        rests = []
        for p in playbooks:
            is_shortest = len(p.split(os.sep)) == most_shallow_depth
            is_playbook_dir = "/playbooks/" in p or "/playbook/" in p
            if is_shortest and is_playbook_dir:
                playbooks_ordered.append(p)
            else:
                rests.append(p)
        playbooks_ordered.extend(rests)
        playbooks = playbooks_ordered
    # ignore tests directory
    playbooks = [p for p in playbooks if "/tests/" not in p]
    if len(playbooks) == 0:
        raise ValueError(f"no playbook files found under {path}")
    top_playbook_path = playbooks[0]
    top_playbook_relative_path = top_playbook_path[len(base_path) :]
    root_path = ""
    if "/playbooks/" in top_playbook_relative_path:
        root_path = top_playbook_path.rsplit("/playbooks/", 1)[0]
    elif "/playbook/" in top_playbook_relative_path:
        root_path = top_playbook_path.rsplit("/playbook/", 1)[0]
    else:
        root_path = os.path.dirname(top_playbook_path)

    # if the root_path is a subdirectory of the input path,
    # then try finding more appropriate root dir based on `roles`
    if path != root_path:
        pattern = os.path.join(path, "**", "roles")
        found_roles = safe_glob(pattern, recursive=True)
        if found_roles:
            roles_dir = found_roles[0]
            candidate = os.path.dirname(roles_dir)
            if len(candidate) < len(root_path):
                root_path = candidate

    return str(root_path)


def find_collection_name_of_repo(path: str) -> str:
    """Read galaxy.yml or MANIFEST.json and return namespace.name.

    Args:
        path: Repository root path.

    Returns:
        Collection name (namespace.name) or empty string.

    """
    galaxy_yml_pattern = os.path.join(path, "**/galaxy.yml")
    manifest_json_pattern = os.path.join(path, "**/MANIFEST.json")
    found_metadata_files = safe_glob([galaxy_yml_pattern, manifest_json_pattern], recursive=True)
    found_metadata_files = [fpath for fpath in found_metadata_files if github_workflows_dir not in fpath]

    # skip metadata files found in collections/roles in the repository
    _metadata_files = []
    for mpath in found_metadata_files:
        relative_path = mpath.replace(path, "", 1)
        if "/collections/" in relative_path:
            continue
        if "/roles/" in relative_path:
            continue
        _metadata_files.append(mpath)
    found_metadata_files = _metadata_files

    my_collection_name = ""
    if len(found_metadata_files) > 0:
        metadata_file = found_metadata_files[0]
        my_collection_info = None
        if metadata_file.endswith(".yml"):
            with open(metadata_file) as file:
                try:
                    my_collection_info = yaml.load(file, Loader=Loader)
                except Exception as e:
                    logger.debug(f"failed to load this yaml file to read galaxy.yml; {e.args[0]}")
        elif metadata_file.endswith(".json"):
            with open(metadata_file) as file:
                try:
                    my_collection_info = json.load(file).get("collection_info", {})
                except Exception as e:
                    logger.debug(f"failed to load this json file to read MANIFEST.json; {e.args[0]}")
        if my_collection_info is None:
            return ""
        namespace = str(my_collection_info.get("namespace", ""))
        name = str(my_collection_info.get("name", ""))
        my_collection_name = f"{namespace}.{name}"
    return str(my_collection_name)


def find_all_ymls(root_dir: str) -> list[str]:
    """Glob all .yml/.yaml files under root_dir.

    Args:
        root_dir: Root directory to search.

    Returns:
        List of YAML file paths.

    """
    patterns = [os.path.join(root_dir, "**", "*.ya?ml")]
    return safe_glob(patterns)


def find_all_files(root_dir: str) -> list[str]:
    """Glob all files (non-dirs) under root_dir.

    Args:
        root_dir: Root directory to search.

    Returns:
        List of file paths.

    """
    patterns = [os.path.join(root_dir, "**", "*")]
    return safe_glob(patterns, type=["file"])


def _get_body_data(body: str = "", data: YAMLValue | None = None, fpath: str = "") -> tuple[str, YAMLValue | None, str]:
    """Load body and parsed data from fpath if not provided.

    Args:
        body: Optional raw YAML string.
        data: Optional parsed YAML value.
        fpath: Path to read from if body/data missing.

    Returns:
        Tuple of (body, data, fpath).

    """
    if fpath and not body and not data:
        try:
            with open(fpath) as file:
                body = file.read()
                data = yaml.safe_load(body)
        except Exception:
            pass
    elif body and not data:
        with contextlib.suppress(Exception):
            data = yaml.safe_load(body)
    return body, data, fpath


def could_be_playbook_detail(body: str = "", data: YAMLValue | None = None, fpath: str = "") -> bool:
    """Return True if content looks like a playbook (list of plays with hosts).

    Args:
        body: Optional raw YAML string.
        data: Optional parsed YAML value.
        fpath: Path to read from if body/data missing.

    Returns:
        True if data is a list and first item has hosts or import_playbook.

    """
    body, data, fpath = _get_body_data(body, data, fpath)

    if not body:
        return False

    if not data:
        return False

    if not isinstance(data, list):
        return False

    if len(data) == 0:
        return False

    if not isinstance(data[0], dict):
        return False

    if "hosts" in data[0]:
        return True

    return bool("import_playbook" in data[0] or "ansible.builtin.import_playbook" in data[0])


def could_be_taskfile(body: str = "", data: YAMLValue | None = None, fpath: str = "") -> bool:
    """Return True if content looks like a task file (list of tasks, not import_playbook).

    Args:
        body: Optional raw YAML string.
        data: Optional parsed YAML value.
        fpath: Path to read from if body/data missing.

    Returns:
        True if data looks like a task list.

    """
    body, data, fpath = _get_body_data(body, data, fpath)

    if not body:
        return False

    if not data:
        return False

    if not isinstance(data, list):
        return False

    if not isinstance(data[0], dict):
        return False

    if "name" in data[0]:
        return True

    module_name = find_module_name(data[0])
    if module_name:
        short_module_name = module_name.split(".")[-1] if "." in module_name else module_name
        # if the found module name is import_playbook, the file is a playbook
        return short_module_name != "import_playbook"

    return False


def label_empty_file_by_path(fpath: str) -> str:
    """Label an empty file as taskfile or playbook based on path only.

    Args:
        fpath: Path to the file.

    Returns:
        'taskfile', 'playbook', or empty string.

    """
    taskfile_dir = ["/tasks/", "/handlers/"]
    for t_d in taskfile_dir:
        if t_d in fpath:
            return "taskfile"

    playbook_dir = ["/playbooks/"]
    for p_d in playbook_dir:
        if p_d in fpath:
            return "playbook"

    return ""


def get_role_info_from_path(fpath: str) -> tuple[str, str]:
    """Extract role name and role root path from a file path under roles/ or targets/.

    Args:
        fpath: Path to a file inside a role.

    Returns:
        Tuple of (role_name, role_path).

    """
    patterns = [
        "/roles/",
        "/tests/integration/targets/",
    ]
    targets = [
        "/tasks/",
        "/handlers/",
        "/vars/",
        "/defaults/",
        "/meta/",
        "/tests/",
    ]
    # use alternative target if it matches
    # e.g.) tests/integration/targets/xxxx/playbooks/tasks/included.yml
    #        --> role_name should be `xxxx`, not `playbooks`
    alternative_targets = {
        "/tasks/": [
            "/playbooks/tasks/",
        ],
        "/vars/": [
            "/playbooks/vars/",
        ],
    }
    role_name = ""
    role_path = ""
    for p in patterns:
        found = False
        if p in fpath:
            parent_dir = fpath.split(p, 1)[0]
            relative_path = os.path.join(p, fpath.split(p, 1)[-1])
            if relative_path[0] == "/":
                relative_path = relative_path[1:]
            for t in targets:
                if t in relative_path:
                    _target = t

                    _alt_targets = alternative_targets.get(t, [])
                    for at in _alt_targets:
                        if at in relative_path:
                            _target = at
                            break

                    _path = relative_path.rsplit(_target, 1)[0]
                    role_name = _path.split("/")[-1]

                    # if the path is something like "xxxx/roles/tasks"
                    # it is not an actual role, so skip it
                    if role_name == p.strip("/"):
                        continue

                    role_path = os.path.join(parent_dir, _path)
                    found = True
                    break
        if found:
            break
    return role_name, role_path


def get_project_info_for_file(fpath: str, root_dir: str) -> tuple[str, str]:
    """Return project name and root dir for a file under root_dir.

    Args:
        fpath: Path to a file.
        root_dir: Project root directory.

    Returns:
        Tuple of (project_name, root_dir).

    """
    return os.path.basename(root_dir), root_dir


def is_meta_yml(yml_path: str) -> bool:
    """Return True if path looks like role meta (e.g. .../meta/filename).

    Args:
        yml_path: Path to a YAML file.

    Returns:
        True if path contains /meta/ as parent of filename.

    """
    parts = yml_path.split("/")
    return bool(len(parts) > 2 and parts[-2] == "meta")


def is_vars_yml(yml_path: str) -> bool:
    """Return True if path is under vars/ or defaults/.

    Args:
        yml_path: Path to a YAML file.

    Returns:
        True if parent directory is vars or defaults.

    """
    parts = yml_path.split("/")
    return bool(len(parts) > 2 and parts[-2] in ["vars", "defaults"])


def count_top_level_element(yml_body: str = "") -> int:
    """Count top-level YAML elements (by indent) in the body.

    Args:
        yml_body: Raw YAML string.

    Returns:
        Count of top-level elements, or -1 if none.

    """

    def _is_skip_line(line: str) -> bool:
        # skip empty line
        if not line.strip():
            return True
        # skip comment line
        return line.strip()[0] == "#"

    lines = yml_body.splitlines()
    top_level_indent = 1024
    valid_line_found = False
    for line in lines:
        if _is_skip_line(line):
            continue

        valid_line_found = True
        indent_level = len(line) - len(line.lstrip())
        if indent_level < top_level_indent:
            top_level_indent = indent_level

    if not valid_line_found:
        return -1

    count = 0
    for line in lines:
        if _is_skip_line(line):
            continue
        if len(line) < top_level_indent:
            continue
        elem = line[top_level_indent:]
        if not elem:
            continue
        if elem[0] == " ":
            continue
        else:
            count += 1
    return count


def label_yml_file(
    yml_path: str = "", yml_body: str = "", task_num_thresh: int = 50
) -> tuple[str, int, dict[str, str] | None]:
    """Classify YAML as playbook, taskfile, or others; optionally enforce task count limit.

    Args:
        yml_path: Path to YAML file (optional).
        yml_body: Raw YAML string (optional).
        task_num_thresh: Max tasks/elements; exceed returns error.

    Returns:
        Tuple of (label, name_count, error_dict or None).

    """
    body = ""
    data = None
    error = None
    if yml_body:
        body = yml_body
    elif not yml_body and yml_path:
        try:
            with open(yml_path) as file:
                body = file.read()
        except Exception:
            error = {"type": "FileReadError", "detail": traceback.format_exc()}
    if error:
        return "others", -1, error

    lines = body.splitlines()
    # roughly count tasks
    name_count = len([line for line in lines if line.lstrip().startswith("- name:")])

    if task_num_thresh > 0 and name_count > task_num_thresh:
        error_detail = f"The number of task names found in yml exceeds the threshold ({task_num_thresh})"
        error = {"type": "TooManyTasksError", "detail": error_detail}
        return "others", name_count, error

    top_level_element_count = count_top_level_element(body)
    if task_num_thresh > 0 and top_level_element_count > task_num_thresh:
        error_detail = f"The number of top-level elements found in yml exceeds the threshold ({task_num_thresh})"
        error = {"type": "TooManyTasksError", "detail": error_detail}
        return "others", name_count, error

    try:
        data = yaml.safe_load(body)
    except Exception:
        error = {"type": "YAMLParseError", "detail": traceback.format_exc()}
    if error:
        return "others", name_count, error

    label = ""
    if not body or not data:
        label_by_path = ""
        if yml_path:
            label_by_path = label_empty_file_by_path(yml_path)
        label = label_by_path if label_by_path else "others"
    elif data and not isinstance(data, list):
        label = "others"
    elif could_be_playbook_detail(body, data):
        label = "playbook"
    elif could_be_taskfile(body, data):
        label = "taskfile"
    else:
        label = "others"
    return label, name_count, None


def get_yml_label(
    file_path: str, root_path: str, task_num_threshold: int = -1
) -> tuple[str, YAMLDict | None, YAMLDict | None]:
    """Get label and optional metadata for a YAML file under root_path.

    Args:
        file_path: Absolute path to the file.
        root_path: Project root path.
        task_num_threshold: Max tasks for classification; -1 to disable.

    Returns:
        Tuple of (label, metadata_dict or None, error_dict or None).

    """
    relative_path = file_path.replace(root_path, "")
    if relative_path[-1] == "/":
        relative_path = relative_path[:-1]

    label, _, error = label_yml_file(file_path, task_num_thresh=task_num_threshold)
    role_name, role_path = get_role_info_from_path(file_path)
    role_info = None
    if role_name and role_path:
        role_info = {"name": role_name, "path": role_path}

    project_name, project_path = get_project_info_for_file(file_path, root_path)
    project_info = None
    if project_name and project_path:
        project_info = {"name": project_name, "path": project_path}

    # print(f"[{label}] {relative_path} {role_info}")
    if error:
        logger.debug(f"failed to get yml label:\n {error}")
        label = "error"
    return label, cast(YAMLDict | None, role_info), cast(YAMLDict | None, project_info)


def get_yml_list(root_dir: str, task_num_threshold: int = -1) -> list[YAMLDict]:
    """Build list of YAML file metadata (label, role_info, etc.) under root_dir.

    Args:
        root_dir: Root directory to scan for YAML files.
        task_num_threshold: Max tasks per file for classification; -1 to disable.

    Returns:
        List of dicts with filepath, label, role_info, project_info, etc.

    """
    found_ymls = find_all_ymls(root_dir)
    all_files = []
    for yml_path in found_ymls:
        label, role_info, project_info = get_yml_label(yml_path, root_dir, task_num_threshold)
        if not role_info:
            role_info = {}
        if not project_info:
            project_info = {}
        if role_info:
            path_val = role_info.get("path", "")
            if path_val and not str(path_val).startswith(root_dir):
                role_info["path"] = os.path.join(root_dir, str(path_val))
            role_info["is_external_dependency"] = "." in str(role_info.get("name", ""))
        in_role: bool = bool(role_info)
        in_project: bool = bool(project_info)
        all_files.append(
            {
                "filepath": yml_path,
                "path_from_root": yml_path.replace(root_dir, "").lstrip("/"),
                "label": label,
                "role_info": role_info,
                "project_info": project_info,
                "in_role": in_role,
                "in_project": in_project,
            }
        )
    return cast(list[YAMLDict], all_files)


def list_scan_target(root_dir: str, task_num_threshold: int = -1) -> list[YAMLDict]:
    """List playbook and taskfile (and role) scan targets under root_dir.

    Args:
        root_dir: Root directory to scan.
        task_num_threshold: Max tasks per file; -1 to disable.

    Returns:
        List of target dicts with filepath, path_from_root, scan_type.

    """
    yml_list = get_yml_list(root_dir=root_dir, task_num_threshold=task_num_threshold)
    known_roles = set()
    all_targets = []
    for yml_info in yml_list:
        if str(yml_info.get("label", "")) not in ["playbook", "taskfile"]:
            continue
        role_path = ""
        if yml_info.get("in_role"):
            role_info = yml_info.get("role_info")
            role_path = str(role_info.get("path", "")) if isinstance(role_info, dict) else ""
        if role_path and role_path in known_roles:
            continue
        scan_type = ""
        filepath = ""
        path_from_root = ""
        if role_path:
            scan_type = "role"
            filepath = role_path
            path_from_root = role_path.replace(root_dir, "").lstrip("/")
            known_roles.add(role_path)
        else:
            scan_type = str(yml_info.get("label", ""))
            filepath = str(yml_info.get("filepath", ""))
            path_from_root = str(yml_info.get("path_from_root", ""))
        target_info: YAMLDict = {
            "filepath": filepath,
            "path_from_root": path_from_root,
            "scan_type": scan_type,
        }
        all_targets.append(target_info)
    all_targets = sorted(all_targets, key=lambda x: str(x.get("filepath", "")))
    all_targets = sorted(all_targets, key=lambda x: str(x.get("scan_type", "")))
    return all_targets


def update_line_with_space(new_line_content: str, old_line_content: str, leading_spaces: int = 0) -> str:
    """Return new_line_content with leading spaces matching old_line_content.

    Args:
        new_line_content: New line text (leading spaces stripped).
        old_line_content: Reference line for indent.
        leading_spaces: Override indent; 0 means use old_line_content indent.

    Returns:
        Line with leading spaces applied.

    """
    new_line_content = new_line_content.lstrip(" ")
    if not leading_spaces:
        leading_spaces = len(old_line_content) - len(old_line_content.lstrip())
    return " " * leading_spaces + new_line_content


def populate_new_data_list(data: str, line_number_list: list[str]) -> list[str]:
    """Copy lines before the first mutated region from data into a new list.

    Args:
        data: Full file content.
        line_number_list: List of line range specs (e.g. L1-5).

    Returns:
        Lines from start of file up to (but not including) first mutation.

    """
    input_line_number = 0
    for each in line_number_list:
        input_line_number = int(each.lstrip("L").split("-")[0])
        break
    temp_data = data.splitlines(keepends=True)
    return temp_data[0 : input_line_number - 1]


def check_and_add_diff_lines(start_line: int, stop_line: int, lines: list[str], data_copy: list[str]) -> None:
    """Append lines between start_line and stop_line from lines into data_copy.

    Args:
        start_line: Start index (1-based).
        stop_line: End index.
        lines: Source lines.
        data_copy: List to append to (modified in place).

    """
    diff_in_line = stop_line - start_line
    data_copy.append("\n")
    for i in range(start_line, (start_line + diff_in_line) - 1):
        line = lines[i]
        data_copy.append(line)


def check_diff_and_copy_olddata_to_newdata(
    line_number_list: list[str], lines: list[str], new_data: list[str]
) -> list[str]:
    """Append remaining old lines after the last mutation to new_data.

    Args:
        line_number_list: List of mutated line range specs.
        lines: Original file lines.
        new_data: List to append remaining lines to.

    Returns:
        new_data (modified in place and returned).

    """
    if line_number_list and isinstance(line_number_list, list):
        new_content_last_set = line_number_list[-1]
        new_content_last_line = int(new_content_last_set.lstrip("L").split("-")[1])
        if new_content_last_line < len(lines):
            for i in range(new_content_last_line, len(lines)):
                new_data.append(lines[i])
        return new_data
    return new_data


def update_and_append_new_line(new_line: str, old_line: str, leading_spaces: int, data_copy: list[str]) -> str:
    """Adjust new_line indent to match old_line and append to data_copy.

    Args:
        new_line: New content line.
        old_line: Reference line for indent.
        leading_spaces: Override indent.
        data_copy: List to append the adjusted line to.

    Returns:
        Empty string (result appended to data_copy).

    """
    line_with_adjusted_space = update_line_with_space(new_line, old_line, leading_spaces)
    data_copy.append(line_with_adjusted_space)
    return ""


def update_the_yaml_target(file_path: str, line_number_list: list[str], new_content_list: list[str]) -> None:
    """Apply ARI mutations to a YAML file by line ranges and write back.

    Args:
        file_path: Path to the YAML file.
        line_number_list: List of line range specs (e.g. L1-5).
        new_content_list: New content for each range.

    Raises:
        IndexError: If line_number_list and new_content_list length mismatch or invalid range.
    """
    try:
        # Read the original YAML file
        with open(file_path) as file:
            data = file.read()
        yaml = FormattedYAML(
            # Ansible only uses YAML 1.1, but others files should use newer 1.2 (ruamel.yaml defaults to 1.2)
        )
        data_copy = populate_new_data_list(data, line_number_list)
        stop_line_number = 0
        new_lines: list[str] = []
        for iter in range(len(line_number_list)):
            line_number = line_number_list[iter]
            new_content = new_content_list[iter]
            input_line_number = line_number.lstrip("L").split("-")
            lines = data.splitlines(keepends=True)
            if new_lines:
                for i in range(len(new_lines)):
                    try:
                        data_copy.append(new_lines.pop(i))
                    except IndexError:
                        break
            new_lines = new_content.splitlines(keepends=True)
            # Update the specific line with new content
            start_line_number = int(input_line_number[0])
            if stop_line_number > 0 and (start_line_number - stop_line_number) > 1:
                check_and_add_diff_lines(stop_line_number, start_line_number, lines, data_copy)
            stop_line_number = int(input_line_number[1])
            diff_in_lines = stop_line_number - start_line_number
            temp_content = []
            start = start_line_number - 1
            end = stop_line_number - 1
            data_copy.append("\n")
            for i in range(start, end):
                line_idx = i
                if len(lines) == i:
                    break
                try:
                    # always pop 1st element of the new lines list
                    new_line_content = new_lines.pop(0)
                except IndexError:
                    break
                if 0 <= line_idx < len(lines):
                    # Preserve the original indentation
                    old_line_content = lines[line_idx]
                    if "---" in old_line_content:
                        continue
                    if new_line_content in old_line_content:
                        leading_spaces = len(lines[line_idx]) - len(lines[line_idx].lstrip())
                        temp_content.append(new_line_content)
                        new_line_content = new_line_content.lstrip(" ")
                        lines[line_idx] = " " * leading_spaces + new_line_content
                        data_copy.append(lines[line_idx])
                    else:
                        new_line_key = new_line_content.split(":")
                        new_key = new_line_key[0].strip(" ")
                        for k in range(start, end):
                            if k < len(lines):
                                old_line_key = lines[k].split(":")
                                if "---" in old_line_key[0]:
                                    continue
                                old_key = old_line_key[0].strip(" ")
                                if "-" in old_line_key[0] and ":" not in lines[k] and "-" in new_key:
                                    # diff_in_lines = len(lines) - len(new_lines)
                                    leading_spaces = len(lines[k]) - len(lines[k].lstrip())
                                    if diff_in_lines > len(lines):
                                        for i in range(k, k + diff_in_lines):
                                            if lines[i] == "\n":
                                                lines.pop(i - 1)
                                                break
                                            elif i < len(lines) and ":" not in lines[i]:
                                                lines.pop(i)
                                            else:
                                                break
                                    new_line_content = update_and_append_new_line(
                                        new_line_content, lines[k], leading_spaces, data_copy
                                    )
                                    break
                                elif (
                                    old_key == new_key
                                    or old_key.rstrip("\n") == new_key
                                    or old_key.rstrip("\n") in new_key.split(".")
                                ):
                                    new_line_content = update_and_append_new_line(
                                        new_line_content, lines[k], 0, data_copy
                                    )
                                    break
                        # if there wasn't a match with old line, updated by ARI and added w/o change
                        if new_line_content:
                            data_copy.append(new_line_content)
                else:
                    raise IndexError("Line number out of range.")
        # check for diff b/w new content and old contents,
        # and copy the old content that's not updated by ARI mutation
        data_copy = check_diff_and_copy_olddata_to_newdata(line_number_list, lines, data_copy)
        # Join the lines back to a single string
        updated_data = "".join(data_copy)
        # Parse the updated YAML content to ensure it is valid
        updated_parsed_data = yaml.load(updated_data)
        # Write the updated YAML content back to the file
        if updated_parsed_data:
            with open(file_path, "w") as file:
                yaml.dump(updated_parsed_data, file)
    except Exception as ex:
        logger.warning(
            "YAML LINES: ARI fix update yaml by lines failed for file: '%s', with error: '%s'", file_path, ex
        )
        return
