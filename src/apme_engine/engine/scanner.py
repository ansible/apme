"""ARI scanner: evaluates collections, roles, playbooks, and taskfiles against rules."""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import jsonpickle
import yaml

from . import logger
from .analyzer import analyze
from .annotators.variable_resolver import resolve_variables
from .dependency_dir_preparator import (
    DependencyDirPreparator,
)
from .findings import Findings
from .keyutil import detect_type as key_detect_type
from .loader import (
    get_loader_version,
)
from .model_loader import load_object
from .models import (
    Annotation,
    AnsibleRunContext,
    ARIResult,
    Load,
    LoadType,
    Location,
    Object,
    ObjectList,
    RiskAnnotation,
    Rule,
    RunTarget,
    TaskCall,
    TaskCallsInTree,
    YAMLDict,
    YAMLList,
    YAMLValue,
)
from .parser import Parser
from .risk_assessment_model import RAMClient
from .tree import TreeLoader
from .utils import (
    equal,
    escape_local_path,
    escape_url,
    is_local_path,
    is_url,
    split_target_playbook_fullpath,
    split_target_taskfile_fullpath,
    summarize_findings,
)

ARI_CONFIG_PATH = os.getenv("ARI_CONFIG_PATH")
default_config_path = ARI_CONFIG_PATH or os.path.expanduser("~/.ari/config")
default_data_dir = os.path.join("/tmp", "ari-data")
default_rules_dir = os.path.join(os.path.dirname(__file__), "rules")
default_log_level = "info"
default_rules: list[str] = []
default_disable_default_rules = False
default_logger_key = "ari"


@dataclass
class Config:
    """ARI scanner configuration loaded from file and environment.

    Attributes:
        path: Path to the config file.
        data_dir: Directory for ARI data (collections, roles, etc.).
        rules_dir: Directory containing rule definitions.
        logger_key: Logger channel identifier.
        log_level: Logging level (e.g., info, debug).
        rules: List of rule IDs or paths to enable.
        disable_default_rules: If True, do not load default rules from rules_dir.

    """

    path: str = ""

    data_dir: str = ""
    rules_dir: str = ""
    logger_key: str = ""
    log_level: str = ""
    rules: list[str] = field(default_factory=list)
    disable_default_rules: bool = False

    _data: YAMLDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Load config from file and env, then populate defaults.

        Raises:
            ValueError: If config file fails to load.
        """
        if not self.path:
            self.path = default_config_path
        config_data = {}
        if os.path.exists(self.path):
            with open(self.path) as file:
                try:
                    config_data = yaml.safe_load(file)
                except Exception as e:
                    raise ValueError(f"failed to load the config file: {e}") from e
        if config_data:
            self._data = config_data

        if not self.data_dir:
            val = self._get_single_config("ARI_DATA_DIR", "data_dir", default_data_dir)
            self.data_dir = val if isinstance(val, str) else default_data_dir
        if not self.disable_default_rules:
            val = self._get_single_config(
                "ARI_DISABLE_DEFAULT_RULES", "disable_default_rules", default_disable_default_rules
            )
            self.disable_default_rules = val if isinstance(val, bool) else default_disable_default_rules
        if not self.rules_dir:
            if self.disable_default_rules:
                val = self._get_single_config("ARI_RULES_DIR", "rules_dir", "")
                self.rules_dir = val if isinstance(val, str) else ""
            else:
                val = self._get_single_config("ARI_RULES_DIR", "rules_dir", default_rules_dir)
                self.rules_dir = val if isinstance(val, str) else default_rules_dir
        # automatically add the default rules dir unless it is disabled
        if not self.rules_dir.endswith(default_rules_dir) and not self.disable_default_rules:
            self.rules_dir += ":" + default_rules_dir
        if not self.logger_key:
            val = self._get_single_config("ARI_LOGGER_KEY", "logger_key", default_logger_key)
            self.logger_key = val if isinstance(val, str) else default_logger_key
        if not self.log_level:
            val = self._get_single_config("ARI_LOG_LEVEL", "log_level", default_log_level)
            self.log_level = val if isinstance(val, str) else default_log_level
        if not self.rules:
            val = self._get_single_config("ARI_RULES", "rules", default_rules, "list", ",")
            self.rules = val if isinstance(val, list) else default_rules

    def _get_single_config(
        self,
        env_key: str = "",
        yaml_key: str = "",
        __default: str | list[str] | bool | None = None,
        __type: str | None = None,
        separator: str = "",
    ) -> str | list[str] | bool:
        """Resolve a config value from env, YAML file, or default.

        Args:
            env_key: Environment variable name to check first.
            yaml_key: Key in config YAML to check if env is not set.
            __default: Default value when neither env nor YAML has it.
            __type: If "list", split env value by separator.
            separator: String to split env value when __type is "list".

        Returns:
            The resolved value (str, list of str, or bool).
        """
        if env_key in os.environ:
            _from_env: str | list[str] | bool | None = os.environ.get(env_key, None)
            if _from_env and __type and __type == "list":
                _from_env = _from_env.split(separator) if isinstance(_from_env, str) else _from_env
            return cast(str | list[str] | bool, _from_env if _from_env is not None else __default)
        elif yaml_key in self._data:
            _from_file = self._data.get(yaml_key, None)
            return cast(str | list[str] | bool, _from_file if _from_file is not None else __default)
        else:
            return cast(str | list[str] | bool, __default)


collection_manifest_json = "MANIFEST.json"
role_meta_main_yml = "meta/main.yml"
role_meta_main_yaml = "meta/main.yaml"


supported_target_types = [
    LoadType.PROJECT,
    LoadType.COLLECTION,
    LoadType.ROLE,
    LoadType.PLAYBOOK,
]

config = Config()

logger.set_logger_channel(config.logger_key)
logger.set_log_level(config.log_level)


@dataclass
class SingleScan:
    """State for a single ARI scan (collection, role, playbook, or taskfile).

    Attributes:
        type: Scan target type (collection, role, playbook, taskfile).
        name: Target name.
        collection_name: Collection name if scanning a collection.
        role_name: Role name if scanning a role.
        target_playbook_name: Specific playbook name within a project.
        playbook_yaml: Raw YAML string for inline playbook scanning.
        playbook_only: Whether to scan only the playbook file.
        target_taskfile_name: Specific taskfile name within a project.
        taskfile_yaml: Raw YAML string for inline taskfile scanning.
        taskfile_only: Whether to scan only the taskfile.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        install_log: Log output from dependency installation.
        tmp_install_dir: Temporary directory for installed dependencies.
        index: Index data for the scanned target.
        root_definitions: Definitions from the root target.
        ext_definitions: Definitions from external dependencies.
        target_object: Root Object for the scan target.
        trees: List of object trees built during scanning.
        additional: Additional objects (e.g. inventory).
        taskcalls_in_trees: Task calls organized by tree.
        contexts: Ansible run contexts built during scanning.
        data_report: Data report dict for the scan.
        install_dependencies: Whether to install dependencies before scanning.
        use_ansible_path: Whether to use ansible path resolution.
        dependency_dir: Directory containing dependencies.
        base_dir: Base directory for path resolution.
        target_path: Resolved target path on disk.
        loaded_dependency_dirs: List of loaded dependency directory dicts.
        use_src_cache: Whether to use source cache.
        prm: Previous RAM metadata for the target.
        download_url: URL the target was downloaded from.
        version: Version of the target.
        hash: Content hash of the target.
        source_repository: Source repository URL.
        out_dir: Output directory for results.
        include_test_contents: Whether to include test content.
        load_all_taskfiles: Whether to load all taskfiles in a project.
        yaml_label_list: List of YAML labels for targeted loading.
        save_only_rule_result: Whether to save only rule results.
        extra_requirements: Extra Galaxy requirements to install.
        resolve_failures: Dict tracking variable resolution failures.
        findings: Findings object with scan results.
        result: ARIResult summary object.
        hierarchy_payload: OPA input payload with hierarchy and annotations.
        root_dir: Root data directory from scanner config.
        rules_dir: Directory containing rule definitions.
        rules: List of rule IDs or paths to enable.
        rules_cache: Cached Rule objects.
        persist_dependency_cache: Whether to keep dependency cache after scan.
        spec_mutations_from_previous_scan: Spec mutations carried from prior scan.
        spec_mutations: Spec mutations detected in this scan.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        do_save: Whether to save scan artifacts to disk.
        silent: Whether to suppress log output.

    """

    type: str = ""
    name: str = ""
    collection_name: str = ""
    role_name: str = ""
    target_playbook_name: str | None = None
    playbook_yaml: str = ""
    playbook_only: bool = False
    target_taskfile_name: str | None = None
    taskfile_yaml: str = ""
    taskfile_only: bool = False

    skip_playbook_format_error: bool = True
    skip_task_format_error: bool = True

    install_log: str = ""
    tmp_install_dir: tempfile.TemporaryDirectory[str] | None = None

    index: YAMLDict = field(default_factory=dict)

    root_definitions: YAMLDict = field(default_factory=dict)
    ext_definitions: YAMLDict = field(default_factory=dict)

    target_object: Object = field(default_factory=Object)

    trees: list[ObjectList] = field(default_factory=list)
    # for inventory object
    additional: ObjectList = field(default_factory=ObjectList)

    taskcalls_in_trees: list[TaskCallsInTree] = field(default_factory=list)
    contexts: list[AnsibleRunContext] = field(default_factory=list)

    data_report: YAMLDict = field(default_factory=dict)

    __path_mappings: YAMLDict = field(default_factory=dict)

    install_dependencies: bool = False
    use_ansible_path: bool = False

    dependency_dir: str = ""
    base_dir: str = ""
    target_path: str = ""
    loaded_dependency_dirs: list[YAMLDict] = field(default_factory=list)
    use_src_cache: bool = True

    prm: YAMLDict = field(default_factory=dict)

    download_url: str = ""
    version: str = ""
    hash: str = ""

    source_repository: str = ""
    out_dir: str = ""

    include_test_contents: bool = False
    load_all_taskfiles: bool = False
    yaml_label_list: list[str] = field(default_factory=list)

    save_only_rule_result: bool = False

    extra_requirements: YAMLList = field(default_factory=list)
    resolve_failures: YAMLDict = field(default_factory=dict)

    findings: Findings | None = None
    result: ARIResult | None = None

    # OPA input: hierarchy + annotations (set by build_hierarchy_payload when native rules are disabled)
    hierarchy_payload: YAMLDict = field(default_factory=dict)

    # the following are set by ARIScanner
    root_dir: str = ""
    rules_dir: str = ""
    rules: list[str] = field(default_factory=list)
    rules_cache: list[Rule] = field(default_factory=list)
    persist_dependency_cache: bool = False
    spec_mutations_from_previous_scan: YAMLDict = field(default_factory=dict)
    spec_mutations: YAMLDict = field(default_factory=dict)
    use_ansible_doc: bool = True
    do_save: bool = False
    silent: bool = False
    _parser: Parser | None = None

    def __post_init__(self) -> None:
        """Initialize path mappings and target names based on scan type.

        Raises:
            ValueError: If type is unsupported.
        """
        if self.type == LoadType.COLLECTION or self.type == LoadType.ROLE:
            type_root = self.type + "s"
            target_name = self.name
            if is_local_path(target_name):
                target_name = escape_local_path(target_name)
            self.__path_mappings = {
                "src": os.path.join(self.root_dir, type_root, "src"),
                "root_definitions": os.path.join(
                    self.root_dir,
                    type_root,
                    "root",
                    "definitions",
                    type_root,
                    target_name,
                ),
                "ext_definitions": {
                    LoadType.ROLE: os.path.join(self.root_dir, "roles", "definitions"),
                    LoadType.COLLECTION: os.path.join(self.root_dir, "collections", "definitions"),
                },
                "index": os.path.join(
                    self.root_dir,
                    type_root,
                    f"{self.type}-{target_name}-index-ext.json",
                ),
                "install_log": os.path.join(
                    self.root_dir,
                    type_root,
                    f"{self.type}-{target_name}-install.log",
                ),
            }

        elif self.type == LoadType.PROJECT or self.type == LoadType.PLAYBOOK or self.type == LoadType.TASKFILE:
            type_root = self.type + "s"
            proj_name = escape_url(self.name)
            if self.type == LoadType.PLAYBOOK:
                if self.playbook_yaml:
                    self.target_playbook_name = self.name
                else:
                    if self.base_dir:
                        basedir = self.base_dir
                        target_playbook_path = self.name.replace(basedir, "")
                        if target_playbook_path[0] == "/":
                            target_playbook_path = target_playbook_path[1:]
                        self.target_playbook_name = target_playbook_path
                    else:
                        _, self.target_playbook_name = split_target_playbook_fullpath(self.name)
            elif self.type == LoadType.TASKFILE:
                if self.taskfile_yaml:
                    self.target_taskfile_name = self.name
                else:
                    if self.base_dir:
                        basedir = self.base_dir
                        target_taskfile_path = self.name.replace(basedir, "")
                        if target_taskfile_path[0] == "/":
                            target_taskfile_path = target_taskfile_path[1:]
                        self.target_taskfile_name = target_taskfile_path
                    else:
                        _, self.target_taskfile_name = split_target_taskfile_fullpath(self.name)
            self.__path_mappings = {
                "src": os.path.join(self.root_dir, type_root, proj_name, "src"),
                "root_definitions": os.path.join(
                    self.root_dir,
                    type_root,
                    proj_name,
                    "definitions",
                ),
                "ext_definitions": {
                    LoadType.ROLE: os.path.join(self.root_dir, "roles", "definitions"),
                    LoadType.COLLECTION: os.path.join(self.root_dir, "collections", "definitions"),
                },
                "index": os.path.join(
                    self.root_dir,
                    type_root,
                    proj_name,
                    "index-ext.json",
                ),
                "install_log": os.path.join(
                    self.root_dir,
                    type_root,
                    proj_name,
                    f"{self.type}-{proj_name}-install.log",
                ),
                "dependencies": os.path.join(self.root_dir, type_root, proj_name, "dependencies"),
            }

        else:
            raise ValueError(f"Unsupported type: {self.type}")

        if self.playbook_yaml:
            self.playbook_only = True
            if not self.name:
                self.name = "__in_memory__"
                self.target_playbook_name = self.name

        if self.taskfile_yaml:
            self.taskfile_only = True
            if not self.name:
                self.name = "__in_memory__"
                self.target_taskfile_name = self.name

    def make_target_path(self, typ: str, target_name: str, dep_dir: str = "") -> str:
        """Resolve the filesystem path for a target (collection, role, playbook, etc.).

        Args:
            typ: Load type (collection, role, playbook, project, taskfile).
            target_name: Target name (FQCN, path, or URL).
            dep_dir: Optional dependency directory to search first.

        Returns:
            Absolute path to the target on disk.
        """
        target_path = ""

        if dep_dir:
            parts = target_name.split(".")
            if len(parts) == 1:
                parts.append("")
            dep_dir_target_path_candidates = [
                os.path.join(dep_dir, target_name),
                os.path.join(dep_dir, parts[0], parts[1]),
                os.path.join(dep_dir, "ansible_collections", parts[0], parts[1]),
            ]
            for cand_path in dep_dir_target_path_candidates:
                if os.path.exists(cand_path):
                    target_path = cand_path
                    break
        if target_path != "":
            return target_path

        if typ == LoadType.COLLECTION:
            parts = target_name.split(".")
            if is_local_path(target_name):
                target_path = target_name
            else:
                target_path = os.path.join(self.root_dir, typ + "s", "src", "ansible_collections", parts[0], parts[1])
        elif typ == LoadType.ROLE:
            if is_local_path(target_name):
                target_path = target_name
            else:
                target_path = os.path.join(self.root_dir, typ + "s", "src", target_name)
        elif typ == LoadType.PROJECT or typ == LoadType.PLAYBOOK or typ == LoadType.TASKFILE:
            if is_url(target_name):
                target_path = os.path.join(self.get_src_root(), escape_url(target_name))
            else:
                target_path = target_name
        return target_path

    def get_src_root(self) -> str:
        """Return the source root directory for the current scan type.

        Returns:
            Path to the src root, or empty string if not set.
        """
        src_val = self.__path_mappings.get("src")
        return str(src_val) if src_val is not None else ""

    def is_src_installed(self) -> bool:
        """Check whether the target source is already installed (index exists).

        Returns:
            True if the index file exists, False otherwise.
        """
        index_location = self.__path_mappings.get("index")
        return isinstance(index_location, str) and os.path.exists(index_location)

    def _prepare_dependencies(self, root_install: bool = True) -> tuple[str, list[dict[str, object]]]:
        """Install dependencies and prepare dependency directories.

        Args:
            root_install: If True, install the root target.

        Returns:
            Tuple of (target_path, list of dependency dir metadata dicts).
        """
        # Install the target if needed
        target_path = self.make_target_path(self.type, self.name)

        # Dependency Dir Preparator
        ddp = DependencyDirPreparator(
            root_dir=self.root_dir,
            source_repository=self.source_repository,
            target_type=self.type,
            target_name=self.name,
            target_version=self.version,
            target_path=target_path,
            target_dependency_dir=self.dependency_dir,
            target_path_mappings=self.__path_mappings,
            do_save=self.do_save,
            silent=self.silent,
            tmp_install_dir=self.tmp_install_dir,
            periodical_cleanup=self.persist_dependency_cache,
        )
        dep_dirs = ddp.prepare_dir(
            root_install=root_install,
            use_ansible_path=self.use_ansible_path,
            is_src_installed=self.is_src_installed(),
            cache_enabled=self.use_src_cache,
            cache_dir=os.path.join(self.root_dir, "archives"),
        )

        self.target_path = target_path
        self.version = ddp.metadata.version
        self.hash = ddp.metadata.hash
        self.download_url = ddp.metadata.download_url
        self.loaded_dependency_dirs = dep_dirs

        return target_path, cast(list[dict[str, object]], dep_dirs)

    def create_load_file(self, target_type: str, target_name: str, target_path: str) -> Load:
        """Create and populate a Load object for the target.

        Args:
            target_type: Load type (collection, role, playbook, etc.).
            target_name: Target name (FQCN, path, or URL).
            target_path: Resolved filesystem path.

        Returns:
            Load object with target metadata and populated definitions.

        Raises:
            ValueError: If target_path does not exist and no in-memory YAML.
        """
        loader_version = get_loader_version()

        if not os.path.exists(target_path) and not self.playbook_yaml and not self.taskfile_yaml:
            raise ValueError(f"No such file or directory: {target_path}")
        if not self.silent:
            logger.debug(f"target_name: {target_name}")
            logger.debug(f"target_type: {target_type}")
            logger.debug(f"path: {target_path}")
            logger.debug(f"loader_version: {loader_version}")
        ld = Load(
            target_name=target_name,
            target_type=target_type,
            path=target_path,
            loader_version=loader_version,
            playbook_yaml=self.playbook_yaml,
            playbook_only=self.playbook_only,
            taskfile_yaml=self.taskfile_yaml,
            taskfile_only=self.taskfile_only,
            base_dir=self.base_dir,
            include_test_contents=self.include_test_contents,
            yaml_label_list=self.yaml_label_list,
        )
        load_object(ld)
        return ld

    def get_definition_path(self, ext_type: str, ext_name: str) -> str:
        """Return the path where external definitions for a role/collection are stored.

        Args:
            ext_type: External type (role or collection).
            ext_name: External name (role name or collection FQCN).

        Returns:
            Path to the definitions directory.

        Raises:
            ValueError: If ext_type is not role or collection.
        """
        target_path = ""
        ext_defs = self.__path_mappings.get("ext_definitions")
        if isinstance(ext_defs, dict):
            if ext_type == LoadType.ROLE:
                base = ext_defs.get(LoadType.ROLE)
                target_path = os.path.join(str(base), ext_name) if isinstance(base, str) else ""
            elif ext_type == LoadType.COLLECTION:
                base = ext_defs.get(LoadType.COLLECTION)
                target_path = os.path.join(str(base), ext_name) if isinstance(base, str) else ""
        else:
            raise ValueError("Invalid ext_type")
        return target_path

    def load_definition_ext(self, target_type: str, target_name: str, target_path: str) -> None:
        """Load external definitions (role or collection) from path or cache.

        Args:
            target_type: Load type (role or collection).
            target_name: Target name (FQCN or role name).
            target_path: Path to the role or collection.

        Raises:
            ValueError: If parser is not initialized or parser run fails.
        """
        ld = self.create_load_file(target_type, target_name, target_path)
        use_cache = True
        output_dir = self.get_definition_path(ld.target_type, ld.target_name)
        if use_cache and os.path.exists(os.path.join(output_dir, "mappings.json")):
            if not self.silent:
                logger.debug(f"use cache from {output_dir}")
            definitions, mappings = Parser.restore_definition_objects(output_dir)
        else:
            if self._parser is None:
                raise ValueError("Parser not initialized")
            run_result = self._parser.run(load_data=ld)
            if run_result is None:
                raise ValueError("Parser run failed")
            definitions, mappings = run_result
            if self.do_save:
                if output_dir == "":
                    raise ValueError("Invalid output_dir")
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir, exist_ok=True)
                Parser.dump_definition_objects(output_dir, definitions, mappings)

        key = f"{target_type}-{target_name}"
        # mixed model/YAML dict: definitions has Object lists, mappings has Load
        self.ext_definitions[key] = {
            "definitions": definitions,  # type: ignore[dict-item]
            "mappings": mappings,  # type: ignore[dict-item]
        }
        return

    def _set_load_root(self, target_path: str = "") -> Load | None:
        """Create Load object for the root target (collection, role, playbook, etc.).

        Args:
            target_path: Optional path override; uses default if empty.

        Returns:
            Load object for the root, or None if type is unsupported.
        """
        root_load_data = None
        if self.type in [LoadType.ROLE, LoadType.COLLECTION]:
            ext_type = self.type
            ext_name = self.name
            if target_path == "":
                target_path = self.get_source_path(ext_type, ext_name)
            root_load_data = self.create_load_file(ext_type, ext_name, target_path)
        elif self.type in [LoadType.PROJECT, LoadType.PLAYBOOK, LoadType.TASKFILE]:
            src_root = self.get_src_root()
            if target_path == "":
                target_path = os.path.join(src_root, escape_url(self.name))
            root_load_data = self.create_load_file(self.type, self.name, target_path)
        return root_load_data

    def get_source_path(self, ext_type: str, ext_name: str, is_ext_for_project: bool = False) -> str:
        """Return the source path for an external role or collection.

        Args:
            ext_type: External type (role or collection).
            ext_name: External name (role name or collection FQCN).
            is_ext_for_project: If True, use project dependencies dir.

        Returns:
            Absolute path to the role or collection source.

        Raises:
            ValueError: If ext_type is not role or collection.
        """
        base_dir = ""
        if is_ext_for_project:
            dep_val = self.__path_mappings.get("dependencies")
            base_dir = str(dep_val) if isinstance(dep_val, str) else ""
        else:
            if ext_type == LoadType.ROLE:
                base_dir = os.path.join(self.root_dir, "roles", "src")
            elif ext_type == LoadType.COLLECTION:
                base_dir = os.path.join(self.root_dir, "collections", "src")

        target_path = ""
        if ext_type == LoadType.ROLE:
            target_path = os.path.join(base_dir, ext_name)
        elif ext_type == LoadType.COLLECTION:
            parts = ext_name.split(".")
            target_path = os.path.join(
                base_dir,
                "ansible_collections",
                parts[0],
                parts[1],
            )
        else:
            raise ValueError("Invalid ext_type")
        return target_path

    def load_definitions_root(self, target_path: str = "") -> None:
        """Load root definitions (playbooks, roles, etc.) via parser.

        Args:
            target_path: Optional path override for the root target.

        Raises:
            ValueError: If root load is None, parser is not initialized or run fails.
        """
        output_dir_val = self.__path_mappings.get("root_definitions")
        output_dir = str(output_dir_val) if isinstance(output_dir_val, str) else ""
        root_load = self._set_load_root(target_path=target_path)
        if root_load is None:
            raise ValueError("Root load data is None")

        if self._parser is None:
            raise ValueError("Parser not initialized")
        run_result = self._parser.run(load_data=root_load, collection_name_of_project=self.collection_name)
        if run_result is None:
            raise ValueError("Parser run failed")
        definitions, mappings = run_result
        if self.do_save:
            if output_dir == "":
                raise ValueError("Invalid output_dir")
            if not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            Parser.dump_definition_objects(output_dir, definitions, mappings)

        # mixed model/YAML dict: definitions has Object lists, mappings has Load
        self.root_definitions = {
            "definitions": definitions,  # type: ignore[dict-item]
            "mappings": mappings,  # type: ignore[dict-item]
        }

    def apply_spec_mutations(self) -> None:
        """Overwrite root definitions with mutated objects from spec_mutations_from_previous_scan."""
        if not self.spec_mutations_from_previous_scan:
            return
        # overwrite the loaded object with the mutated object in spec mutations
        definitions = self.root_definitions.get("definitions", {})
        if not isinstance(definitions, dict):
            return
        for type_name in definitions:
            obj_list = definitions.get(type_name, [])
            if not isinstance(obj_list, list):
                continue
            for i, obj in enumerate(obj_list):
                if not hasattr(obj, "key"):
                    continue
                key = getattr(obj, "key", "")
                if key in self.spec_mutations_from_previous_scan:
                    m = self.spec_mutations_from_previous_scan[key]
                    if m is not None and hasattr(m, "object"):
                        mutated_spec = m.object
                        new_list = obj_list[:i] + [cast(Object, mutated_spec)] + obj_list[i + 1 :]
                        definitions[type_name] = new_list  # type: ignore[assignment]
        return

    def set_target_object(self) -> None:
        """Set target_object from root definitions based on type and name."""
        type_name = self.type + "s"
        definitions = self.root_definitions.get("definitions", {})
        if not isinstance(definitions, dict):
            return
        obj_list = definitions.get(type_name, [])
        if not isinstance(obj_list, list) or len(obj_list) == 0:
            return
        elif len(obj_list) == 1:
            self.target_object = cast(Object, obj_list[0])
        else:
            # only for playbook / taskfile not in `--xxxx-only` mode
            for obj in obj_list:
                obj_path = getattr(obj, "defined_in", None)
                if obj_path is not None and self.name in str(obj_path):
                    self.target_object = cast(Object, obj)
                    break
        return

    def construct_trees(self, ram_client: RAMClient | None = None) -> None:
        """Build call trees from root and ext definitions, optionally using RAM for lookups.

        Args:
            ram_client: Optional RAM client for module/role/taskfile lookups.
        """
        trees, additional, extra_requirements, resolve_failures = tree(
            cast(dict[str, object], self.root_definitions),
            cast(dict[str, object], self.ext_definitions),
            ram_client,
            self.target_playbook_name,
            self.target_taskfile_name,
            self.load_all_taskfiles,
        )

        # set annotation for spec mutations
        if self.spec_mutations_from_previous_scan:
            spec_mutations = self.spec_mutations_from_previous_scan
            for _tree in trees:
                for callobj in _tree.items:
                    if not isinstance(callobj, TaskCall):
                        continue
                    obj_key = callobj.spec.key
                    if obj_key in spec_mutations:
                        m = spec_mutations[obj_key]
                        if m is not None and hasattr(m, "rule") and hasattr(m, "changes"):
                            rule_id = getattr(m.rule, "rule_id", "")
                            value = {
                                "rule_id": rule_id,
                                "changes": getattr(m, "changes", []),
                            }
                            callobj.set_annotation(key="spec.mutations", value=value, rule_id=rule_id)

        self.trees = trees
        self.additional = additional
        self.extra_requirements = cast(YAMLList, extra_requirements)
        self.resolve_failures = cast(YAMLDict, resolve_failures)

        if self.do_save:
            root_def_dir_val = self.__path_mappings.get("root_definitions")
            root_def_dir = str(root_def_dir_val) if isinstance(root_def_dir_val, str) else ""
            tree_rel_file = os.path.join(root_def_dir, "tree.json")
            if tree_rel_file != "":
                lines = []
                for t_obj_list in self.trees:
                    lines.append(t_obj_list.to_one_line_json())
                Path(tree_rel_file).write_text("\n".join(lines))
                if not self.silent:
                    logger.info("  tree file saved")
        return

    def resolve_variables(self, ram_client: RAMClient | None = None) -> None:
        """Resolve variables in trees and build AnsibleRunContext for each tree.

        Args:
            ram_client: Optional RAM client for context lookups.
        """
        taskcalls_in_trees = resolve(self.trees, self.additional)
        self.taskcalls_in_trees = taskcalls_in_trees

        for i, tree in enumerate(self.trees):
            last_item = i + 1 == len(self.trees)
            scan_metadata = {
                "type": self.type,
                "name": self.name,
            }
            ctx = AnsibleRunContext.from_tree(
                tree=tree,
                parent=self.target_object,
                last_item=last_item,
                ram_client=ram_client,
                scan_metadata=cast(YAMLDict, scan_metadata),
            )
            self.contexts.append(ctx)

        if self.do_save:
            root_def_dir_val = self.__path_mappings.get("root_definitions")
            root_def_dir = str(root_def_dir_val) if isinstance(root_def_dir_val, str) else ""
            tasks_in_t_path = os.path.join(root_def_dir, "tasks_in_trees.json")
            tasks_in_t_lines = []
            for d in taskcalls_in_trees:
                line = jsonpickle.encode(d, make_refs=False)
                tasks_in_t_lines.append(line)

            Path(tasks_in_t_path).write_text("\n".join(tasks_in_t_lines))
        return

    def annotate(self) -> None:
        """Run analysis on contexts to add annotations (e.g., risk annotations)."""
        contexts = analyze(self.contexts)
        self.contexts = contexts

        if self.do_save:
            root_def_dir_val = self.__path_mappings.get("root_definitions")
            root_def_dir = str(root_def_dir_val) if isinstance(root_def_dir_val, str) else ""
            contexts_a_path = os.path.join(root_def_dir, "contexts_with_analysis.json")
            conetxts_a_lines = []
            for d in contexts:
                line = jsonpickle.encode(d, make_refs=False)
                conetxts_a_lines.append(line)

            Path(contexts_a_path).write_text("\n".join(conetxts_a_lines))

        return

    def _node_to_dict(self, node: RunTarget) -> YAMLDict:
        """Serialize a RunTarget (playcall, rolecall, taskcall, etc.) to a JSON-serializable dict for OPA input.

        Args:
            node: RunTarget to serialize.

        Returns:
            Dict with type, key, file, line, defined_in, and node-specific fields.
        """
        d = {"type": getattr(node, "type", ""), "key": getattr(node, "key", "")}
        spec = getattr(node, "spec", None)
        if spec:
            d["file"] = getattr(spec, "defined_in", "") or ""
            line_num = getattr(spec, "line_num_in_file", None) or getattr(spec, "line_number", None)
            if line_num and isinstance(line_num, (list, tuple)) and len(line_num) >= 2:
                d["line"] = [int(line_num[0]), int(line_num[1])]
            else:
                d["line"] = None
            d["defined_in"] = getattr(spec, "defined_in", "") or ""
        else:
            d["file"] = ""
            d["line"] = None
            d["defined_in"] = ""
        node_type = getattr(node, "type", "")
        # Play has no line_num_in_file in loader; give playcall a fallback line so OPA L003 can fire
        if node_type == "playcall" and d.get("line") is None and spec:
            play_index = getattr(spec, "index", 0)
            d["line"] = [max(1, play_index + 1), max(1, play_index + 1)]
        # Playcall: name + options (become, become_user) for partial-become and play-name
        # Use null for missing name so OPA L003 (play should have name) can fire
        if node_type == "playcall" and spec:
            name_val = getattr(spec, "name", "") or ""
            d["name"] = name_val if name_val else None
            opts = getattr(spec, "options", None)
            if isinstance(opts, dict):
                d["options"] = self._opts_for_opa(opts, ["become", "become_user"])
            else:
                d["options"] = {}
        if node_type == "taskcall":
            original_module = (getattr(spec, "module", "") if spec else "") or ""
            d["module"] = (
                getattr(node, "resolved_name", "")
                or getattr(node, "resolved_action", "")
                or original_module
            )
            d["original_module"] = original_module
            anns = []
            for an in getattr(node, "annotations", []) or []:
                anns.append(self._annotation_to_dict(an))
            d["annotations"] = anns
            d["name"] = None
            d["options"] = {}
            d["module_options"] = {}
            if spec:
                name_val = getattr(spec, "name", "") or ""
                d["name"] = name_val if name_val else None
                opts = getattr(spec, "options", None)
                if isinstance(opts, dict):
                    d["options"] = self._opts_for_opa(
                        opts,
                        [
                            "when",
                            "tags",
                            "ignore_errors",
                            "ignore_unreachable",
                            "register",
                            "changed_when",
                            "become",
                            "become_user",
                            "run_once",
                            "local_action",
                            # with_* for M009 (deprecated loops)
                            "with_items",
                            "with_dict",
                            "with_fileglob",
                            "with_subelements",
                            "with_sequence",
                            "with_nested",
                            "with_first_found",
                            "with_indexed_items",
                            "with_flattened",
                            "with_together",
                            "with_random_choice",
                            "with_lines",
                            "with_ini",
                            "with_inventory_hostnames",
                            "with_cartesian",
                        ],
                    )
                mo = getattr(spec, "module_options", None)
                if isinstance(mo, dict):
                    mo_dict: dict[str, object] = {str(k): self._json_safe(v) for k, v in mo.items()}
                    # OPA L006/L013/L022 expect "cmd"; loader stores free-form shell/command as "_raw"
                    if "_raw" in mo_dict and "cmd" not in mo_dict:
                        raw_val = mo_dict.get("_raw")
                        if isinstance(raw_val, str):
                            mo_dict["cmd"] = raw_val
                    d["module_options"] = mo_dict
        return d

    def _opts_for_opa(self, opts: YAMLDict, keys: list[str]) -> YAMLDict:
        """Return a JSON-serializable subset of opts for OPA (only listed keys that exist).

        Args:
            opts: Full options dict.
            keys: Keys to include if present.

        Returns:
            Subset of opts with only the listed keys, JSON-safe values.
        """
        out = {}
        for k in keys:
            if k not in opts:
                continue
            v = opts[k]
            with contextlib.suppress(Exception):
                out[k] = self._json_safe(v)
        return out

    def _json_safe(self, v: YAMLValue) -> YAMLValue:
        """Coerce value to a JSON-serializable form.

        Args:
            v: Value to coerce (str, int, float, bool, list, dict, or other).

        Returns:
            JSON-serializable value; non-primitives are stringified.
        """
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, (list, tuple)):
            return [self._json_safe(x) for x in v]
        if isinstance(v, dict):
            return {str(k): self._json_safe(x) for k, x in v.items()}
        return str(v)

    def _location_to_dict(self, loc: Location | None) -> YAMLDict | None:
        """Serialize a Location to a JSON-safe dict for OPA.

        Args:
            loc: Location to serialize, or None.

        Returns:
            Dict with type, value, is_mutable, or None if loc is empty/None.
        """
        if loc is None or getattr(loc, "is_empty", False):
            return None
        return {
            "type": getattr(loc, "type", "") or "",
            "value": self._json_safe(getattr(loc, "value", "")) or "",
            "is_mutable": getattr(loc, "is_mutable", False),
        }

    def _annotation_to_dict(self, an: Annotation) -> YAMLDict:
        """Serialize a full Annotation (including RiskAnnotation detail) for OPA input.

        Args:
            an: Annotation to serialize.

        Returns:
            Dict with type, key, risk_type, and detail-specific fields.
        """
        from .models import Location

        d = {
            "type": getattr(an, "type", ""),
            "key": getattr(an, "key", ""),
        }
        if not isinstance(an, RiskAnnotation):
            d["risk_type"] = ""
            return d

        d["risk_type"] = getattr(an, "risk_type", "") or ""

        # CommandExecDetail
        cmd = getattr(an, "command", None)
        if cmd is not None:
            d["command"] = self._json_safe(getattr(cmd, "raw", None)) or ""
        exec_files = getattr(an, "exec_files", None)
        if exec_files:
            d["exec_files"] = [self._location_to_dict(ef) for ef in exec_files if ef]

        # NetworkTransferDetail (Inbound / Outbound)
        src = getattr(an, "src", None)
        dest = getattr(an, "dest", None)
        if isinstance(src, Location):
            d["src"] = self._location_to_dict(src)
        if isinstance(dest, Location):
            d["dest"] = self._location_to_dict(dest)
        for flag in ("is_mutable_src", "is_mutable_dest"):
            val = getattr(an, flag, None)
            if val is not None:
                d[flag] = bool(val)

        # PackageInstallDetail
        pkg = getattr(an, "pkg", None)
        if pkg is not None and pkg != "":
            d["pkg"] = self._json_safe(pkg)
        version = getattr(an, "version", None)
        if version is not None and version != "":
            d["version"] = self._json_safe(version)
        for flag in ("is_mutable_pkg", "disable_validate_certs", "allow_downgrade"):
            val = getattr(an, flag, None)
            if val is not None:
                d[flag] = bool(val)

        # FileChangeDetail
        path_loc = getattr(an, "path", None)
        if isinstance(path_loc, Location):
            d["path_loc"] = self._location_to_dict(path_loc)
        for flag in ("is_mutable_path", "is_mutable_src", "is_unsafe_write", "is_deletion", "is_insecure_permissions"):
            val = getattr(an, flag, None)
            if val is not None:
                d[flag] = bool(val)

        # KeyConfigChangeDetail
        config_key = getattr(an, "key", None)
        if config_key and d.get("key") != config_key:
            d["config_key"] = self._json_safe(config_key)
        if getattr(an, "is_mutable_key", None) is not None:
            d["is_mutable_key"] = bool(getattr(an, "is_mutable_key", False))

        return d

    def build_hierarchy_payload(self, scan_id: str = "") -> YAMLDict:
        """Build OPA input: hierarchy (collection/role/playbook/play/task) + annotations. No native rules.

        Args:
            scan_id: Optional scan ID; defaults to current UTC timestamp.

        Returns:
            Dict with scan_id, hierarchy (trees with nodes), and metadata.
        """
        if not scan_id:
            scan_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        trees_data = []
        for ctx in self.contexts:
            if not ctx:
                continue
            root_key = getattr(ctx, "root_key", "")
            root_type = key_detect_type(root_key) if root_key else ""
            # Expose root file path for playbook-extension and similar
            # (e.g. "playbook :/path/to/pb.yml" -> "/path/to/pb.yml")
            root_path = ""
            if root_key and " " in root_key:
                root_path = root_key.split(" ", 1)[-1].lstrip(":")
            nodes = []
            for item in getattr(ctx, "sequence", None) or []:
                nodes.append(self._node_to_dict(item))
            trees_data.append({"root_key": root_key, "root_type": root_type, "root_path": root_path, "nodes": nodes})
        self.hierarchy_payload = cast(
            YAMLDict,
            {
                "scan_id": scan_id,
                "hierarchy": trees_data,
                "metadata": {
                    "type": self.type,
                    "name": self.name,
                    "collection_name": self.collection_name or "",
                    "role_name": self.role_name or "",
                },
            },
        )
        return self.hierarchy_payload

    def apply_rules(self) -> None:
        """Build hierarchy payload and create Findings for OPA (engine-only mode, no native rules)."""
        # Engine-only mode: no native ARI rules; build hierarchy+annotations for OPA.
        self.build_hierarchy_payload()
        target_name = self.name
        if self.collection_name:
            target_name = self.collection_name
        if self.role_name:
            target_name = self.role_name
        metadata = {
            "type": self.type,
            "name": target_name,
            "version": self.version,
            "source": self.source_repository,
            "download_url": self.download_url,
            "hash": self.hash,
        }
        dependencies = self.loaded_dependency_dirs
        self.findings = Findings(
            metadata=cast(YAMLDict, metadata),
            dependencies=cast(YAMLList, dependencies),
            root_definitions=self.root_definitions,
            ext_definitions=self.ext_definitions,
            extra_requirements=self.extra_requirements,
            resolve_failures=self.resolve_failures,
            prm=self.prm,
            report={"hierarchy_payload": self.hierarchy_payload},
            summary_txt="",
            scan_time=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
        )
        self.result = None
        return

    def add_time_records(self, time_records: dict[str, object]) -> None:
        """Add timing records to findings metadata.

        Args:
            time_records: Dict mapping record names to begin/end/elapsed timing data.
        """
        if self.findings and isinstance(self.findings.metadata, dict):
            self.findings.metadata["time_records"] = cast(YAMLValue, time_records)
        return

    def count_definitions(self) -> tuple[int, dict[str, int], dict[str, int]]:
        """Count dependency dirs and definition counts for root and ext definitions.

        Returns:
            Tuple of (dep_num, ext_counts, root_counts).
        """
        dep_num = len(self.loaded_dependency_dirs)
        ext_counts: dict[str, int] = {}
        for _, _defs in self.ext_definitions.items():
            defs_val = _defs.get("definitions") if isinstance(_defs, dict) else None
            defs_dict = defs_val if isinstance(defs_val, dict) else {}
            for key, val in defs_dict.items():
                _current = ext_counts.get(key, 0)
                _current += len(val) if isinstance(val, (list, dict)) else 0
                ext_counts[key] = _current
        root_counts: dict[str, int] = {}
        root_defs_val = self.root_definitions.get("definitions")
        root_defs_dict = root_defs_val if isinstance(root_defs_val, dict) else {}
        for key, val in root_defs_dict.items():
            _current = root_counts.get(key, 0)
            _current += len(val) if isinstance(val, (list, dict)) else 0
            root_counts[key] = _current
        return dep_num, ext_counts, root_counts

    def set_metadata(self, metadata: dict[str, object], dependencies: list[dict[str, object]]) -> None:
        """Set scan metadata (version, hash, download_url) and dependency dirs from dicts.

        Args:
            metadata: Dict with version, hash, download_url keys.
            dependencies: List of dependency dir dicts.

        """
        self.target_path = self.make_target_path(self.type, self.name)
        self.version = str(metadata.get("version", ""))
        self.hash = str(metadata.get("hash", ""))
        self.download_url = str(metadata.get("download_url", ""))
        self.loaded_dependency_dirs = dependencies  # type: ignore[assignment]

    def set_metadata_findings(self) -> None:
        """Create minimal Findings with metadata and dependencies only."""
        target_name = self.name
        if self.collection_name:
            target_name = self.collection_name
        if self.role_name:
            target_name = self.role_name
        metadata = {
            "type": self.type,
            "name": target_name,
            "version": self.version,
            "source": self.source_repository,
            "download_url": self.download_url,
            "hash": self.hash,
        }
        dependencies = self.loaded_dependency_dirs
        self.findings = Findings(
            metadata=cast(YAMLDict, metadata),
            dependencies=cast(YAMLList, dependencies),
        )

    def load_index(self) -> None:
        """Load the index JSON from path mappings into self.index."""
        index_location_val = self.__path_mappings.get("index")
        index_location = str(index_location_val) if isinstance(index_location_val, str) else ""
        if not index_location:
            return
        with open(index_location) as f:
            self.index = json.load(f)


@dataclass
class ARIScanner:
    """ARI scanner that evaluates collections, roles, playbooks, and taskfiles.

    Attributes:
        config: Scanner configuration object.
        root_dir: Root data directory for RAM and dependencies.
        rules_dir: Directory containing rule definitions.
        rules: List of rule IDs or paths to enable.
        rules_cache: Cached Rule objects.
        ram_client: Risk Assessment Model client for caching.
        read_ram: Whether to read from RAM cache.
        read_ram_for_dependency: Whether to read dependency data from RAM.
        write_ram: Whether to write scan results to RAM.
        persist_dependency_cache: Whether to keep dependency cache.
        skip_playbook_format_error: Whether to skip malformed playbooks.
        skip_task_format_error: Whether to skip malformed tasks.
        use_ansible_doc: Whether to use ansible-doc for module specs.
        do_save: Whether to save scan artifacts to disk.
        show_all: Whether to show all findings (including passing).
        pretty: Whether to pretty-print output.
        silent: Whether to suppress log output.
        output_format: Output format (json, yaml, etc.).

    """

    config: Config | None = None

    root_dir: str = ""
    rules_dir: str = ""
    rules: list[str] = field(default_factory=list)
    rules_cache: list[Rule] = field(default_factory=list)

    ram_client: RAMClient | None = None
    read_ram: bool = True
    read_ram_for_dependency: bool = True
    write_ram: bool = False

    persist_dependency_cache: bool = False

    skip_playbook_format_error: bool = True
    skip_task_format_error: bool = True

    use_ansible_doc: bool = True

    do_save: bool = False
    _parser: Parser | None = None

    show_all: bool = False
    pretty: bool = False
    silent: bool = False
    output_format: str = ""

    _current: SingleScan | None = None

    def __post_init__(self) -> None:
        """Initialize config, root dir, rules, RAM client, and parser from config."""
        if not self.config:
            self.config = config

        if not self.root_dir:
            self.root_dir = self.config.data_dir
        if not self.rules_dir:
            self.rules_dir = self.config.rules_dir
        if not self.rules:
            self.rules = self.config.rules
        if not self.ram_client:
            self.ram_client = RAMClient(root_dir=self.root_dir)
        self._parser = Parser(
            do_save=self.do_save,
            use_ansible_doc=self.use_ansible_doc,
            skip_playbook_format_error=self.skip_playbook_format_error,
            skip_task_format_error=self.skip_task_format_error,
        )

        if not self.silent:
            logger.debug(f"config: {self.config}")

    def evaluate(
        self,
        type: str,
        name: str = "",
        path: str = "",
        base_dir: str = "",
        collection_name: str = "",
        role_name: str = "",
        install_dependencies: bool = True,
        use_ansible_path: bool = False,
        version: str = "",
        hash: str = "",
        target_path: str = "",
        dependency_dir: str = "",
        download_only: bool = False,
        load_only: bool = False,
        skip_dependency: bool = False,
        use_src_cache: bool = False,
        source_repository: str = "",
        playbook_yaml: str = "",
        playbook_only: bool = False,
        taskfile_yaml: str = "",
        taskfile_only: bool = False,
        raw_yaml: str = "",
        include_test_contents: bool = False,
        load_all_taskfiles: bool = False,
        save_only_rule_result: bool = False,
        yaml_label_list: list[str] | None = None,
        objects: bool = False,
        out_dir: str = "",
        spec_mutations_from_previous_scan: YAMLDict | None = None,
    ) -> SingleScan | None:
        """Run a full ARI scan for the given target.

        Args:
            type: Load type (collection, role, playbook, project, taskfile).
            name: Target name (FQCN, path, or URL).
            path: Alias for name when name is empty.
            base_dir: Base directory for path resolution.
            collection_name: Parent collection when scanning a role.
            role_name: Parent role when scanning a taskfile.
            install_dependencies: Whether to install dependencies.
            use_ansible_path: Use ansible.cfg paths.
            version: Target version.
            hash: Target content hash.
            target_path: Override target path.
            dependency_dir: Pre-installed dependency dir.
            download_only: Only download, do not scan.
            load_only: Only load definitions, no tree/rule evaluation.
            skip_dependency: Skip loading dependencies.
            use_src_cache: Use source cache.
            source_repository: Source repo URL.
            playbook_yaml: In-memory playbook YAML.
            playbook_only: Scan only in-memory playbook.
            taskfile_yaml: In-memory taskfile YAML.
            taskfile_only: Scan only in-memory taskfile.
            raw_yaml: Raw YAML (alias for playbook or taskfile).
            include_test_contents: Include test content.
            load_all_taskfiles: Load all taskfiles in role.
            save_only_rule_result: Only save rule results.
            yaml_label_list: YAML labels to include.
            objects: Save definition objects to out_dir.
            out_dir: Output directory.
            spec_mutations_from_previous_scan: Mutations from prior scan.

        Returns:
            SingleScan with findings, or None if download_only or load_only.
        """
        time_records: dict[str, object] = {}
        self.record_begin(time_records, "scandata_init")

        if not name and path:
            name = path

        if raw_yaml:
            if type == LoadType.PLAYBOOK:
                playbook_yaml = raw_yaml
            elif type == LoadType.TASKFILE:
                taskfile_yaml = raw_yaml

        if is_local_path(name) and not playbook_yaml and not taskfile_yaml:
            name = os.path.abspath(name)

        scandata = SingleScan(
            type=type,
            name=name,
            collection_name=collection_name,
            role_name=role_name,
            install_dependencies=install_dependencies,
            use_ansible_path=use_ansible_path,
            version=version,
            hash=hash,
            target_path=target_path,
            base_dir=base_dir,
            skip_playbook_format_error=self.skip_playbook_format_error,
            skip_task_format_error=self.skip_task_format_error,
            dependency_dir=dependency_dir,
            use_src_cache=use_src_cache,
            source_repository=source_repository,
            playbook_yaml=playbook_yaml,
            playbook_only=playbook_only,
            taskfile_yaml=taskfile_yaml,
            taskfile_only=taskfile_only,
            include_test_contents=include_test_contents,
            load_all_taskfiles=load_all_taskfiles,
            save_only_rule_result=save_only_rule_result,
            yaml_label_list=yaml_label_list or [],
            out_dir=out_dir,
            root_dir=self.root_dir,
            rules_dir=self.rules_dir,
            rules=self.rules,
            rules_cache=self.rules_cache,
            persist_dependency_cache=self.persist_dependency_cache,
            spec_mutations_from_previous_scan=spec_mutations_from_previous_scan or {},
            use_ansible_doc=self.use_ansible_doc,
            do_save=self.do_save,
            silent=self.silent,
            _parser=self._parser,
        )
        self._current = scandata
        self.record_end(time_records, "scandata_init")

        self.record_begin(time_records, "metadata_load")
        metdata_loaded = False
        read_root_from_ram = (
            self.read_ram
            and scandata.type not in [LoadType.PLAYBOOK, LoadType.TASKFILE, LoadType.PROJECT]
            and not is_local_path(scandata.name)
        )
        if read_root_from_ram:
            loaded, metadata, dependencies = self.load_metadata_from_ram(scandata.type, scandata.name, scandata.version)
            logger.debug(f"metadata loaded: {loaded}")
            if loaded and metadata is not None and dependencies is not None:
                scandata.set_metadata(metadata, dependencies)
                metdata_loaded = True
                if not self.silent:
                    logger.debug(f'Use metadata for "{scandata.name}" in RAM DB')

        if scandata.install_dependencies and not metdata_loaded:
            logger.debug(f"start preparing {scandata.type} {scandata.name}")
            scandata._prepare_dependencies()
            logger.debug(f"finished preparing {scandata.type} {scandata.name}")

        if download_only:
            return None
        self.record_end(time_records, "metadata_load")

        if not skip_dependency:
            ext_list: list[tuple[str, str, str, str, str, bool]] = []
            for d in scandata.loaded_dependency_dirs:
                if not isinstance(d, dict):
                    continue
                meta = d.get("metadata")
                meta_dict = meta if isinstance(meta, dict) else {}
                ext_type = str(meta_dict.get("type", "")) if meta_dict else ""
                ext_name = str(meta_dict.get("name", "")) if meta_dict else ""
                ext_ver = str(meta_dict.get("version", "")) if meta_dict else ""
                ext_hash = str(meta_dict.get("hash", "")) if meta_dict else ""
                dir_val = d.get("dir")
                ext_path = str(dir_val) if isinstance(dir_val, str) else ""
                is_local_val = d.get("is_local_dir")
                is_local_dir = bool(is_local_val) if isinstance(is_local_val, bool) else False
                ext_list.append((ext_type, ext_name, ext_ver, ext_hash, ext_path, is_local_dir))
            ext_count = len(ext_list)

            # Start ARI Scanner main flow
            self.record_begin(time_records, "dependency_load")
            for i, (ext_type, ext_name, ext_ver, ext_hash, ext_path, is_local_dir) in enumerate(ext_list):
                if not self.silent:
                    if i == 0:
                        logger.info(f"start loading {ext_count} {ext_type}(s)")
                    logger.info(f"[{i + 1}/{ext_count}] {ext_type} {ext_name}")

                # avoid infinite loop
                is_root = False
                if scandata.type == ext_type and scandata.name == ext_name:
                    is_root = True

                ext_target_path = os.path.join(self.root_dir, ext_path)
                role_name_for_local_dep = ""
                # if a dependency is a local role, set the local path
                if (
                    scandata.type == LoadType.ROLE
                    and ext_type == LoadType.ROLE
                    and is_local_dir
                    and is_local_path(scandata.name)
                    and scandata.name != ext_name
                ):
                    root_role_path = scandata.name[:-1] if scandata.name[-1] == "/" else scandata.name
                    role_base_dir = os.path.dirname(root_role_path)
                    dep_role_path = os.path.join(role_base_dir, ext_name)
                    role_name_for_local_dep = ext_name
                    ext_name = dep_role_path
                    ext_target_path = dep_role_path

                if not is_root:
                    key = f"{ext_type}-{ext_name}"
                    if role_name_for_local_dep:
                        key = f"{ext_type}-{role_name_for_local_dep}"
                    read_ram_for_dependency = self.read_ram or self.read_ram_for_dependency

                    dep_loaded = False
                    if read_ram_for_dependency:
                        # searching findings from ARI RAM and use them if found
                        dep_loaded, ext_defs = self.load_definitions_from_ram(ext_type, ext_name, ext_ver, ext_hash)
                        if dep_loaded:
                            scandata.ext_definitions[key] = cast(YAMLValue, ext_defs)
                            if not self.silent:
                                logger.debug(f'Use spec data for "{ext_name}" in RAM DB')

                    if not dep_loaded:
                        # if the dependency was not found in RAM and if the target path does not exist,
                        # then we give up getting dependency data here
                        if not os.path.exists(ext_target_path):
                            continue

                        # scan dependencies and save findings to ARI RAM
                        dep_scanner = ARIScanner(
                            root_dir=self.root_dir,
                            rules_dir="",
                            rules=[],
                            ram_client=self.ram_client,
                            read_ram=read_ram_for_dependency,
                            read_ram_for_dependency=self.read_ram_for_dependency,
                            write_ram=self.write_ram,
                            use_ansible_doc=self.use_ansible_doc,
                            do_save=self.do_save,
                            silent=True,
                        )
                        # use prepared dep dirs
                        dep_scanner.evaluate(
                            type=ext_type,
                            name=ext_name,
                            version=ext_ver,
                            hash=ext_hash,
                            target_path=ext_target_path,
                            dependency_dir=scandata.dependency_dir,
                            install_dependencies=False,
                            use_ansible_path=False,
                            skip_dependency=True,
                            source_repository=scandata.source_repository,
                            include_test_contents=include_test_contents,
                            load_all_taskfiles=load_all_taskfiles,
                            load_only=True,
                        )
                        dep_scandata = dep_scanner.get_last_scandata()
                        if dep_scandata is not None:
                            scandata.ext_definitions[key] = dep_scandata.root_definitions
                        dep_loaded = True

            self.record_end(time_records, "dependency_load")

            if not self.silent:
                logger.debug("load_definition_ext() done")

        # PRM Finder
        self.record_begin(time_records, "prm_load")
        # playbooks, roles, modules = find_playbook_role_module(scandata.target_path, self.use_ansible_doc)
        # scandata.prm["playbooks"] = playbooks
        # scandata.prm["roles"] = roles
        # scandata.prm["modules"] = modules
        self.record_end(time_records, "prm_load")

        loaded = False
        self.record_begin(time_records, "target_load")
        if read_root_from_ram:
            loaded, root_defs = self.load_definitions_from_ram(
                scandata.type, scandata.name, scandata.version, scandata.hash, allow_unresolved=True
            )
            logger.debug(f"spec data loaded: {loaded}")
            if loaded:
                scandata.root_definitions = cast(YAMLDict, root_defs)
                if not self.silent:
                    logger.info("Use spec data in RAM DB")
        self.record_end(time_records, "target_load")

        if not loaded:
            scandata.load_definitions_root(target_path=scandata.target_path)

        scandata.set_target_object()

        if not self.silent:
            logger.debug("load_definitions_root() done")
            defs = scandata.root_definitions.get("definitions")
            defs_dict = defs if isinstance(defs, dict) else {}
            _pb = defs_dict.get("playbooks")
            _rl = defs_dict.get("roles")
            _tf = defs_dict.get("taskfiles")
            _tk = defs_dict.get("tasks")
            _md = defs_dict.get("modules")
            playbooks_num = len(_pb) if isinstance(_pb, (list, dict)) else 0
            roles_num = len(_rl) if isinstance(_rl, (list, dict)) else 0
            taskfiles_num = len(_tf) if isinstance(_tf, (list, dict)) else 0
            tasks_num = len(_tk) if isinstance(_tk, (list, dict)) else 0
            modules_num = len(_md) if isinstance(_md, (list, dict)) else 0
            logger.debug(
                f"playbooks: {playbooks_num}, roles: {roles_num}, taskfiles: {taskfiles_num}, "
                f"tasks: {tasks_num}, modules: {modules_num}"
            )

        self.record_begin(time_records, "apply_spec_rules")
        scandata.apply_spec_mutations()
        self.record_end(time_records, "apply_spec_rules")
        if not self.silent:
            logger.debug("apply_spec_rules() done")

        # load_only is True when this scanner is scanning dependency
        # otherwise, move on tree construction / rule evaluation
        if load_only:
            return None

        _ram_client = None
        if self.read_ram:
            _ram_client = self.ram_client

        self.record_begin(time_records, "tree_construction")
        scandata.construct_trees(_ram_client)
        self.record_end(time_records, "tree_construction")
        if not self.silent:
            logger.debug("construct_trees() done")

        self.record_begin(time_records, "variable_resolution")
        scandata.resolve_variables(_ram_client)
        self.record_end(time_records, "variable_resolution")
        if not self.silent:
            logger.debug("resolve_variables() done")

        self.record_begin(time_records, "module_annotators")
        scandata.annotate()
        self.record_end(time_records, "module_annotators")
        if not self.silent:
            logger.debug("annotate() done")

        self.record_begin(time_records, "apply_rules")
        scandata.apply_rules()
        self.record_end(time_records, "apply_rules")
        if not self.silent:
            logger.debug("apply_rules() done")

        if scandata.rules_cache:
            self.rules_cache = scandata.rules_cache

        scandata.add_time_records(time_records=time_records)

        dep_num, ext_counts, root_counts = scandata.count_definitions()
        if not self.silent:
            print("# of dependencies:", dep_num)
            # print("ext definitions:", ext_counts)
            # print("root definitions:", root_counts)

        # save RAM data
        findings = scandata.findings
        if (
            self.write_ram
            and scandata.type not in [LoadType.PLAYBOOK, LoadType.TASKFILE, LoadType.PROJECT]
            and findings is not None
        ):
            self.register_findings_to_ram(findings)
            self.register_indices_to_ram(findings, include_test_contents)

        if scandata.out_dir is not None and scandata.out_dir != "" and findings is not None:
            self.save_rule_result(findings, scandata.out_dir)
            if not self.silent:
                print(f"The rule result is saved at {scandata.out_dir}")

            if objects:
                self.save_definitions(cast(dict[str, object], scandata.root_definitions), scandata.out_dir)
                if not self.silent:
                    print(f"The objects is saved at {scandata.out_dir}")

        if not self.silent and findings is not None:
            summary = summarize_findings(findings, self.show_all)
            print(summary)

        if self.pretty and findings is not None:
            data_str = ""
            data = json.loads(jsonpickle.encode(findings.simple(), make_refs=False))
            if self.output_format.lower() == "json":
                data_str = json.dumps(data, indent=2)
            elif self.output_format.lower() == "yaml":
                data_str = yaml.safe_dump(data)
            print(data_str)

        if scandata.spec_mutations:
            trigger_rescan = False
            _previous = spec_mutations_from_previous_scan
            if _previous and equal(scandata.spec_mutations, _previous):
                if not self.silent:
                    logger.warning(
                        "Spec mutation loop has been detected! Exitting the scan here but the result may be incomplete."
                    )
            else:
                trigger_rescan = True

            if trigger_rescan:
                if not self.silent:
                    print("Spec mutations are found. Triggering ARI scan again...")
                return self.evaluate(
                    type=type,
                    name=name,
                    path=path,
                    collection_name=collection_name,
                    role_name=role_name,
                    install_dependencies=install_dependencies,
                    use_ansible_path=use_ansible_path,
                    version=version,
                    hash=hash,
                    target_path=target_path,
                    dependency_dir=dependency_dir,
                    download_only=download_only,
                    load_only=load_only,
                    skip_dependency=skip_dependency,
                    use_src_cache=use_src_cache,
                    source_repository=source_repository,
                    playbook_yaml=playbook_yaml,
                    playbook_only=playbook_only,
                    taskfile_yaml=taskfile_yaml,
                    taskfile_only=taskfile_only,
                    include_test_contents=include_test_contents,
                    load_all_taskfiles=load_all_taskfiles,
                    objects=objects,
                    raw_yaml=raw_yaml,
                    out_dir=out_dir,
                    spec_mutations_from_previous_scan=scandata.spec_mutations,
                )

        return cast(SingleScan | None, findings.report.get("ari_result", None)) if findings is not None else None

    def load_metadata_from_ram(
        self, type: str, name: str, version: str
    ) -> tuple[bool, dict[str, object] | None, list[dict[str, object]] | None]:
        """Load metadata and dependencies from RAM for a target.

        Args:
            type: Target type (collection, role, etc.).
            name: Target name.
            version: Target version string.

        Returns:
            Tuple of (loaded, metadata, dependencies). metadata/dependencies are None if not found.

        """
        if self.ram_client is None:
            return False, None, None
        loaded, metadata, dependencies = self.ram_client.load_metadata_from_findings(type, name, version)
        return loaded, cast(dict[str, object] | None, metadata), cast(list[dict[str, object]] | None, dependencies)

    def load_definitions_from_ram(
        self, type: str, name: str, version: str, hash: str, allow_unresolved: bool = False
    ) -> tuple[bool, dict[str, object]]:
        """Load definitions and mappings from RAM for a target.

        Args:
            type: Target type (collection, role, etc.).
            name: Target name.
            version: Target version string.
            hash: Content hash of the target.
            allow_unresolved: Whether to accept unresolved definitions.

        Returns:
            Tuple of (loaded, definitions_dict with definitions and mappings).

        """
        if self.ram_client is None:
            return False, {}
        loaded, definitions, mappings = self.ram_client.load_definitions_from_findings(
            type, name, version, hash, allow_unresolved
        )
        definitions_dict: dict[str, object] = {}
        if loaded:
            definitions_dict = {
                "definitions": definitions,
                "mappings": mappings,
            }
        return loaded, definitions_dict

    def register_findings_to_ram(self, findings: Findings) -> None:
        """Register findings to RAM (save to disk and evict old cache).

        Args:
            findings: Findings to register.

        """
        if self.ram_client is not None:
            self.ram_client.register(findings)

    def register_indices_to_ram(self, findings: Findings, include_test_contents: bool = False) -> None:
        """Register module, role, taskfile, and action group indices to RAM.

        Args:
            findings: Findings containing index data.
            include_test_contents: Whether to include test content indices.

        """
        if self.ram_client is not None:
            self.ram_client.register_indices_to_ram(findings, include_test_contents)

    def save_findings(self, findings: Findings, out_dir: str) -> None:
        """Save findings to findings.json in out_dir.

        Args:
            findings: Findings to save.
            out_dir: Output directory path.

        """
        if self.ram_client is not None:
            self.ram_client.save_findings(findings, out_dir)

    def save_rule_result(self, findings: Findings, out_dir: str) -> None:
        """Save rule result JSON to out_dir.

        Args:
            findings: Findings containing rule results.
            out_dir: Output directory path.

        Raises:
            ValueError: If out_dir is empty.

        """
        if out_dir == "":
            raise ValueError("output dir must be a non-empty value")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        findings.save_rule_result(fpath=os.path.join(out_dir, "rule_result.json"))

    def save_definitions(self, definitions: dict[str, object], out_dir: str) -> None:
        """Save definition objects to objects.json in out_dir.

        Args:
            definitions: Dict with definitions key containing serializable objects.
            out_dir: Output directory path.

        Raises:
            ValueError: If out_dir is empty.

        """
        if out_dir == "":
            raise ValueError("output dir must be a non-empty value")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        objects_json_str = jsonpickle.encode(definitions["definitions"], make_refs=False)
        fpath = os.path.join(out_dir, "objects.json")
        with open(fpath, "w") as file:
            file.write(objects_json_str)

    def get_last_scandata(self) -> SingleScan | None:
        """Return the most recent SingleScan from evaluate, or None.

        Returns:
            Most recent SingleScan or None if no scan has been run.

        """
        return self._current

    def save_error(self, error: str, out_dir: str = "") -> None:
        """Save error message to error.log in out_dir (or RAM findings dir if empty).

        Args:
            error: Error message to save.
            out_dir: Output directory path (falls back to RAM findings dir).

        """
        if out_dir == "" and self._current is not None and self.ram_client is not None:
            _type = self._current.type
            _name = self._current.name
            _version = self._current.version
            _hash = self._current.hash
            out_dir = self.ram_client.make_findings_dir_path(_type, _name, _version, _hash)
        if self.ram_client is not None:
            self.ram_client.save_error(error, out_dir)

    def record_begin(self, time_records: dict[str, object], record_name: str) -> None:
        """Record the start time for a named timing record.

        Args:
            time_records: Dict to update with record.
            record_name: Name of the timing record.
        """
        rec: dict[str, object] = {}
        rec["begin"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
        time_records[record_name] = rec

    def record_end(self, time_records: dict[str, object], record_name: str) -> None:
        """Record end time and elapsed seconds for a named timing record.

        Args:
            time_records: Dict containing the record from record_begin.
            record_name: Name of the timing record.
        """
        end = datetime.datetime.now(datetime.timezone.utc)
        end = end.replace(tzinfo=None)
        rec = time_records.get(record_name)
        if not isinstance(rec, dict):
            return
        rec["end"] = end.strftime("%Y-%m-%dT%H:%M:%S.%f")
        begin_val = rec.get("begin")
        begin = (
            datetime.datetime.fromisoformat(str(begin_val))
            if isinstance(begin_val, str)
            else datetime.datetime.now(datetime.timezone.utc)
        )
        elapsed = (end - begin).total_seconds()
        rec["elapsed"] = elapsed


def tree(
    root_definitions: dict[str, object],
    ext_definitions: dict[str, object],
    ram_client: RAMClient | None = None,
    target_playbook_path: str | None = None,
    target_taskfile_path: str | None = None,
    load_all_taskfiles: bool = False,
) -> tuple[list[ObjectList], ObjectList, list[dict[str, object]], dict[str, dict[str, int]]]:
    """Build call trees from root and external definitions.

    Args:
        root_definitions: Root definitions (playbooks, roles, etc.).
        ext_definitions: External dependency definitions.
        ram_client: Optional RAM client for module/role/taskfile lookups.
        target_playbook_path: Target playbook path for filtering.
        target_taskfile_path: Target taskfile path for filtering.
        load_all_taskfiles: If True, load all taskfiles in roles.

    Returns:
        Tuple of (trees, additional objects, extra_requirements, resolve_failures).

    Raises:
        ValueError: If tree construction fails.
    """
    tl = TreeLoader(
        root_definitions, ext_definitions, ram_client, target_playbook_path, target_taskfile_path, load_all_taskfiles
    )
    trees, additional = tl.run()
    if trees is None:
        raise ValueError("failed to get trees")
    # if node_objects is None:
    #     raise ValueError("failed to get node_objects")
    return (
        trees,
        additional,
        tl.extra_requirements,
        tl.resolve_failures,
    )


def resolve(trees: list[ObjectList], additional: ObjectList) -> list[TaskCallsInTree]:
    """Resolve variables in trees and return task calls per tree.

    Args:
        trees: List of object lists (call trees).
        additional: Additional objects (e.g., inventory) for variable resolution.

    Returns:
        List of TaskCallsInTree, one per tree with resolved taskcalls.
    """
    taskcalls_in_trees = []
    for i, tree in enumerate(trees):
        if not isinstance(tree, ObjectList):
            continue
        if len(tree.items) == 0:
            continue
        first_item = tree.items[0]
        spec = getattr(first_item, "spec", None)
        root_key = spec.key if spec is not None else getattr(first_item, "key", "")
        logger.debug(f"[{i + 1}/{len(trees)}] {root_key}")
        taskcalls = resolve_variables(tree, additional)
        d = TaskCallsInTree(
            root_key=root_key,
            taskcalls=taskcalls,
        )
        taskcalls_in_trees.append(d)
    return taskcalls_in_trees


if __name__ == "__main__":
    __target_type = sys.argv[1]
    __target_name = sys.argv[2]
    __dependency_dir = ""
    if len(sys.argv) >= 4:
        __dependency_dir = sys.argv[3]
    c = ARIScanner(
        root_dir=config.data_dir,
    )
    c.evaluate(
        type=__target_type,
        name=__target_name,
        dependency_dir=__dependency_dir,
    )
