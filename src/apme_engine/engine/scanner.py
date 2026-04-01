"""ARI scanner: evaluates collections, roles, playbooks, and taskfiles against rules."""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import cast

import jsonpickle
import yaml

from . import logger
from .findings import Findings
from .models import (
    LoadType,
    Rule,
    YAMLDict,
    YAMLValue,
)
from .parser import Parser
from .risk_assessment_model import RAMClient
from .scan_state import SingleScan as SingleScan
from .scanner_config import Config as Config
from .utils import (
    is_local_path,
    summarize_findings,
)

config = Config()

logger.set_logger_channel(config.logger_key)
logger.set_log_level(config.log_level)


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
    ) -> SingleScan | None:
        """Run a full ARI scan for the given target.

        Args:
            type: Load type (collection, role, playbook, project, taskfile).
            name: Target name (FQCN, path, or URL).
            path: Alias for name when name is empty.
            base_dir: Base directory for path resolution.
            collection_name: Parent collection when scanning a role.
            role_name: Parent role when scanning a taskfile.
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

        Returns:
            SingleScan with findings, or None if download_only or load_only.
        """
        if not name and path:
            name = path

        if raw_yaml:
            if type == LoadType.PLAYBOOK:
                playbook_yaml = raw_yaml
            elif type == LoadType.TASKFILE:
                taskfile_yaml = raw_yaml

        if is_local_path(name) and not playbook_yaml and not taskfile_yaml:
            name = os.path.abspath(name)

        time_records: dict[str, object] = {}

        scandata = SingleScan(
            type=type,
            name=name,
            collection_name=collection_name,
            role_name=role_name,
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
            use_ansible_doc=self.use_ansible_doc,
            do_save=self.do_save,
            silent=self.silent,
            _parser=self._parser,
        )
        self._current = scandata
        if not scandata.target_path:
            scandata.target_path = scandata.make_target_path(scandata.type, scandata.name)
        self.record_end(time_records, "scandata_init")

        self.record_begin(time_records, "metadata_load")
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
                if not self.silent:
                    logger.debug(f'Use metadata for "{scandata.name}" in RAM DB')

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

            self.record_begin(time_records, "dependency_load")
            for i, (ext_type, ext_name, ext_ver, ext_hash, ext_path, is_local_dir) in enumerate(ext_list):
                if not self.silent:
                    if i == 0:
                        logger.info(f"start loading {ext_count} {ext_type}(s)")
                    logger.info(f"[{i + 1}/{ext_count}] {ext_type} {ext_name}")

                is_root = False
                if scandata.type == ext_type and scandata.name == ext_name:
                    is_root = True

                ext_target_path = os.path.join(self.root_dir, ext_path)
                role_name_for_local_dep = ""
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
                        dep_loaded, ext_defs = self.load_definitions_from_ram(ext_type, ext_name, ext_ver, ext_hash)
                        if dep_loaded:
                            scandata.ext_definitions[key] = cast(YAMLValue, ext_defs)
                            if not self.silent:
                                logger.debug(f'Use spec data for "{ext_name}" in RAM DB')

                    if not dep_loaded:
                        if not os.path.exists(ext_target_path):
                            continue

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
                        dep_scanner.evaluate(
                            type=ext_type,
                            name=ext_name,
                            version=ext_ver,
                            hash=ext_hash,
                            target_path=ext_target_path,
                            dependency_dir=scandata.dependency_dir,
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

        self.record_begin(time_records, "prm_load")
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
            playbooks_num = len(_pb) if isinstance(_pb, list | dict) else 0
            roles_num = len(_rl) if isinstance(_rl, list | dict) else 0
            taskfiles_num = len(_tf) if isinstance(_tf, list | dict) else 0
            tasks_num = len(_tk) if isinstance(_tk, list | dict) else 0
            modules_num = len(_md) if isinstance(_md, list | dict) else 0
            logger.debug(
                f"playbooks: {playbooks_num}, roles: {roles_num}, taskfiles: {taskfiles_num}, "
                f"tasks: {tasks_num}, modules: {modules_num}"
            )

        if load_only:
            return None

        self.record_begin(time_records, "graph_construction")
        scandata.build_content_graph()
        self.record_end(time_records, "graph_construction")
        if not self.silent:
            logger.debug("build_content_graph() done")

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

        """
        from .result_writer import save_rule_result

        save_rule_result(findings, out_dir)

    def save_definitions(self, definitions: dict[str, object], out_dir: str) -> None:
        """Save definition objects to objects.json in out_dir.

        Args:
            definitions: Dict with definitions key containing serializable objects.
            out_dir: Output directory path.

        """
        from .result_writer import save_definitions

        save_definitions(definitions, out_dir)

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
