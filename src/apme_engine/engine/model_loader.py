"""Load Ansible content (repositories, playbooks, roles, modules, collections) into model objects."""

from __future__ import annotations

import datetime
import json
import os
import re
import traceback
from typing import cast

import yaml

try:
    # if `libyaml` is available, use C based loader for performance
    import _yaml  # type: ignore[import-not-found]  # noqa: F401
    from yaml import CSafeLoader as Loader
except Exception:
    from yaml import SafeLoader as Loader  # type: ignore[assignment]

import contextlib

from . import logger
from .awx_utils import could_be_playbook
from .finder import (
    could_be_playbook_detail,
    could_be_taskfile,
    find_best_repo_root_path,
    find_collection_name_of_repo,
    find_module_dirs,
    find_module_name,
    get_task_blocks,
    module_dir_patterns,
    search_inventory_files,
    search_module_files,
    search_taskfiles_for_playbooks,
)
from .models import (
    BecomeInfo,
    Collection,
    ExecutableType,
    File,
    Inventory,
    InventoryType,
    Load,
    LoadType,
    Module,
    ModuleArgument,
    ObjectList,
    Play,
    Playbook,
    PlaybookFormatError,
    Repository,
    Role,
    RoleInPlay,
    Task,
    TaskFile,
    TaskFormatError,
    YAMLDict,
    YAMLValue,
)
from .safe_glob import safe_glob
from .utils import (
    get_class_by_arg_type,
    get_documentation_in_module_file,
    get_module_specs_by_ansible_doc,
    is_test_object,
    parse_bool,
    split_target_playbook_fullpath,
    split_target_taskfile_fullpath,
)


def _safe_int(val: object) -> int:
    """Safely convert YAMLValue to int for task_loading counters.

    Args:
        val: Value to convert (int, float, or str).

    Returns:
        Integer value, or 0 if conversion fails.
    """
    if isinstance(val, int | float):
        return int(val)
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return 0
    return 0


# collection info direcotry can be something like
#   "brightcomputing.bcm-9.1.11+41615.gitfab9053.info"
collection_info_dir_re = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+-[0-9]+\.[0-9]+\.[0-9]+.*\.info$")

string_module_options_re = re.compile(r"^(?:[^ ]* ?)([a-z0-9_]+=(?:[^ ]*{{ [^*]+ }}[^ ]*|[^ ])+\s?)")

string_module_option_parts_re = re.compile(r"([a-z0-9_]+=(?:[^ ]*{{ [^*]+ }}[^ ]*|[^ ])+\s?)")

loop_task_option_names = [
    "loop",
    "with_list",
    "with_items",
    "with_dict",
    # TODO: support the following
    "with_indexed_items",
    "with_flattened",
    "with_together",
    "with_sequence",
    "with_subelements",
    "with_nested",
    "with_cartesian",
    "with_random_choice",
    # NOTE: the following are not listed in Ansible loop document, but actually available
    "with_first_found",
    "with_fileglob",
]


def load_repository(
    path: str = "",
    installed_collections_path: str = "",
    installed_roles_path: str = "",
    my_collection_name: str = "",
    basedir: str = "",
    target_playbook_path: str = "",
    target_taskfile_path: str = "",
    use_ansible_doc: bool = True,
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
    include_test_contents: bool = False,
    yaml_label_list: list[tuple[str, str, YAMLValue]] | None = None,
    load_children: bool = True,
) -> Repository:
    """Load a full repository with playbooks, roles, modules, and inventories.

    Args:
        path: Path to the repository root.
        installed_collections_path: Path to installed Ansible collections.
        installed_roles_path: Path to installed Ansible roles.
        my_collection_name: Override collection name for the repo.
        basedir: Base directory for resolving relative paths.
        target_playbook_path: Path to target playbook when scanning a single playbook.
        target_taskfile_path: Path to target taskfile when scanning a single taskfile.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        include_test_contents: Whether to include test/molecule content.
        yaml_label_list: Pre-computed list of (path, label, role_info) for YAML files.
        load_children: Whether to load full objects or just paths.

    Returns:
        Repository object with playbooks, roles, modules, inventories, and files.
    """
    repoObj = Repository()

    repo_path = ""
    repo_to_root = ""
    if path == "":
        # if path is empty, just load installed collections / roles
        repo_path = ""
    else:
        # otherwise, find the root path by searching playbooks
        try:
            repo_path = find_best_repo_root_path(path)
            repo_to_root = os.path.relpath(path, repo_path)
        except Exception as exc:
            logger.debug(f'failed to find a root directory for a project in "{path}"; error: {exc}')
            # if an exception occurs while finding repo root, use the input path as repo root
            repo_path = path

    # if `path` and `repo_path` are different, update yaml_label_list
    if repo_to_root and yaml_label_list is not None:
        yaml_label_list = [
            (os.path.normpath(os.path.join(repo_to_root, fpath)), label, role_info)
            for (fpath, label, role_info) in yaml_label_list
        ]

    if repo_path != "":
        if my_collection_name == "":
            my_collection_name = find_collection_name_of_repo(repo_path)
        if my_collection_name != "":
            repoObj.my_collection_name = my_collection_name

    if basedir == "":
        basedir = path

    logger.debug(f"start loading the repo {repo_path}")
    logger.debug("start loading playbooks")
    repoObj.playbooks = load_playbooks(
        repo_path,
        basedir=basedir,
        include_test_contents=include_test_contents,
        yaml_label_list=yaml_label_list,
        load_children=load_children,
    )
    logger.debug(f"done ... {len(repoObj.playbooks)} playbooks loaded")
    logger.debug("start loading roles")
    repoObj.roles = load_roles(
        repo_path,
        basedir=basedir,
        use_ansible_doc=use_ansible_doc,
        include_test_contents=include_test_contents,
        yaml_label_list=yaml_label_list,
        load_children=load_children,
    )
    # Standalone role: check for canonical role entry points rather than
    # a bare tasks/ dir, which many non-role projects also have.
    _has_role_markers = any(
        os.path.exists(os.path.join(repo_path, marker))
        for marker in (
            os.path.join("tasks", "main.yml"),
            os.path.join("tasks", "main.yaml"),
            os.path.join("meta", "main.yml"),
            os.path.join("meta", "main.yaml"),
        )
    )
    if _has_role_markers:
        role_name = os.path.basename(repo_path)
        role = load_role(
            path=repo_path,
            name=role_name,
            collection_name=my_collection_name,
            basedir=basedir,
            use_ansible_doc=use_ansible_doc,
            include_test_contents=include_test_contents,
            load_children=load_children,
        )
        if role:
            if load_children:
                repoObj.roles.append(role)
            else:
                repoObj.roles.append(role.defined_in)
    logger.debug(f"done ... {len(repoObj.roles)} roles loaded")
    logger.debug("start loading modules (that are defined in this repository)")
    repoObj.modules = load_modules(
        repo_path,
        basedir=basedir,
        collection_name=repoObj.my_collection_name,
        use_ansible_doc=use_ansible_doc,
        load_children=load_children,
    )
    logger.debug(f"done ... {len(repoObj.modules)} modules loaded")
    logger.debug("start loading taskfiles (that are defined for playbooks in this repository)")
    repoObj.taskfiles = load_taskfiles(
        repo_path, basedir=basedir, yaml_label_list=yaml_label_list, load_children=load_children
    )
    logger.debug(f"done ... {len(repoObj.taskfiles)} task files loaded")
    logger.debug("start loading inventory files")
    repoObj.inventories = cast(list[Inventory | str], load_inventories(repo_path, basedir=basedir))
    logger.debug(f"done ... {len(repoObj.inventories)} inventory files loaded")
    repoObj.files = load_files(
        path=repo_path, basedir=basedir, yaml_label_list=yaml_label_list, load_children=load_children
    )
    logger.debug(f"done ... {len(repoObj.files)} other files loaded")
    logger.debug("start loading installed collections")
    repoObj.installed_collections = cast(list[Collection | str], load_installed_collections(installed_collections_path))

    logger.debug(f"done ... {len(repoObj.installed_collections)} collections loaded")
    logger.debug("start loading installed roles")
    repoObj.installed_roles = cast(list[Role | str], load_installed_roles(installed_roles_path))
    logger.debug(f"done ... {len(repoObj.installed_roles)} roles loaded")
    repoObj.requirements = cast(YAMLDict, load_requirements(path=repo_path))
    name = os.path.basename(path)
    repoObj.name = name
    _path = name
    if os.path.abspath(repo_path).startswith(os.path.abspath(path)):
        relative = os.path.abspath(repo_path)[len(os.path.abspath(path)) :]
        _path = os.path.join(name, relative)
    repoObj.path = _path
    repoObj.installed_collections_path = installed_collections_path
    repoObj.installed_roles_path = installed_roles_path
    repoObj.target_playbook_path = target_playbook_path
    repoObj.target_taskfile_path = target_taskfile_path
    logger.debug("done")

    return repoObj


def load_installed_collections(installed_collections_path: str) -> list[Collection]:
    """Load all installed Ansible collections from a directory.

    Args:
        installed_collections_path: Path to the collections directory (e.g. ~/.ansible/collections).

    Returns:
        List of Collection objects.
    """
    search_path = installed_collections_path
    if installed_collections_path == "" or not os.path.exists(search_path):
        return []
    if os.path.exists(os.path.join(search_path, "ansible_collections")):
        search_path = os.path.join(search_path, "ansible_collections")
    dirs = os.listdir(search_path)
    basedir = os.path.dirname(os.path.normpath(installed_collections_path))
    collections = []
    for d in dirs:
        if collection_info_dir_re.match(d):
            continue
        if not os.path.exists(os.path.join(search_path, d)):
            continue
        subdirs = os.listdir(os.path.join(search_path, d))
        for sd in subdirs:
            collection_path = os.path.join(search_path, d, sd)
            try:
                c = load_collection(collection_dir=collection_path, basedir=basedir)
                collections.append(c)
            except Exception:
                logger.exception(f"error while loading the collection at {collection_path}")
    return collections


def load_inventory(path: str, basedir: str = "") -> Inventory:
    """Load a single inventory file (YAML or JSON).

    Args:
        path: Path to the inventory file.
        basedir: Base directory for resolving relative paths.

    Returns:
        Inventory object with variables and metadata.

    Raises:
        ValueError: If the file is not found.
    """
    invObj = Inventory()
    fullpath = ""
    if os.path.exists(path) and path != "" and path != ".":
        fullpath = path
    if os.path.exists(os.path.join(basedir, path)):
        fullpath = os.path.normpath(os.path.join(basedir, path))
    if fullpath == "":
        raise ValueError("file not found")
    defined_in = fullpath
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    invObj.defined_in = defined_in
    base_parts = os.path.splitext(os.path.basename(fullpath))
    invObj.name = base_parts[0]
    file_ext = base_parts[1]
    dirname = os.path.dirname(fullpath)
    inventory_type = InventoryType.UNKNOWN_TYPE
    group_name = ""
    host_name = ""
    if dirname.endswith("/group_vars"):
        inventory_type = InventoryType.GROUP_VARS_TYPE
        group_name = base_parts[0]
    elif dirname.endswith("/host_vars"):
        inventory_type = InventoryType.HOST_VARS_TYPE
        host_name = base_parts[0]
    invObj.inventory_type = inventory_type
    invObj.group_name = group_name
    invObj.host_name = host_name
    data = {}
    if file_ext == "":
        # TODO: parse it as INI file
        pass
    elif file_ext == ".yml" or file_ext == ".yaml":
        with open(fullpath) as file:
            try:
                data = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load this yaml file (inventory); {e.args[0]}")
    elif file_ext == ".json":
        with open(fullpath) as file:
            try:
                data = json.load(file)
            except Exception as e:
                logger.debug(f"failed to load this json file (inventory); {e.args[0]}")
    invObj.variables = data
    return invObj


def load_inventories(path: str, basedir: str = "") -> list[Inventory]:
    """Load all inventory files in a directory.

    Args:
        path: Path to search for inventory files.
        basedir: Base directory for resolving relative paths.

    Returns:
        List of Inventory objects.
    """
    if not os.path.exists(path):
        return []
    inventories = []
    inventory_file_paths = search_inventory_files(path)
    if len(inventory_file_paths) > 0:
        for inventory_path in inventory_file_paths:
            try:
                iv = load_inventory(inventory_path, basedir=basedir)
                inventories.append(iv)
            except Exception:
                logger.exception(f"error while loading the inventory file at {inventory_path}")
    return inventories


# TODO: need more-detailed labels like `vars`? (currently use the passed one as is)
def load_file(
    path: str,
    basedir: str = "",
    label: str = "",
    body: str = "",
    error: str = "",
    read: bool = True,
    role_name: str = "",
    collection_name: str = "",
) -> File:
    """Load a file (variable file, template, or other) into a File object.

    Args:
        path: Path to the file.
        basedir: Base directory for resolving relative paths.
        label: Label for the file (e.g. 'others', 'vars').
        body: Pre-read file body; used when read is False.
        error: Pre-set error message if load failed.
        read: Whether to read the file from disk.
        role_name: Role name if file belongs to a role.
        collection_name: Collection name if file belongs to a collection.

    Returns:
        File object with body, data, and metadata.
    """
    fullpath = os.path.join(basedir, path)
    if not os.path.exists(fullpath) and path and os.path.exists(path):
        fullpath = path

    # use passed body/error when provided or when read=False
    if body or error or not read:
        pass
    else:
        # otherwise, try reading the file
        if os.path.exists(fullpath):
            try:
                with open(fullpath) as file:
                    body = file.read()
            except Exception:
                error = traceback.format_exc()
        else:
            error = f"File not found: {fullpath}"

    # try reading body as a YAML string
    data_str = ""
    encrypted = False
    if body:
        if "$ANSIBLE_VAULT" in body:
            encrypted = True

        try:
            data = yaml.safe_load(body)
            data_str = json.dumps(data, separators=(",", ":"))
        except Exception:
            # ignore exception if any
            # because possibly this file is not a YAML file
            pass

    defined_in = fullpath
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]

    fObj = File()
    fObj.name = defined_in
    fObj.body = body
    fObj.data = data_str
    fObj.encrypted = encrypted
    fObj.error = error
    fObj.label = label
    fObj.defined_in = defined_in
    if role_name != "":
        fObj.role = role_name
    if collection_name != "":
        fObj.collection = collection_name
    fObj.set_key()
    return fObj


# load general files that has no task definitions
# e.g. variable files, jinja2 templates and non-ansible files
# TODO: support loading without pre-computed yaml_label_list
# TODO: support non-YAML files
def load_files(
    path: str,
    basedir: str = "",
    yaml_label_list: list[tuple[str, str, YAMLValue]] | None = None,
    role_name: str = "",
    collection_name: str = "",
    load_children: bool = True,
) -> list[File | str]:
    """Load general files (variable files, templates) labeled as 'others'.

    Args:
        path: Path to the repository root.
        basedir: Base directory for resolving relative paths.
        yaml_label_list: Pre-computed list of (path, label, role_info).
        role_name: Role name for files in a role.
        collection_name: Collection name for files in a collection.
        load_children: Whether to load full File objects or just paths.

    Returns:
        List of File objects or paths.
    """
    if not yaml_label_list:
        return []

    files: list[File | str] = []
    for fpath, label, _role_info in yaml_label_list:
        if not fpath:
            continue
        if not label:
            continue
        # load only `others` files
        if label != "others":
            continue
        f = load_file(path=fpath, basedir=basedir, label=label, role_name=role_name, collection_name=collection_name)
        if load_children:
            files.append(f)
        else:
            files.append(f.defined_in)
    return files


def load_play(
    path: str,
    index: int,
    play_block_dict: YAMLDict,
    role_name: str = "",
    collection_name: str = "",
    parent_key: str = "",
    parent_local_key: str = "",
    yaml_lines: str = "",
    basedir: str = "",
    skip_task_format_error: bool = True,
) -> Play:
    """Load a single play block from a playbook dict into a Play object.

    Args:
        path: Path to the playbook file.
        index: Index of the play in the playbook list.
        play_block_dict: Parsed YAML dict for the play block.
        role_name: Role name if play is in a role.
        collection_name: Collection name if play is in a collection.
        parent_key: Parent object key for hierarchy.
        parent_local_key: Parent local key for hierarchy.
        yaml_lines: Raw YAML content for line number lookup.
        basedir: Base directory for path resolution.
        skip_task_format_error: Whether to skip malformed tasks.

    Returns:
        Play object with tasks, roles, handlers, and variables.

    Raises:
        ValueError: If play_block_dict is None.
        PlaybookFormatError: If play block is not a valid dict.
        TaskFormatError: If a task block is malformed and skip_task_format_error is False.
    """
    pbObj = Play()
    if play_block_dict is None:
        raise ValueError("play block dict is required to load Play")
    if not isinstance(play_block_dict, dict):
        raise PlaybookFormatError("this play block is not loaded as dict; maybe this is not a playbook")
    data_block = play_block_dict
    play_keywords = [
        "hosts",
        "import_playbook",
        "include",
        "tasks",
        "pre_tasks",
        "post_tasks",
    ]
    could_be_play = any([k in data_block for k in play_keywords])
    if not could_be_play:
        raise PlaybookFormatError(
            f"this play block does not have any of the following keywords {play_keywords}; maybe this is not a playbook"
        )

    jsonpath = f"$.{index}"
    pbObj.index = index
    pbObj.role = role_name
    pbObj.collection = collection_name
    pbObj.jsonpath = jsonpath
    pbObj.set_key(parent_key, parent_local_key)
    play_name_val = data_block.get("name", "")
    play_name = str(play_name_val) if play_name_val is not None else ""
    collections_in_play_raw = data_block.get("collections", [])
    collections_in_play: list[str] = (
        [str(x) for x in collections_in_play_raw] if isinstance(collections_in_play_raw, list) else []
    )
    pre_tasks = []
    post_tasks = []
    tasks = []
    handlers = []
    roles = []
    variables = {}
    vars_files = []
    module_defaults = {}
    play_options = {}
    import_module = ""
    import_playbook = ""

    tasks_keys = ["pre_tasks", "tasks", "post_tasks", "handlers"]
    keys = [k for k in data_block if k not in tasks_keys]
    keys.extend(tasks_keys)
    task_count = 0
    task_errors: list[BaseException] = []
    task_loading: dict[str, object] = {
        "total": 0,
        "success": 0,
        "failure": 0,
        "errors": task_errors,
    }
    err: BaseException | None = None
    for k in keys:
        if k not in data_block:
            continue
        v = data_block[k]
        if k == "name" or k == "collections":
            pass
        elif k == "pre_tasks":
            if not isinstance(v, list):
                continue
            jsonpath_prefix = f".plays.{index}.pre_tasks"
            task_dict_list = [x for x in v if isinstance(x, dict)]
            task_blocks, _ = get_task_blocks(
                task_dict_list=task_dict_list if task_dict_list else None,
                jsonpath_prefix=jsonpath_prefix,
            )
            if task_blocks is None:
                continue
            last_task_line_num = -1
            for task_dict, task_jsonpath in task_blocks:
                task_loading["total"] = _safe_int(task_loading.get("total", 0)) + 1
                i = task_count
                err = None
                try:
                    t = load_task(
                        path=path,
                        index=i,
                        task_block_dict=cast(dict[str, object], task_dict),
                        task_jsonpath=task_jsonpath,
                        role_name=role_name,
                        collection_name=collection_name,
                        collections_in_play=collections_in_play,
                        play_index=index,
                        parent_key=pbObj.key,
                        parent_local_key=pbObj.local_key,
                        yaml_lines=yaml_lines,
                        previous_task_line=last_task_line_num,
                        basedir=basedir,
                    )
                    pre_tasks.append(t)
                    if t:
                        task_loading["success"] = _safe_int(task_loading.get("success", 0)) + 1
                        if t.line_num_in_file and len(t.line_num_in_file) == 2:
                            last_task_line_num = t.line_num_in_file[1]
                except TaskFormatError as exc:
                    err = exc
                    if skip_task_format_error:
                        logger.debug(f"this task is wrong format; skip the task in {path}, index: {i}; skip this")
                    else:
                        raise TaskFormatError(
                            f"this task is wrong format; skip the task in {path}, index: {{i}}"
                        ) from exc
                except Exception as exc:
                    err = exc
                    logger.exception(f"error while loading the task at {path} (index={i})")
                finally:
                    task_count += 1
                    if err is not None:
                        task_loading["failure"] = _safe_int(task_loading.get("failure", 0)) + 1
                        task_errors.append(err)
        elif k == "tasks":
            if not isinstance(v, list):
                continue
            jsonpath_prefix = f".plays.{index}.tasks"
            task_dict_list = [x for x in v if isinstance(x, dict)]
            task_blocks, _ = get_task_blocks(
                task_dict_list=task_dict_list if task_dict_list else None,
                jsonpath_prefix=jsonpath_prefix,
            )
            if task_blocks is None:
                continue
            last_task_line_num = -1
            for task_dict, task_jsonpath in task_blocks:
                i = task_count
                task_loading["total"] = _safe_int(task_loading.get("total", 0)) + 1
                err = None
                try:
                    t = load_task(
                        path=path,
                        index=i,
                        task_block_dict=cast(dict[str, object], task_dict),
                        task_jsonpath=task_jsonpath,
                        role_name=role_name,
                        collection_name=collection_name,
                        collections_in_play=collections_in_play,
                        play_index=index,
                        parent_key=pbObj.key,
                        parent_local_key=pbObj.local_key,
                        yaml_lines=yaml_lines,
                        previous_task_line=last_task_line_num,
                        basedir=basedir,
                    )
                    tasks.append(t)
                    if t:
                        task_loading["success"] = _safe_int(task_loading.get("success", 0)) + 1
                        if t.line_num_in_file and len(t.line_num_in_file) == 2:
                            last_task_line_num = t.line_num_in_file[1]
                except TaskFormatError as exc:
                    err = exc
                    if skip_task_format_error:
                        logger.debug(f"this task is wrong format; skip the task in {path}, index: {i}; skip this")
                    else:
                        raise TaskFormatError(
                            f"this task is wrong format; skip the task in {path}, index: {{i}}"
                        ) from exc
                except Exception as exc:
                    err = exc
                    logger.exception(f"error while loading the task at {path} (index={i})")
                finally:
                    task_count += 1
                    if err is not None:
                        task_loading["failure"] = _safe_int(task_loading.get("failure", 0)) + 1
                        task_errors.append(err)
        elif k == "post_tasks":
            if not isinstance(v, list):
                continue
            jsonpath_prefix = f".plays.{index}.post_tasks"
            task_dict_list = [x for x in v if isinstance(x, dict)]
            task_blocks, _ = get_task_blocks(
                task_dict_list=task_dict_list if task_dict_list else None,
                jsonpath_prefix=jsonpath_prefix,
            )
            if task_blocks is None:
                continue
            last_task_line_num = -1
            for task_dict, task_jsonpath in task_blocks:
                i = task_count
                task_loading["total"] = _safe_int(task_loading.get("total", 0)) + 1
                err = None
                try:
                    t = load_task(
                        path=path,
                        index=i,
                        task_block_dict=cast(dict[str, object], task_dict),
                        task_jsonpath=task_jsonpath,
                        role_name=role_name,
                        collection_name=collection_name,
                        collections_in_play=collections_in_play,
                        play_index=index,
                        parent_key=pbObj.key,
                        parent_local_key=pbObj.local_key,
                        yaml_lines=yaml_lines,
                        previous_task_line=last_task_line_num,
                        basedir=basedir,
                    )
                    post_tasks.append(t)
                    if t:
                        task_loading["success"] = _safe_int(task_loading.get("success", 0)) + 1
                        if t.line_num_in_file and len(t.line_num_in_file) == 2:
                            last_task_line_num = t.line_num_in_file[1]
                except TaskFormatError as exc:
                    err = exc
                    if skip_task_format_error:
                        logger.debug(f"this task is wrong format; skip the task in {path}, index: {i}; skip this")
                    else:
                        raise TaskFormatError(
                            f"this task is wrong format; skip the task in {path}, index: {{i}}"
                        ) from exc
                except Exception as exc:
                    err = exc
                    logger.exception(f"error while loading the task at {path} (index={i})")
                finally:
                    task_count += 1
                    if err is not None:
                        task_loading["failure"] = _safe_int(task_loading.get("failure", 0)) + 1
                        task_errors.append(err)
        elif k == "handlers":
            if not isinstance(v, list):
                continue
            jsonpath_prefix = f".plays.{index}.handlers"
            task_dict_list = [x for x in v if isinstance(x, dict)]
            task_blocks, _ = get_task_blocks(
                task_dict_list=task_dict_list if task_dict_list else None,
                jsonpath_prefix=jsonpath_prefix,
            )
            if task_blocks is None:
                continue
            last_task_line_num = -1
            for task_dict, task_jsonpath in task_blocks:
                i = task_count
                task_loading["total"] = _safe_int(task_loading.get("total", 0)) + 1
                err = None
                try:
                    t = load_task(
                        path=path,
                        index=i,
                        task_block_dict=cast(dict[str, object], task_dict),
                        task_jsonpath=task_jsonpath,
                        role_name=role_name,
                        collection_name=collection_name,
                        collections_in_play=collections_in_play,
                        play_index=index,
                        parent_key=pbObj.key,
                        parent_local_key=pbObj.local_key,
                        yaml_lines=yaml_lines,
                        previous_task_line=last_task_line_num,
                        basedir=basedir,
                    )
                    handlers.append(t)
                    if t:
                        task_loading["success"] = _safe_int(task_loading.get("success", 0)) + 1
                        if t.line_num_in_file and len(t.line_num_in_file) == 2:
                            last_task_line_num = t.line_num_in_file[1]
                except TaskFormatError as exc:
                    err = exc
                    if skip_task_format_error:
                        logger.debug(f"this task is wrong format; skip the task in {path}, index: {i}; skip this")
                    else:
                        raise TaskFormatError(
                            f"this task is wrong format; skip the task in {path}, index: {{i}}"
                        ) from exc
                except Exception as exc:
                    err = exc
                    logger.exception(f"error while loading the task at {path} (index={i})")
                finally:
                    task_count += 1
                    if err is not None:
                        task_loading["failure"] = _safe_int(task_loading.get("failure", 0)) + 1
                        task_errors.append(err)
        elif k == "roles":
            if not isinstance(v, list):
                continue
            for i, r_block in enumerate(v):
                r_name = ""
                role_options: dict[str, object] = {}
                if isinstance(r_block, dict):
                    role_val = r_block.get("role", "")
                    r_name = str(role_val) if role_val is not None else ""
                    role_options = {rk: rv for rk, rv in r_block.items()}
                elif isinstance(r_block, str):
                    r_name = r_block
                try:
                    rip = load_roleinplay(
                        name=r_name,
                        options=role_options,
                        defined_in=path,
                        role_index=i,
                        play_index=index,
                        role_name=role_name,
                        collection_name=collection_name,
                        collections_in_play=collections_in_play,
                        basedir=basedir,
                    )
                    roles.append(rip)
                except Exception:
                    logger.exception(
                        f"error while loading the role in playbook at {path} (play_index={pbObj.index}, role_index={i})"
                    )
        elif k == "vars":
            if not isinstance(v, dict):
                continue
            variables = cast(dict[str, object], v)
        elif k == "vars_files":
            if not isinstance(v, list):
                continue
            vars_files = [str(x) for x in v]
        elif k == "module_defaults":
            if not isinstance(v, dict):
                continue
            module_defaults = cast(dict[str, object], v)
        elif k == "import_playbook" or k == "include":
            if not isinstance(v, str):
                continue
            import_module = k
            import_playbook = v
        else:
            play_options.update({k: v})

    pbObj.name = play_name
    pbObj.defined_in = path
    pbObj.import_module = import_module
    pbObj.import_playbook = import_playbook
    pbObj.pre_tasks = cast(list[Task | str], pre_tasks)
    pbObj.tasks = cast(list[Task | str], tasks)
    pbObj.post_tasks = cast(list[Task | str], post_tasks)
    pbObj.handlers = cast(list[Task | str], handlers)
    pbObj.roles = cast(list[RoleInPlay | str], roles)
    pbObj.variables = cast(YAMLDict, variables)
    pbObj.vars_files = vars_files
    pbObj.module_defaults = cast(YAMLDict, module_defaults)
    pbObj.options = play_options
    _become = BecomeInfo.from_options(play_options)
    pbObj.become = _become if _become is not None else BecomeInfo()
    pbObj.collections_in_play = collections_in_play
    pbObj.task_loading = cast(YAMLDict, task_loading)

    return pbObj


def load_roleinplay(
    name: str,
    options: dict[str, object],
    defined_in: str,
    role_index: int,
    play_index: int,
    role_name: str = "",
    collection_name: str = "",
    collections_in_play: list[str] | None = None,
    playbook_yaml: str = "",
    basedir: str = "",
) -> RoleInPlay:
    """Load a RoleInPlay from a play's roles block entry.

    Args:
        name: Role name or FQCN.
        options: Role options dict (vars, tags, etc.).
        defined_in: Path to the playbook file.
        role_index: Index of role in the play's roles list.
        play_index: Index of the play.
        role_name: Parent role name if nested.
        collection_name: Parent collection name.
        collections_in_play: Collections declared in the play.
        playbook_yaml: Unused; kept for API compatibility.
        basedir: Base directory for path resolution.

    Returns:
        RoleInPlay instance.
    """
    if collections_in_play is None:
        collections_in_play = []
    ripObj = RoleInPlay()
    if name == "" and "name" in options:
        name_val = options.pop("name", None)
        name = str(name_val) if name_val is not None else ""
    ripObj.name = name
    ripObj.options = cast(YAMLDict, options)
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    ripObj.defined_in = defined_in
    ripObj.role = role_name
    ripObj.collection = collection_name
    ripObj.role_index = role_index
    ripObj.play_index = play_index
    ripObj.collections_in_play = collections_in_play

    return ripObj


def load_playbook(
    path: str = "",
    yaml_str: str = "",
    role_name: str = "",
    collection_name: str = "",
    basedir: str = "",
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
) -> Playbook:
    """Load a playbook file or YAML string into a Playbook object.

    Args:
        path: Path to the playbook file.
        yaml_str: Raw YAML string (alternative to path).
        role_name: Role name if playbook is in a role.
        collection_name: Collection name if playbook is in a collection.
        basedir: Base directory for path resolution.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.

    Returns:
        Playbook object with plays.

    Raises:
        ValueError: If file not found or path is invalid.
        PlaybookFormatError: If YAML is malformed and skip_playbook_format_error is False.
    """
    pbObj = Playbook()
    fullpath = ""
    if yaml_str:
        fullpath = path
    else:
        if os.path.exists(path) and path != "" and path != ".":
            fullpath = path
        if os.path.exists(os.path.join(basedir, path)):
            fullpath = os.path.normpath(os.path.join(basedir, path))
        if fullpath == "":
            raise ValueError("file not found")
    defined_in = fullpath
    if basedir and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    pbObj.defined_in = defined_in
    pbObj.name = os.path.basename(fullpath)
    pbObj.role = role_name
    pbObj.collection = collection_name
    pbObj.set_key()
    yaml_lines = ""
    data = None
    if yaml_str:
        try:
            yaml_lines = yaml_str
            data = yaml.load(yaml_lines, Loader=Loader)
        except Exception as e:
            if skip_playbook_format_error:
                logger.debug(f"failed to load this yaml string to load playbook, skip this yaml; {e}")
            else:
                raise PlaybookFormatError(f"failed to load this yaml string to load playbook; {e}") from e
    elif fullpath != "":
        with open(fullpath) as file:
            try:
                yaml_lines = file.read()
                data = yaml.load(yaml_lines, Loader=Loader)
            except Exception as e:
                if skip_playbook_format_error:
                    logger.debug(f"failed to load this yaml file to load playbook, skip this yaml; {e}")
                else:
                    raise PlaybookFormatError(f"failed to load this yaml file to load playbook; {e}") from e
    if data is None:
        return pbObj
    if not isinstance(data, list):
        raise PlaybookFormatError(f"playbook must be loaded as a list, but got {type(data).__name__}")

    if yaml_lines:
        pbObj.yaml_lines = yaml_lines

    plays = []
    for i, play_dict in enumerate(data):
        try:
            play = load_play(
                path=defined_in,
                index=i,
                play_block_dict=play_dict,
                role_name=role_name,
                collection_name=collection_name,
                parent_key=pbObj.key,
                parent_local_key=pbObj.local_key,
                yaml_lines=yaml_str,
                basedir=basedir,
                skip_task_format_error=skip_task_format_error,
            )
            plays.append(play)
        except PlaybookFormatError as err:
            if skip_playbook_format_error:
                logger.debug(f"this play is wrong format; skip the play in {fullpath}, index: {i}, skip this play")
            else:
                raise PlaybookFormatError(
                    f"this play is wrong format; skip the play in {fullpath}, index: {i}"
                ) from err
        except Exception:
            logger.exception(f"error while loading the play at {fullpath} (index={i})")
    pbObj.plays = cast(list[Play | str], plays)

    return pbObj


def load_playbooks(
    path: str,
    basedir: str = "",
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
    include_test_contents: bool = False,
    yaml_label_list: list[tuple[str, str, YAMLValue]] | None = None,
    load_children: bool = True,
) -> list[Playbook | str]:
    """Load all playbook files in a directory.

    Args:
        path: Path to search for playbooks.
        basedir: Base directory for resolving paths.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        include_test_contents: Whether to include tests/molecule playbooks.
        yaml_label_list: Pre-computed list of (path, label, role_info).
        load_children: Whether to load full Playbook objects or just paths.

    Returns:
        List of Playbook objects or paths.

    Raises:
        PlaybookFormatError: If a playbook file is malformed and skip_playbook_format_error is False.
    """
    if path == "":
        return []
    patterns = [
        os.path.join(path, "*.ya?ml"),
        os.path.join(path, "playbooks/**/*.ya?ml"),
    ]
    if include_test_contents:
        patterns.append(os.path.join(path, "tests/**/*.ya?ml"))
        patterns.append(os.path.join(path, "molecule/**/*.ya?ml"))
    glob_results = safe_glob(patterns, recursive=True)
    candidates_list: list[tuple[str, bool]] = [(c, False) for c in glob_results]

    # add files if yaml_label_list is given
    if yaml_label_list:
        for fpath, label, role_info in yaml_label_list:
            if label == "playbook":
                # if it is a playbook in role, it should be scanned by load_role
                if role_info:
                    continue

                _fpath = fpath
                if not _fpath.startswith(basedir):
                    _fpath = os.path.join(basedir, fpath)
                if not any(c[0] == _fpath for c in candidates_list):
                    candidates_list.append((_fpath, True))

    playbooks: list[Playbook | str] = []
    playbook_names = []
    candidates_list = sorted(candidates_list, key=lambda x: x[0])
    loaded: set[str] = set()
    for fpath, from_list in candidates_list:
        if fpath in loaded:
            continue

        if could_be_playbook(fpath=fpath) and could_be_playbook_detail(fpath=fpath):
            relative_path = ""
            if fpath.startswith(path):
                relative_path = fpath[len(path) :]
            if "/roles/" in relative_path and not from_list and not include_test_contents:
                continue
            p = None
            try:
                p = load_playbook(
                    path=fpath,
                    basedir=basedir,
                    skip_playbook_format_error=skip_playbook_format_error,
                    skip_task_format_error=skip_task_format_error,
                )
            except PlaybookFormatError as e:
                if skip_playbook_format_error:
                    logger.debug(
                        f"this file is not in a playbook format, maybe not a playbook file, skip this: {e.args[0]}"
                    )
                    continue
                else:
                    raise PlaybookFormatError(
                        f"this file is not in a playbook format, maybe not a playbook file: {e.args[0]}"
                    ) from e
            except Exception:
                logger.exception(f"error while loading the playbook at {fpath}")
            if p:
                if load_children:
                    playbooks.append(p)
                    playbook_names.append(p.defined_in)
                else:
                    playbooks.append(p.defined_in)
                    playbook_names.append(p.defined_in)
                loaded.add(fpath)
    if not load_children:
        playbooks = sorted(playbooks)  # type: ignore[type-var]
    return playbooks


def load_role(
    path: str,
    name: str = "",
    collection_name: str = "",
    module_dir_paths: list[str] | None = None,
    basedir: str = "",
    use_ansible_doc: bool = True,
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
    include_test_contents: bool = False,
    load_children: bool = True,
) -> Role:
    """Load an Ansible role directory into a Role object.

    Args:
        path: Path to the role directory.
        name: Override role name (default from directory name).
        collection_name: Collection name if role is in a collection.
        module_dir_paths: Additional paths to search for modules.
        basedir: Base directory for resolving paths.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        include_test_contents: Whether to include test playbooks.
        load_children: Whether to load full child objects or just paths.

    Returns:
        Role object with playbooks, taskfiles, modules, and variables.

    Raises:
        ValueError: If role directory not found or path is invalid.
        TaskFormatError: If skip_task_format_error is False and a task is malformed.
    """
    if module_dir_paths is None:
        module_dir_paths = []
    roleObj = Role()
    fullpath = ""
    if os.path.exists(path) and path != "" and path != ".":
        fullpath = path
    if os.path.exists(os.path.join(basedir, path)):
        fullpath = os.path.normpath(os.path.join(basedir, path))
    if fullpath == "":
        raise ValueError(f"directory not found: {path}, {basedir}")
    else:
        # some roles can be found at "/path/to/role.name/role.name"
        # especially when the role has dependency roles
        # so we try it here
        basename = os.path.basename(fullpath)
        tmp_fullpath = os.path.join(fullpath, basename)
        if os.path.exists(tmp_fullpath):
            fullpath = tmp_fullpath
    meta_file_path = ""
    defaults_dir_path = ""
    vars_dir_path = ""
    tasks_dir_path = ""
    handlers_dir_path = ""
    includes_dir_path = ""
    if fullpath != "":
        meta_file_path = os.path.join(fullpath, "meta/main.yml")
        defaults_dir_path = os.path.join(fullpath, "defaults")
        vars_dir_path = os.path.join(fullpath, "vars")
        tasks_dir_path = os.path.join(fullpath, "tasks")
        tests_dir_path = os.path.join(fullpath, "tests")
        handlers_dir_path = os.path.join(fullpath, "handlers")
        includes_dir_path = os.path.join(fullpath, "includes")
    if os.path.exists(meta_file_path):
        with open(meta_file_path) as file:
            try:
                roleObj.metadata = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load this yaml file to raed metadata; {e.args[0]}")

            if roleObj.metadata is not None and isinstance(roleObj.metadata, dict):
                roleObj.dependency["roles"] = roleObj.metadata.get("dependencies", [])
                roleObj.dependency["collections"] = roleObj.metadata.get("collections", [])

    requirements_yml_path = os.path.join(fullpath, "requirements.yml")
    if os.path.exists(requirements_yml_path):
        with open(requirements_yml_path) as file:
            try:
                roleObj.requirements = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load requirements.yml; {e.args[0]}")

    parts = tasks_dir_path.split("/")
    if len(parts) < 2:
        raise ValueError("role path is wrong")
    role_name = parts[-2] if name == "" else name
    roleObj.name = role_name
    defined_in = fullpath
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    roleObj.defined_in = defined_in
    is_test = is_test_object(defined_in)

    collection = ""
    fqcn = role_name
    if collection_name != "" and not is_test:
        collection = collection_name
        fqcn = f"{collection_name}.{role_name}"
    roleObj.collection = collection
    roleObj.fqcn = fqcn
    roleObj.set_key()

    playbooks = load_playbooks(
        path=fullpath,
        basedir=basedir,
        skip_playbook_format_error=skip_playbook_format_error,
        skip_task_format_error=skip_task_format_error,
        include_test_contents=include_test_contents,
        load_children=load_children,
    )
    roleObj.playbooks = playbooks

    if os.path.exists(defaults_dir_path):
        patterns = [
            defaults_dir_path + "/**/*.ya?ml",
        ]
        defaults_yaml_files = safe_glob(patterns, recursive=True)
        default_variables = {}
        for fpath in defaults_yaml_files:
            with open(fpath) as file:
                try:
                    vars_in_yaml = yaml.load(file, Loader=Loader)
                    if vars_in_yaml is None:
                        continue
                    if not isinstance(vars_in_yaml, dict):
                        continue
                    default_variables.update(vars_in_yaml)
                except Exception as e:
                    logger.debug(f"failed to load this yaml file to read default variables; {e.args[0]}")
        roleObj.default_variables = cast(YAMLDict, default_variables)

    if os.path.exists(vars_dir_path):
        patterns = [vars_dir_path + "/**/*.ya?ml"]
        vars_yaml_files = safe_glob(patterns, recursive=True)
        variables = {}
        for fpath in vars_yaml_files:
            with open(fpath) as file:
                try:
                    vars_in_yaml = yaml.load(file, Loader=Loader)
                    if vars_in_yaml is None:
                        continue
                    if not isinstance(vars_in_yaml, dict):
                        continue
                    variables.update(vars_in_yaml)
                except Exception as e:
                    logger.debug(f"failed to load this yaml file to read variables; {e.args[0]}")
        roleObj.variables = variables

    modules: list[Module | str] = []
    module_files = search_module_files(fullpath, module_dir_paths)

    if not load_children:
        use_ansible_doc = False

    module_specs: dict[str, dict[str, object]] | None = None
    if use_ansible_doc:
        module_specs = get_module_specs_by_ansible_doc(
            module_files=module_files,
            fqcn_prefix=collection_name,
            search_path=fullpath,
        )

    for module_file_path in module_files:
        m = None
        try:
            m = load_module(
                module_file_path,
                collection_name=collection_name,
                role_name=fqcn,
                basedir=basedir,
                use_ansible_doc=use_ansible_doc,
                module_specs=module_specs,
            )
        except Exception:
            logger.exception(f"error while loading the module at {module_file_path}")
        if m is not None:
            if load_children:
                modules.append(m)
            else:
                modules.append(m.defined_in)
    if not load_children:
        modules = sorted(modules)  # type: ignore[type-var]
    roleObj.modules = modules

    patterns = [tasks_dir_path + "/**/*.ya?ml"]
    # ansible.network collection has this type of another taskfile directory
    if os.path.exists(includes_dir_path):
        patterns.extend([includes_dir_path + "/**/*.ya?ml"])
    if include_test_contents:
        patterns.extend([tests_dir_path + "/**/*.ya?ml"])
    task_yaml_files = safe_glob(patterns, recursive=True)

    taskfiles: list[TaskFile | str] = []
    for task_yaml_path in task_yaml_files:
        tf = None
        if not could_be_taskfile(fpath=task_yaml_path):
            continue
        if could_be_playbook_detail(fpath=task_yaml_path):
            continue
        try:
            tf = load_taskfile(
                task_yaml_path,
                role_name=fqcn,
                collection_name=collection_name,
                basedir=basedir,
                skip_task_format_error=skip_task_format_error,
            )
        except TaskFormatError as e:
            if skip_task_format_error:
                logger.debug(f"Task format error found; skip this taskfile {task_yaml_path}")
            else:
                raise TaskFormatError(f"Task format error found: {e.args[0]}") from e
        except Exception:
            logger.exception(f"error while loading the task file at {task_yaml_path}")
        if not tf:
            continue
        if load_children:
            taskfiles.append(tf)
        else:
            taskfiles.append(tf.defined_in)
    if not load_children:
        taskfiles = sorted(taskfiles)  # type: ignore[type-var]
    roleObj.taskfiles = taskfiles

    if os.path.exists(handlers_dir_path):
        handler_patterns = [handlers_dir_path + "/**/*.ya?ml"]
        handler_files = safe_glob(handler_patterns, recursive=True)

        handlers: list[TaskFile | str] = []
        for handler_yaml_path in handler_files:
            tf = None
            try:
                tf = load_taskfile(
                    handler_yaml_path,
                    role_name=fqcn,
                    collection_name=collection_name,
                    basedir=basedir,
                    skip_task_format_error=skip_task_format_error,
                )
            except TaskFormatError as e:
                if skip_task_format_error:
                    logger.debug(f"Task format error found; skip this taskfile {task_yaml_path}")
                else:
                    raise TaskFormatError(f"Task format error found: {e.args[0]}") from e
            except Exception:
                logger.exception(f"error while loading the task file at {task_yaml_path}")
            if not tf:
                continue
            if load_children:
                handlers.append(tf)
            else:
                handlers.append(tf.defined_in)
        if not load_children:
            handlers = sorted(handlers)  # type: ignore[type-var]
        roleObj.handlers = cast(list[Task], handlers)

    return roleObj


def load_roles(
    path: str,
    basedir: str = "",
    use_ansible_doc: bool = True,
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
    include_test_contents: bool = False,
    yaml_label_list: list[tuple[str, str, YAMLValue]] | None = None,
    load_children: bool = True,
) -> list[Role | str]:
    """Load all roles from a repository path.

    Args:
        path: Path to search for roles (e.g. roles/, playbooks/roles/).
        basedir: Base directory for resolving paths.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        include_test_contents: Whether to include test playbooks.
        yaml_label_list: Pre-computed list of (path, label, role_info).
        load_children: Whether to load full Role objects or just paths.

    Returns:
        List of Role objects or paths.
    """
    if path == "":
        return []
    roles_patterns = ["roles", "playbooks/roles", "playbook/roles"]
    roles_dir_path = ""
    for r_p in roles_patterns:
        candidate = os.path.join(path, r_p)
        if os.path.exists(candidate):
            roles_dir_path = candidate
            break
    if not roles_dir_path:
        pattern = os.path.join(path, "**", "roles")
        found_roles = safe_glob(pattern, recursive=True)
        found_roles = [r for r in found_roles if r.endswith("/roles")]
        if found_roles:
            roles_dir_path = found_roles[0]

    def is_role_dir(found_dirs: list[str]) -> bool:
        """Check if a directory contains role structure (tasks, handlers, etc.).

        Args:
            found_dirs: List of subdirectory names in the candidate path.

        Returns:
            True if at least one role dir (tasks, handlers, templates, etc.) exists.
        """
        # From ansible role doc
        # if none of the following dirs are found, we don't treat it as a role dir
        role_dir_patterns = set(
            [
                "tasks",
                "handlers",
                "templates",
                "files",
                "vars",
                "defaults",
                "meta",
                "library",
                "module_utils",
                "lookup_plugins",
            ]
        )
        return len(role_dir_patterns.intersection(set(found_dirs))) > 0

    role_dirs = []
    if roles_dir_path:
        dirs = sorted(os.listdir(roles_dir_path))
        for dir_name in dirs:
            candidate = os.path.join(roles_dir_path, dir_name)
            dirs_in_cand = os.listdir(candidate)
            if is_role_dir(dirs_in_cand):
                role_dirs.append(candidate)

    if include_test_contents:
        test_targets_dir = os.path.join(path, "tests/integration/targets")
        if os.path.exists(test_targets_dir):
            test_names = os.listdir(test_targets_dir)
            for test_name in test_names:
                test_dir = os.path.join(test_targets_dir, test_name)
                test_tasks_dir = os.path.join(test_dir, "tasks")
                test_sub_roles_dir = os.path.join(test_dir, "roles")
                if os.path.exists(test_tasks_dir):
                    role_dirs.append(test_dir)
                elif os.path.exists(test_sub_roles_dir):
                    test_sub_role_names = os.listdir(test_sub_roles_dir)
                    for test_sub_role_name in test_sub_role_names:
                        test_sub_role_dir = os.path.join(test_sub_roles_dir, test_sub_role_name)
                        role_dirs.append(test_sub_role_dir)

    # add role dirs if yaml_label_list is given
    if yaml_label_list:
        for _fpath, _label, role_info in yaml_label_list:
            if role_info and isinstance(role_info, dict):
                role_path_val = role_info.get("path", "")
                role_path = str(role_path_val) if role_path_val is not None else ""
                _role_path = role_path
                if not _role_path.startswith(path):
                    _role_path = os.path.join(path, _role_path)
                if _role_path not in role_dirs:
                    role_dirs.append(str(_role_path))

    if not role_dirs:
        return []

    roles: list[Role | str] = []
    for role_dir in role_dirs:
        try:
            r = load_role(
                path=role_dir,
                basedir=basedir,
                use_ansible_doc=use_ansible_doc,
                skip_playbook_format_error=skip_playbook_format_error,
                skip_task_format_error=skip_task_format_error,
                include_test_contents=include_test_contents,
            )
        except Exception:
            logger.exception(f"error while loading the role at {role_dir}")
        if load_children:
            roles.append(r)
        else:
            roles.append(r.defined_in)
    if not load_children:
        roles = sorted(roles)  # type: ignore[type-var]
    return roles


def load_requirements(path: str) -> dict[str, object]:
    """Load requirements.yml from a project or role directory.

    Args:
        path: Path to the directory containing requirements.yml.

    Returns:
        Parsed requirements dict (roles, collections, etc.).
    """
    requirements = {}
    requirements_yml_path = os.path.join(path, "requirements.yml")
    if os.path.exists(requirements_yml_path):
        with open(requirements_yml_path) as file:
            try:
                requirements = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load requirements.yml; {e.args[0]}")
    return requirements


def load_installed_roles(installed_roles_path: str) -> list[Role]:
    """Load all installed Ansible roles from a directory.

    Args:
        installed_roles_path: Path to the roles directory (e.g. ~/.ansible/roles).

    Returns:
        List of Role objects.
    """
    search_path = installed_roles_path
    if installed_roles_path == "" or not os.path.exists(search_path):
        return []
    dirs = os.listdir(search_path)
    roles = []
    basedir = os.path.dirname(os.path.normpath(installed_roles_path))
    for d in dirs:
        role_path = os.path.join(installed_roles_path, d)
        role_meta_files = safe_glob(role_path + "/**/meta/main.ya?ml", recursive=True)

        roles_root_dirs = set([f.split("/roles/")[-2] for f in role_meta_files if "/roles/" in f])
        module_dirs = []
        for role_root_dir in roles_root_dirs:
            moddirs = find_module_dirs(role_root_dir)
            module_dirs.extend(moddirs)

        for i, role_meta_file in enumerate(role_meta_files):
            role_dir_path = role_meta_file.replace("/meta/main.yml", "").replace("/meta/main.yaml", "")
            module_dir_paths = []
            if i == 0:
                module_dir_paths = module_dirs
            try:
                r = load_role(
                    role_dir_path,
                    module_dir_paths=module_dir_paths,
                    basedir=basedir,
                )
                roles.append(r)
            except Exception:
                logger.exception(f"error while loading the role at {role_dir_path}")
    return roles


def load_module(
    module_file_path: str,
    collection_name: str = "",
    role_name: str = "",
    basedir: str = "",
    use_ansible_doc: bool = True,
    module_specs: dict[str, dict[str, object]] | None = None,
) -> Module:
    """Load a Python module file into a Module object.

    Args:
        module_file_path: Path to the module .py file.
        collection_name: Collection name if module is in a collection.
        role_name: Role name if module is in a role's library.
        basedir: Base directory for resolving paths.
        use_ansible_doc: Whether to use ansible-doc for argument specs.
        module_specs: Pre-fetched module specs from ansible-doc.

    Returns:
        Module object with name, FQCN, arguments, and documentation.

    Raises:
        ValueError: If module path is empty or file not found.
    """
    if module_specs is None:
        module_specs = {}
    moduleObj = Module()
    if module_file_path == "":
        raise ValueError("require module file path to load a Module")
    fullpath = ""
    if os.path.exists(module_file_path) and module_file_path != "" and module_file_path != ".":
        fullpath = module_file_path
    if os.path.exists(os.path.join(basedir, module_file_path)):
        fullpath = os.path.normpath(os.path.join(basedir, module_file_path))
    if fullpath == "":
        raise ValueError(f"module file not found: {module_file_path}, {basedir}")

    file_name = os.path.basename(module_file_path)
    module_name = file_name.replace(".py", "")

    # some collections have modules like `plugins/modules/xxxx/yyyy.py`
    # so try finding `xxxx` part by checking module file path
    for dir_pattern in module_dir_patterns:
        separator = dir_pattern + "/"
        if separator in module_file_path:
            module_name = module_file_path.split(separator)[-1].replace(".py", "").replace("/", ".")
            break

    moduleObj.name = module_name
    if collection_name != "":
        moduleObj.collection = collection_name
        moduleObj.fqcn = f"{collection_name}.{module_name}"
    elif role_name != "":
        # if module is defined in a role, it does not have real fqcn
        moduleObj.role = role_name
        moduleObj.fqcn = module_name
    defined_in = module_file_path
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    moduleObj.defined_in = defined_in

    arguments = []
    doc_yaml = ""
    examples = ""
    if use_ansible_doc:
        # running `ansible-doc` for each module causes speed problem due to overhead,
        # so use it for all modules and pick up the doc for the module here
        if module_specs:
            spec = module_specs.get(moduleObj.fqcn, {})
            if isinstance(spec, dict):
                doc_val = spec.get("doc", "")
                doc_yaml = str(doc_val) if doc_val is not None else ""
                ex_val = spec.get("examples", "")
                examples = str(ex_val) if ex_val is not None else ""
            else:
                doc_yaml = ""
                examples = ""
    else:
        # parse the script file for a quick scan (this does not contain doc from `doc_fragments`)
        doc_yaml = get_documentation_in_module_file(fullpath)
    if doc_yaml:
        doc_dict_raw: object = None
        try:
            doc_dict_raw = yaml.load(doc_yaml, Loader=Loader)
        except Exception:
            logger.debug(f"failed to load the arguments documentation of the module: {module_name}")
        doc_dict = doc_dict_raw if isinstance(doc_dict_raw, dict) else {}
        arg_specs = doc_dict.get("options", {})
        if isinstance(arg_specs, dict):
            for arg_name in arg_specs:
                arg_spec = arg_specs[arg_name]
                if not isinstance(arg_spec, dict):
                    continue
                _arg_type = arg_spec.get("type")
                arg_value_type = get_class_by_arg_type(str(_arg_type) if _arg_type is not None else "")
                arg_value_type_str = ""
                if arg_value_type:
                    arg_value_type_str = arg_value_type.__name__

                _arg_elements = arg_spec.get("elements")
                arg_elements_type = get_class_by_arg_type(str(_arg_elements) if _arg_elements is not None else "")
                arg_elements_type_str = ""
                if arg_elements_type:
                    arg_elements_type_str = arg_elements_type.__name__
                required: bool | None = None
                with contextlib.suppress(Exception):
                    required = parse_bool(arg_spec.get("required", "false"))
                _choices = arg_spec.get("choices")
                _aliases = arg_spec.get("aliases")
                arg = ModuleArgument(
                    name=arg_name,
                    type=arg_value_type_str,
                    elements=arg_elements_type_str,
                    required=required if required is not None else False,
                    description=arg_spec.get("description", ""),
                    default=arg_spec.get("default"),
                    choices=_choices if isinstance(_choices, list) else [],
                    aliases=_aliases if isinstance(_aliases, list) else [],
                )
                arguments.append(arg)
    moduleObj.documentation = doc_yaml
    moduleObj.examples = examples
    moduleObj.arguments = arguments

    moduleObj.set_key()

    return moduleObj


builtin_modules_file_name = "ansible_builtin_modules.json"
builtin_modules: dict[str, Module] = {}


def load_builtin_modules() -> dict[str, Module]:
    """Load built-in Ansible modules from bundled JSON.

    Returns:
        Dict mapping module name to Module object.
    """
    global builtin_modules
    if builtin_modules:
        return builtin_modules
    base_path = os.path.dirname(__file__)
    data_path = os.path.join(base_path, builtin_modules_file_name)
    module_list = ObjectList.from_json(fpath=data_path)
    builtin_modules = {m.name: m for m in module_list.items if isinstance(m, Module)}
    return builtin_modules


# modules in a SCM repo should be in `library` dir in the best practice case
# https://docs.ansible.com/ansible/2.8/user_guide/playbooks_best_practices.html
# however, it is often defined in `plugins/modules` directory,
# so we search both the directories
def load_modules(
    path: str,
    basedir: str = "",
    collection_name: str = "",
    module_dir_paths: list[str] | None = None,
    use_ansible_doc: bool = True,
    load_children: bool = True,
) -> list[Module | str]:
    """Load all custom modules in a repository or collection.

    Args:
        path: Path to search for modules (library, plugins/modules).
        basedir: Base directory for resolving paths.
        collection_name: Collection name for FQCN.
        module_dir_paths: Additional paths to search for modules.
        use_ansible_doc: Whether to use ansible-doc for specs.
        load_children: Whether to load full Module objects or just paths.

    Returns:
        List of Module objects or paths.
    """
    if module_dir_paths is None:
        module_dir_paths = []
    if path == "":
        return []
    if not os.path.exists(path):
        return []
    module_files = search_module_files(path, module_dir_paths)

    if len(module_files) == 0:
        return []

    if not load_children:
        use_ansible_doc = False

    module_specs: dict[str, dict[str, object]] | None = None
    if use_ansible_doc:
        module_specs = get_module_specs_by_ansible_doc(
            module_files=module_files,
            fqcn_prefix=collection_name,
            search_path=path,
        )

    modules: list[Module | str] = []
    for module_file_path in module_files:
        m = None
        try:
            m = load_module(
                module_file_path,
                collection_name=collection_name,
                basedir=basedir,
                use_ansible_doc=use_ansible_doc,
                module_specs=module_specs,
            )
        except Exception:
            logger.exception(f"error while loading the module at {module_file_path}")
        if m is not None:
            if load_children:
                modules.append(m)
            else:
                modules.append(m.defined_in)
    if not load_children:
        modules = sorted(modules)  # type: ignore[type-var]
    return modules


_BLOCK_SECTIONS = ("block", "rescue", "always")


def _load_block_children(
    task: Task,
    data_block: dict[str, object],
    *,
    path: str,
    role_name: str,
    collection_name: str,
    collections_in_play: list[str] | None,
    play_index: int,
    parent_key: str,
    parent_local_key: str,
    yaml_lines: str,
    basedir: str,
) -> None:
    """Recursively load block/rescue/always children into *task.options*.

    If *data_block* contains ``block``, ``rescue``, or ``always`` keys
    whose values are lists of dicts, each child dict is loaded via
    ``load_task()`` and the resulting ``Task`` objects replace the raw
    dicts in ``task.options``.  The block task's ``module`` is cleared
    so ``GraphBuilder`` classifies it as ``NodeType.BLOCK``.

    Args:
        task: The parent block Task being constructed.
        data_block: Original YAML dict (may contain block/rescue/always).
        path: File path forwarded to child ``load_task`` calls.
        role_name: Role name forwarded to children.
        collection_name: Collection name forwarded to children.
        collections_in_play: Collections list forwarded to children.
        play_index: Play index forwarded to children.
        parent_key: Parent key forwarded to children.
        parent_local_key: Parent local key forwarded to children.
        yaml_lines: YAML source forwarded to children.
        basedir: Base directory forwarded to children.
    """
    has_block = any(isinstance(data_block.get(s), list) for s in _BLOCK_SECTIONS)
    if not has_block:
        return

    task.module = ""
    task.executable = ""
    task.executable_type = ""

    for section in _BLOCK_SECTIONS:
        children_raw = data_block.get(section)
        if not isinstance(children_raw, list):
            continue
        child_tasks: list[Task] = []
        last_child_line = -1
        for i, child_dict in enumerate(children_raw):
            if not isinstance(child_dict, dict):
                continue
            child_jsonpath = f"{task.jsonpath}.{section}.{i}"
            child = load_task(
                path=path,
                index=i,
                task_block_dict=child_dict,
                task_jsonpath=child_jsonpath,
                role_name=role_name,
                collection_name=collection_name,
                collections_in_play=collections_in_play,
                play_index=play_index,
                parent_key=task.key,
                parent_local_key=task.local_key,
                yaml_lines=yaml_lines,
                previous_task_line=last_child_line,
                basedir=basedir,
            )
            child_tasks.append(child)
            if child.line_num_in_file and len(child.line_num_in_file) == 2:
                last_child_line = child.line_num_in_file[1]
        task.options[section] = child_tasks  # type: ignore[assignment]


def load_task(
    path: str,
    index: int,
    task_block_dict: dict[str, object],
    task_jsonpath: str = "",
    role_name: str = "",
    collection_name: str = "",
    collections_in_play: list[str] | None = None,
    play_index: int = -1,
    parent_key: str = "",
    parent_local_key: str = "",
    yaml_lines: str = "",
    previous_task_line: int = -1,
    basedir: str = "",
) -> Task:
    """Load a single task block into a Task object.

    Args:
        path: Path to the playbook or task file.
        index: Index of the task in the task list.
        task_block_dict: Parsed YAML dict for the task block.
        task_jsonpath: JSONPath for the task in the document.
        role_name: Role name if task is in a role.
        collection_name: Collection name if task is in a collection.
        collections_in_play: Collections declared in the play.
        play_index: Index of the play containing this task.
        parent_key: Parent object key for hierarchy.
        parent_local_key: Parent local key for hierarchy.
        yaml_lines: Raw YAML content for line number lookup.
        previous_task_line: Line number of previous task for context.
        basedir: Base directory for path normalization.

    Returns:
        Task object with module, options, variables, and loop info.

    Raises:
        ValueError: If file not found or task_block_dict is None.
        TaskFormatError: If task block is not a valid dict.
    """
    if collections_in_play is None:
        collections_in_play = []
    taskObj = Task()
    fullpath = ""
    if yaml_lines:
        fullpath = path
    else:
        if os.path.exists(path) and path != "" and path != ".":
            fullpath = path
        if os.path.exists(os.path.join(basedir, path)):
            fullpath = os.path.normpath(os.path.join(basedir, path))
        if fullpath == "":
            raise ValueError("file not found")
        if not fullpath.endswith(".yml") and not fullpath.endswith(".yaml"):
            raise ValueError('task yaml file must be ".yml" or ".yaml"')
    if task_block_dict is None:
        raise ValueError("task block dict is required to load Task")
    if not isinstance(task_block_dict, dict):
        raise TaskFormatError(
            f"this task block is not a dict, but {type(task_block_dict).__name__}; maybe this is not a task"
        )
    data_block = task_block_dict
    task_name = ""
    module_name = find_module_name(cast(YAMLDict, task_block_dict))
    module_short_name = module_name.split(".")[-1]
    task_options: YAMLDict = {}
    module_options: YAMLDict | str = {}
    for k, v in data_block.items():
        if k == "name":
            task_name = str(v) if v is not None else ""
        if k == module_name:
            if isinstance(v, dict):
                module_options = cast(YAMLDict, v)
            elif isinstance(v, str):
                module_options = v
            else:
                module_options = str(v) if v is not None else ""
        elif k == "local_action":
            _opt = data_block[k]
            if isinstance(_opt, str):
                module_options = _opt.lstrip(module_name).lstrip(" ")
            elif isinstance(_opt, dict):
                _mod_opts: YAMLDict = {}
                for mk, mv in _opt.items():
                    if mk == "module":
                        continue
                    _mod_opts[mk] = mv
                module_options = _mod_opts
            task_options.update({k: cast(YAMLValue, v)})
        else:
            task_options.update({k: cast(YAMLValue, v)})

    taskObj.jsonpath = task_jsonpath
    taskObj.set_yaml_lines(
        fullpath=fullpath,
        yaml_lines=yaml_lines,
        task_name=task_name,
        module_name=module_name,
        module_options=module_options,
        task_options=task_options,
        previous_task_line=previous_task_line,
        jsonpath=task_jsonpath,
    )

    # module_options can be passed as a string like below
    #
    # - name: sample of string module options
    #   ufw: port={{ item }} proto=tcp rule=allow
    #   with_items:
    #   - 5222
    #   - 5269
    if isinstance(module_options, str) and string_module_options_re.match(module_options):
        new_module_options = {}
        unknown_key = "__unknown_option_name__"
        if module_short_name in ["import_role", "include_role"]:
            unknown_key = "name"
        elif module_short_name in [
            "import_tasks",
            "include_tasks",
            "include",
        ]:
            unknown_key = "file"
        matched_options = string_module_option_parts_re.findall(module_options)
        if len(matched_options) == 0:
            new_module_options[unknown_key] = module_options
        else:
            unknown_key_val = module_options.split(matched_options[0])[0]
            if unknown_key_val != "":
                new_module_options[unknown_key] = unknown_key_val.strip()
            for p in matched_options:
                key = p.split("=")[0]
                val = "=".join(p.split("=")[1:]).rstrip()
                new_module_options[key] = val
        module_options = cast(YAMLDict, new_module_options)
    executable = module_name
    executable_type = ExecutableType.MODULE_TYPE
    if module_short_name in ["import_role", "include_role"]:
        role_ref = ""
        if isinstance(module_options, str):
            role_ref = module_options
        elif isinstance(module_options, dict):
            rv = module_options.get("name", "")
            role_ref = str(rv) if rv is not None else ""
        executable = role_ref
        executable_type = ExecutableType.ROLE_TYPE
    if module_short_name in ["import_tasks", "include_tasks", "include"]:
        taskfile_ref = ""
        if isinstance(module_options, str):
            taskfile_ref = module_options
        elif isinstance(module_options, dict):
            taskfile_ref = str(module_options.get("file", "") or "")
        executable = taskfile_ref
        executable_type = ExecutableType.TASKFILE_TYPE

    taskObj.name = task_name
    taskObj.role = role_name
    taskObj.collection = collection_name
    defined_in = fullpath
    if basedir and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    taskObj.defined_in = defined_in
    taskObj.index = index
    taskObj.play_index = play_index
    taskObj.executable = executable
    taskObj.executable_type = executable_type
    taskObj.collections_in_play = collections_in_play
    taskObj.set_key(parent_key, parent_local_key)

    variables = {}
    # get variables for this task
    if "vars" in task_options:
        vars_in_task = task_options.get("vars", {})
        if vars_in_task is not None and isinstance(vars_in_task, dict):
            variables.update(vars_in_task)

    module_defaults = {}
    if "module_defaults" in task_options:
        m_default_in_task = task_options.get("module_defaults", {})
        if m_default_in_task and isinstance(m_default_in_task, dict):
            module_defaults.update(m_default_in_task)

    set_facts = {}
    # if the Task is set_fact, set variables too
    if module_short_name == "set_fact" and isinstance(module_options, dict):
        set_facts.update(module_options)

    registered_variables: YAMLDict = {}
    # set variables if this task register a new var
    if "register" in task_options:
        register_var_name = task_options.get("register", "")
        if register_var_name is not None and isinstance(register_var_name, str) and register_var_name != "":
            registered_variables.update({register_var_name: taskObj.key})

    # set loop variables when loop / with_xxxx are there
    loop_info: YAMLDict = {}
    for k in task_options:
        if k in loop_task_option_names:
            loop_control = task_options.get("loop_control", {})
            loop_var = "item"
            if isinstance(loop_control, dict):
                lv = loop_control.get("loop_var", "item")
                loop_var = str(lv) if lv is not None else "item"
            loop_info[loop_var] = task_options.get(k, [])

    taskObj.options = task_options
    taskObj.become = BecomeInfo.from_options(task_options)
    taskObj.variables = variables
    taskObj.module_defaults = module_defaults
    taskObj.registered_variables = registered_variables
    taskObj.set_facts = set_facts
    taskObj.loop = loop_info
    taskObj.module = module_name
    taskObj.module_options = module_options if isinstance(module_options, dict) else {"_raw": module_options}

    _load_block_children(
        taskObj,
        data_block,
        path=path,
        role_name=role_name,
        collection_name=collection_name,
        collections_in_play=collections_in_play,
        play_index=play_index,
        parent_key=parent_key,
        parent_local_key=parent_local_key,
        yaml_lines=yaml_lines,
        basedir=basedir,
    )

    return taskObj


def load_taskfile(
    path: str,
    yaml_str: str = "",
    role_name: str = "",
    collection_name: str = "",
    basedir: str = "",
    skip_task_format_error: bool = True,
) -> TaskFile:
    """Load a task file (tasks/*.yml or includes) into a TaskFile object.

    Args:
        path: Path to the task file.
        yaml_str: Raw YAML string (alternative to path).
        role_name: Role name if taskfile is in a role.
        collection_name: Collection name if taskfile is in a collection.
        basedir: Base directory for resolving paths.
        skip_task_format_error: Whether to skip malformed tasks.

    Returns:
        TaskFile object with tasks.

    Raises:
        ValueError: If file not found or not .yml/.yaml.
        TaskFormatError: If skip_task_format_error is False and a task is malformed.
    """
    tfObj = TaskFile()
    fullpath = ""
    if yaml_str:
        fullpath = path
    else:
        if os.path.exists(path) and path != "" and path != ".":
            fullpath = path
        if os.path.exists(os.path.join(basedir, path)):
            fullpath = os.path.normpath(os.path.join(basedir, path))
        if fullpath == "":
            raise ValueError("file not found")
        if not fullpath.endswith(".yml") and not fullpath.endswith(".yaml"):
            raise ValueError('task yaml file must be ".yml" or ".yaml"')
    tfObj.name = os.path.basename(fullpath)
    defined_in = fullpath
    if basedir != "" and defined_in.startswith(basedir):
        defined_in = defined_in[len(basedir) :]
        if defined_in.startswith("/"):
            defined_in = defined_in[1:]
    tfObj.defined_in = defined_in
    if role_name != "":
        tfObj.role = role_name
    if collection_name != "":
        tfObj.collection = collection_name
    tfObj.set_key()

    task_dicts, yaml_lines = get_task_blocks(fpath=fullpath, yaml_str=yaml_str)

    if yaml_str and not yaml_lines:
        yaml_lines = yaml_str
    tfObj.yaml_lines = yaml_lines or ""

    if task_dicts is None:
        return tfObj
    tasks: list[Task] = []
    task_errors_tf: list[BaseException] = []
    task_loading: dict[str, object] = {
        "total": 0,
        "success": 0,
        "failure": 0,
        "errors": task_errors_tf,
    }
    last_task_line_num: int = -1
    error: BaseException | None = None
    for i, (t_dict, t_jsonpath) in enumerate(task_dicts):
        task_loading["total"] = _safe_int(task_loading.get("total", 0)) + 1
        error = None
        try:
            t = load_task(
                fullpath,
                i,
                cast(dict[str, object], t_dict),
                t_jsonpath,
                role_name,
                collection_name,
                yaml_lines=yaml_lines or "",
                parent_key=tfObj.key,
                parent_local_key=tfObj.local_key,
                previous_task_line=last_task_line_num,
                basedir=basedir,
            )
            tasks.append(t)
            if t:
                task_loading["success"] = _safe_int(task_loading.get("success", 0)) + 1
                if t.line_num_in_file and len(t.line_num_in_file) == 2:
                    last_task_line_num = t.line_num_in_file[1]
        except TaskFormatError as exc:
            error = exc
            if skip_task_format_error:
                logger.debug(f"this task is wrong format; skip the task in {fullpath}, index: {i}; skip this")
                continue
            else:
                raise TaskFormatError(f"Task format error found; {fullpath}, index: {i}") from exc
        except Exception as exc:
            error = exc
            logger.exception(f"error while loading the task at {fullpath}, index: {i}")
        finally:
            if error is not None:
                task_loading["failure"] = _safe_int(task_loading.get("failure", 0)) + 1
                task_errors_tf.append(error)
    tfObj.tasks = cast(list[Task | str], tasks)
    tfObj.task_loading = cast(YAMLDict, task_loading)

    return tfObj


# playbooks possibly include/import task files around the playbook file
# we search this type of isolated taskfile in `playbooks` and `tasks` dir
def load_taskfiles(
    path: str,
    basedir: str = "",
    yaml_label_list: list[tuple[str, str, YAMLValue]] | None = None,
    load_children: bool = True,
) -> list[TaskFile | str]:
    """Load taskfiles from playbooks/tasks directories, optionally filtered by yaml_label_list.

    Args:
        path: Repository root path.
        basedir: Base directory for resolving paths.
        yaml_label_list: Pre-computed (path, label, role_info) for YAML files.
        load_children: Whether to load full TaskFile objects or just paths.

    Returns:
        List of TaskFile objects or paths.
    """
    if not os.path.exists(path):
        return []

    taskfile_paths = search_taskfiles_for_playbooks(path)

    # add files if yaml_label_list is given
    if yaml_label_list:
        for fpath, label, role_info in yaml_label_list:
            if label == "taskfile":
                # if it is a taskfile in role, it should be scanned by load_role
                if role_info:
                    continue

                _fpath = fpath
                if not _fpath.startswith(path):
                    _fpath = os.path.join(path, fpath)
                if _fpath not in taskfile_paths:
                    taskfile_paths.append(_fpath)

    if len(taskfile_paths) == 0:
        return []

    taskfiles: list[TaskFile | str] = []
    for taskfile_path in taskfile_paths:
        try:
            tf = load_taskfile(taskfile_path, basedir=basedir)
        except Exception:
            logger.exception(f"error while loading the task file at {taskfile_path}")
            tf = None
        if tf is not None:
            if load_children:
                taskfiles.append(tf)
            else:
                taskfiles.append(tf.defined_in)
    if not load_children:
        taskfiles = sorted(taskfiles)  # type: ignore[type-var]
    return taskfiles


def load_collection(
    collection_dir: str,
    basedir: str = "",
    use_ansible_doc: bool = True,
    skip_playbook_format_error: bool = True,
    skip_task_format_error: bool = True,
    include_test_contents: bool = False,
    load_children: bool = True,
) -> Collection:
    """Load an Ansible collection directory into a Collection object.

    Args:
        collection_dir: Path to the collection directory.
        basedir: Base directory for resolving paths.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        include_test_contents: Whether to include test playbooks.
        load_children: Whether to load full child objects or just paths.

    Returns:
        Collection object with playbooks, roles, modules, and taskfiles.

    Raises:
        ValueError: If collection directory not found or path is invalid.
    """
    colObj = Collection()
    fullpath = ""
    if os.path.exists(collection_dir):
        fullpath = collection_dir
    if os.path.exists(os.path.join(basedir, collection_dir)):
        fullpath = os.path.join(basedir, collection_dir)
    if fullpath == "":
        raise ValueError("directory not found")
    parts = fullpath.split("/")
    if len(parts) < 2:
        raise ValueError("collection directory path is wrong")
    collection_name = f"{parts[-2]}.{parts[-1]}"

    manifest_file_path = os.path.join(fullpath, "MANIFEST.json")
    if os.path.exists(manifest_file_path):
        with open(manifest_file_path) as file:
            colObj.metadata = json.load(file)

        if colObj.metadata is not None and isinstance(colObj.metadata, dict):
            ci = colObj.metadata.get("collection_info", {})
            deps = ci.get("dependencies", {}) if isinstance(ci, dict) else {}
            colObj.dependency["collections"] = deps

    files_file_path = os.path.join(fullpath, "FILES.json")
    if os.path.exists(files_file_path):
        with open(files_file_path) as file:
            colObj.files = cast(YAMLDict, json.load(file))

    meta_runtime_file_path = os.path.join(fullpath, "meta", "runtime.yml")
    if os.path.exists(meta_runtime_file_path):
        with open(meta_runtime_file_path) as file:
            try:
                colObj.meta_runtime = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load meta/runtime.yml; {e.args[0]}")

    requirements_yml_path = os.path.join(fullpath, "requirements.yml")
    if os.path.exists(requirements_yml_path):
        with open(requirements_yml_path) as file:
            try:
                colObj.requirements = yaml.load(file, Loader=Loader)
            except Exception as e:
                logger.debug(f"failed to load requirements.yml; {e.args[0]}")

    if isinstance(colObj.metadata, dict):
        ci = colObj.metadata.get("collection_info", {})
        license_filename = ci.get("license_file", None) if isinstance(ci, dict) else None
        if license_filename and isinstance(license_filename, str):
            license_filepath = os.path.join(fullpath, license_filename)
            if os.path.exists(license_filepath):
                with open(license_filepath) as file:
                    contents = file.read()
                    lines = contents.splitlines()
                    if len(lines) > 10:
                        contents = "\n".join(lines[:10])
                    colObj.metadata["_ari_added"] = cast(
                        YAMLDict,
                        {"license_file_contents_head": contents},
                    )

    playbooks = load_playbooks(
        path=fullpath,
        basedir=basedir,
        skip_playbook_format_error=skip_playbook_format_error,
        skip_task_format_error=skip_task_format_error,
        include_test_contents=include_test_contents,
        load_children=load_children,
    )

    taskfile_paths = search_taskfiles_for_playbooks(fullpath)
    if len(taskfile_paths) > 0:
        taskfiles: list[TaskFile | str] = []
        for taskfile_path in taskfile_paths:
            try:
                tf = load_taskfile(taskfile_path, basedir=basedir)
            except Exception:
                logger.exception(f"error while loading the task file at {taskfile_path}")
                tf = None
            if tf is not None:
                if load_children:
                    taskfiles.append(tf)
                else:
                    taskfiles.append(tf.defined_in)
        if not load_children:
            taskfiles = sorted(taskfiles)  # type: ignore[type-var]
        colObj.taskfiles = taskfiles

    roles = load_roles(
        path=fullpath,
        basedir=basedir,
        use_ansible_doc=use_ansible_doc,
        include_test_contents=include_test_contents,
        load_children=load_children,
    )

    module_files = search_module_files(fullpath)

    modules: list[Module | str] = []
    if not load_children:
        use_ansible_doc = False

    module_specs: dict[str, dict[str, object]] | None = None
    if use_ansible_doc:
        module_specs = get_module_specs_by_ansible_doc(
            module_files=module_files,
            fqcn_prefix=collection_name,
            search_path=fullpath,
        )

    for f in module_files:
        m = None
        try:
            m = load_module(
                f,
                collection_name=collection_name,
                basedir=basedir,
                use_ansible_doc=use_ansible_doc,
                module_specs=module_specs,
            )
        except Exception:
            logger.exception(f"error while loading the module at {f}")
            continue
        if m is not None:
            if load_children:
                modules.append(m)
            else:
                modules.append(m.defined_in)
    if not load_children:
        modules = sorted(modules)  # type: ignore[type-var]
    colObj.name = collection_name
    path = collection_dir
    if basedir != "" and path.startswith(basedir):
        path = path[len(basedir) :]
    colObj.path = path
    colObj.playbooks = playbooks
    colObj.roles = roles
    colObj.modules = modules

    return colObj


def load_object(loadObj: Load) -> None:
    """Load Ansible content (collection, role, playbook, etc.) and populate Load object.

    Args:
        loadObj: Load object with target_type, path, and options. Populated in place.
    """
    target_type = loadObj.target_type
    path = loadObj.path
    obj: Collection | Role | Playbook | Repository | TaskFile | None = None
    if target_type == LoadType.COLLECTION:
        obj = load_collection(
            collection_dir=path, basedir=path, include_test_contents=loadObj.include_test_contents, load_children=False
        )
    elif target_type == LoadType.ROLE:
        obj = load_role(
            path=path, basedir=path, include_test_contents=loadObj.include_test_contents, load_children=False
        )
    elif target_type == LoadType.PLAYBOOK:
        basedir = ""
        target_playbook_path = ""
        if loadObj.playbook_yaml:
            target_playbook_path = path
        else:
            if loadObj.base_dir:
                basedir = loadObj.base_dir
                target_playbook_path = path.replace(basedir, "")
                if target_playbook_path[0] == "/":
                    target_playbook_path = target_playbook_path[1:]
            else:
                basedir, target_playbook_path = split_target_playbook_fullpath(path)
        if loadObj.playbook_only:
            obj = load_playbook(path=target_playbook_path, yaml_str=loadObj.playbook_yaml, basedir=basedir)
        else:
            obj = load_repository(
                path=basedir, basedir=basedir, target_playbook_path=target_playbook_path, load_children=False
            )
    elif target_type == LoadType.TASKFILE:
        basedir = ""
        target_taskfile_path = ""
        if loadObj.taskfile_yaml:
            target_taskfile_path = path
        else:
            if loadObj.base_dir:
                basedir = loadObj.base_dir
                target_taskfile_path = path.replace(basedir, "")
                if target_taskfile_path[0] == "/":
                    target_taskfile_path = target_taskfile_path[1:]
            else:
                basedir, target_taskfile_path = split_target_taskfile_fullpath(path)
        if loadObj.taskfile_only:
            obj = load_taskfile(path=target_taskfile_path, yaml_str=loadObj.taskfile_yaml, basedir=basedir)
        else:
            obj = load_repository(
                path=basedir, basedir=basedir, target_taskfile_path=target_taskfile_path, load_children=False
            )
    elif target_type == LoadType.PROJECT:
        _yaml_labels = loadObj.yaml_label_list
        obj = load_repository(
            path=path,
            basedir=path,
            include_test_contents=loadObj.include_test_contents,
            yaml_label_list=cast(list[tuple[str, str, YAMLValue]] | None, _yaml_labels if _yaml_labels else None),
            load_children=False,
        )

    if obj is not None:
        if hasattr(obj, "roles"):
            loadObj.roles = getattr(obj, "roles", [])
        if hasattr(obj, "playbooks"):
            loadObj.playbooks = getattr(obj, "playbooks", [])
        if hasattr(obj, "taskfiles"):
            loadObj.taskfiles = getattr(obj, "taskfiles", [])
        if hasattr(obj, "handlers"):
            current = loadObj.taskfiles or []
            loadObj.taskfiles = current + getattr(obj, "handlers", [])
        if hasattr(obj, "modules"):
            loadObj.modules = getattr(obj, "modules", [])
        if hasattr(obj, "files"):
            loadObj.files = getattr(obj, "files", [])

    if target_type == LoadType.ROLE and isinstance(obj, Role):
        loadObj.roles = [obj.defined_in]
    elif target_type == LoadType.PLAYBOOK and loadObj.playbook_only and isinstance(obj, Playbook):
        loadObj.playbooks = [obj.defined_in]
    elif target_type == LoadType.TASKFILE and loadObj.taskfile_only and isinstance(obj, TaskFile):
        loadObj.taskfiles = [obj.defined_in]

    loadObj.timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def find_playbook_role_module(
    path: str, use_ansible_doc: bool = True
) -> tuple[list[Playbook | str], list[str | Role], list[Module | str]]:
    """Load playbooks, roles, and modules from a path without full hierarchy.

    Args:
        path: Repository or project path.
        use_ansible_doc: Whether to use ansible-doc for module specs.

    Returns:
        Tuple of (playbooks, roles, modules). Roles may include "." for root role.
    """
    playbooks = load_playbooks(path, basedir=path, load_children=False)
    root_role = None
    with contextlib.suppress(Exception):
        root_role = load_role(path, basedir=path, use_ansible_doc=use_ansible_doc, load_children=False)
    sub_roles = load_roles(path, basedir=path, use_ansible_doc=use_ansible_doc, load_children=False)
    roles: list[str | Role] = []
    if root_role and root_role.metadata:
        roles.append(".")
    if len(sub_roles) > 0:
        roles.extend(sub_roles)
    modules = load_modules(path, basedir=path, use_ansible_doc=use_ansible_doc, load_children=False)
    return playbooks, roles, modules
