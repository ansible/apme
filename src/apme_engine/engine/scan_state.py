"""SingleScan state container and graph construction helpers for the ARI scanner."""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import cast

from . import logger
from .content_graph import ContentGraph, GraphBuilder
from .findings import Findings
from .graph_scanner import GraphScanReport
from .loader import (
    get_loader_version,
)
from .model_loader import load_object
from .models import (
    ARIResult,
    Load,
    LoadType,
    Object,
    Rule,
    YAMLDict,
    YAMLList,
    YAMLValue,
)
from .parser import Parser
from .utils import (
    escape_local_path,
    escape_url,
    is_local_path,
    split_target_playbook_fullpath,
    split_target_taskfile_fullpath,
)


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
        index: Index data for the scanned target.
        root_definitions: Definitions from the root target.
        ext_definitions: Definitions from external dependencies.
        target_object: Root Object for the scan target.
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
        content_graph: ContentGraph (ADR-044); always built during tree construction.
        graph_scan_report: Results from running GraphRule evaluation on the content graph.
        root_dir: Root data directory from scanner config.
        rules_dir: Directory containing rule definitions.
        rules: List of rule IDs or paths to enable.
        rules_cache: Cached Rule objects.
        persist_dependency_cache: Whether to keep the dependency cache after scan.
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

    index: YAMLDict = field(default_factory=dict)

    root_definitions: YAMLDict = field(default_factory=dict)
    ext_definitions: YAMLDict = field(default_factory=dict)

    target_object: Object = field(default_factory=Object)

    _path_mappings: YAMLDict = field(default_factory=dict)

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

    # ContentGraph (ADR-044) — always populated during tree construction
    content_graph: ContentGraph | None = None
    graph_scan_report: GraphScanReport | None = None

    # the following are set by ARIScanner
    root_dir: str = ""
    rules_dir: str = ""
    rules: list[str] = field(default_factory=list)
    rules_cache: list[Rule] = field(default_factory=list)
    persist_dependency_cache: bool = False
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
            self._path_mappings = {
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
            self._path_mappings = {
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
        from .dependency_loading import make_target_path as _make

        return _make(self.root_dir, self.get_src_root(), typ, target_name, dep_dir)

    def get_src_root(self) -> str:
        """Return the source root directory for the current scan type.

        Returns:
            Path to the src root, or empty string if not set.
        """
        src_val = self._path_mappings.get("src")
        return str(src_val) if src_val is not None else ""

    def is_src_installed(self) -> bool:
        """Check whether the target source is already installed (index exists).

        Returns:
            True if the index file exists, False otherwise.
        """
        index_location = self._path_mappings.get("index")
        return isinstance(index_location, str) and os.path.exists(index_location)

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
        """
        from .dependency_loading import get_definition_path as _get

        return _get(self._path_mappings, ext_type, ext_name)

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
        """
        from .dependency_loading import get_source_path as _get

        return _get(self.root_dir, self._path_mappings, ext_type, ext_name, is_ext_for_project)

    def load_definitions_root(self, target_path: str = "") -> None:
        """Load root definitions (playbooks, roles, etc.) via parser.

        Args:
            target_path: Optional path override for the root target.

        Raises:
            ValueError: If root load is None, parser is not initialized or run fails.
        """
        output_dir_val = self._path_mappings.get("root_definitions")
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

    def build_content_graph(self) -> None:
        """Build ContentGraph from definitions (ADR-044).

        Constructs the graph and copies ``resolve_failures`` from the
        builder's resolution bookkeeping.  ``extra_requirements`` is
        carried forward but currently always empty (reserved for future use).
        Failure is fatal — the scan cannot proceed without a ContentGraph.
        """
        builder = GraphBuilder(
            cast(dict[str, object], self.root_definitions),
            cast(dict[str, object], self.ext_definitions),
        )
        self.content_graph = builder.build()
        self.extra_requirements = cast(YAMLList, builder.extra_requirements)
        self.resolve_failures = cast(YAMLDict, builder.resolve_failures)

        graph_node_count = self.content_graph.node_count()
        graph_edge_count = self.content_graph.edge_count()
        logger.debug(
            "ContentGraph built: %d nodes, %d edges",
            graph_node_count,
            graph_edge_count,
        )

    def build_hierarchy_payload(self, scan_id: str = "") -> YAMLDict:
        """Build OPA input: hierarchy (collection/role/playbook/play/task) + annotations.

        Args:
            scan_id: Optional scan ID; defaults to current UTC timestamp.

        Returns:
            Dict with scan_id, hierarchy (trees with nodes), and metadata.

        Raises:
            ValueError: If ContentGraph has not been built yet.
        """
        from .graph_opa_payload import build_hierarchy_from_graph

        if self.content_graph is None:
            raise ValueError(f"ContentGraph must be built before hierarchy payload (scan: {self.type}/{self.name})")
        self.hierarchy_payload = build_hierarchy_from_graph(
            self.content_graph,
            scan_type=self.type,
            scan_name=self.name,
            collection_name=self.collection_name,
            role_name=self.role_name,
            scan_id=scan_id,
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
                _current += len(val) if isinstance(val, list | dict) else 0
                ext_counts[key] = _current
        root_counts: dict[str, int] = {}
        root_defs_val = self.root_definitions.get("definitions")
        root_defs_dict = root_defs_val if isinstance(root_defs_val, dict) else {}
        for key, val in root_defs_dict.items():
            _current = root_counts.get(key, 0)
            _current += len(val) if isinstance(val, list | dict) else 0
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
        index_location_val = self._path_mappings.get("index")
        index_location = str(index_location_val) if isinstance(index_location_val, str) else ""
        if not index_location:
            return
        with open(index_location) as f:
            self.index = json.load(f)
