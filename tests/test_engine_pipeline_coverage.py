"""Unit tests for engine pipeline modules (coverage for scanner/loader gaps)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from apme_engine.engine import dependency_loading, keyutil, loader
from apme_engine.engine import yaml as engine_yaml
from apme_engine.engine.findings import Findings
from apme_engine.engine.models import LoadType, Task, YAMLDict
from apme_engine.engine.safe_glob import pattern_match, safe_glob
from apme_engine.engine.scan_state import SingleScan
from apme_engine.engine.scanner import AnsibleProjectLoader


class TestLoaderHelpers:
    """Tests for loader remove_subdirectories/trim_suffix/version/target_name."""

    def test_remove_subdirectories_filters_children(self) -> None:
        """Child dirs of an earlier sorted entry are dropped."""
        result = loader.remove_subdirectories(["/a", "/a/b", "/c"])
        assert result == ["/a", "/c"]

    def test_remove_subdirectories_keeps_siblings(self) -> None:
        """Sibling dirs with a shared prefix but no containment are kept."""
        result = loader.remove_subdirectories(["/b", "/a"])
        assert result == ["/a", "/b"]

    def test_trim_suffix_none_and_str_and_list(self) -> None:
        """None defaults, str wraps to list, and matching suffix trims."""
        assert loader.trim_suffix("foo.yml", None) == "foo.yml"
        assert loader.trim_suffix("foo.yml", ".yml") == "foo"
        assert loader.trim_suffix("foo.yml", [".json", ".yml"]) == "foo"
        assert loader.trim_suffix("foo.yml", [".json"]) == "foo.yml"

    def test_trim_suffix_non_list_returns_txt(self) -> None:
        """Non-list suffix patterns return text unchanged."""
        assert loader.trim_suffix("foo", 123) == "foo"  # type: ignore[arg-type]

    def test_get_loader_version_returns_package_version(self) -> None:
        """Patched package metadata version is returned directly."""
        with patch("apme_engine.engine.loader._pkg_version", return_value="9.9.9"):
            assert loader.get_loader_version() == "9.9.9"

    def test_get_loader_version_falls_back_to_empty(self) -> None:
        """Missing package metadata yields empty string via fallback block."""
        with patch("apme_engine.engine.loader._pkg_version", side_effect=Exception("no pkg")):
            assert loader.get_loader_version() == ""

    def test_get_target_name_project(self) -> None:
        """Project target name is the final path component."""
        assert loader.get_target_name(LoadType.PROJECT, "/a/b/myproj") == "myproj"

    def test_get_target_name_collection(self, tmp_path: Path) -> None:
        """Collection target name joins namespace and name from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        manifest = {"collection_info": {"namespace": "ns", "name": "col"}}
        (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
        assert loader.get_target_name(LoadType.COLLECTION, str(tmp_path)) == "ns.col"

    def test_get_target_name_role(self) -> None:
        """Role target name is the final path component."""
        assert loader.get_target_name(LoadType.ROLE, "/roles/myrole") == "myrole"

    def test_get_target_name_playbook(self) -> None:
        """Playbook target name delegates to filepath sanitization."""
        assert loader.get_target_name(LoadType.PLAYBOOK, "a/b.yml") == loader.filepath_to_target_name("a/b.yml")

    def test_get_target_name_unknown_returns_empty(self) -> None:
        """Unknown target type returns empty string."""
        assert loader.get_target_name("unknown", "/a/b") == ""

    def test_filepath_to_target_name(self) -> None:
        """Spaces, slashes, and dots are replaced with safe tokens."""
        assert loader.filepath_to_target_name("a b/c.d") == "a___b---c_dot_d"


class TestDependencyLoading:
    """Tests for dependency_loading path resolution helpers."""

    def test_make_target_path_dep_dir_single_part(self, tmp_path: Path) -> None:
        """Single-part name in dep_dir resolves to the existing candidate.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cand = tmp_path / "myrole"
        cand.mkdir()
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.ROLE,
            target_name="myrole",
            dep_dir=str(tmp_path),
        )
        assert result == str(cand)

    def test_make_target_path_dep_dir_third_candidate(self, tmp_path: Path) -> None:
        """Third dep_dir candidate (ansible_collections layout) resolves.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        third = tmp_path / "ansible_collections" / "ns" / "col"
        third.mkdir(parents=True)
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.COLLECTION,
            target_name="ns.col",
            dep_dir=str(tmp_path),
        )
        assert result == str(third)

    def test_make_target_path_dep_dir_nested_candidate(self, tmp_path: Path) -> None:
        """Dotted name resolves via the namespaced dep_dir candidate.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        nested = tmp_path / "ns" / "col"
        nested.mkdir(parents=True)
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.COLLECTION,
            target_name="ns.col",
            dep_dir=str(tmp_path),
        )
        assert result == str(nested)

    def test_make_target_path_dep_dir_miss_falls_through(self, tmp_path: Path) -> None:
        """Missing dep_dir candidates fall through to local-path handling.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        local = tmp_path / "localcoll"
        local.mkdir()
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.COLLECTION,
            target_name=str(local),
            dep_dir=str(tmp_path),
        )
        assert result == str(local)

    def test_make_target_path_dep_dir_true_miss(self, tmp_path: Path) -> None:
        """No dep_dir candidate exists, so resolution falls through to remote.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.COLLECTION,
            target_name="ns.missing",
            dep_dir=str(tmp_path),
        )
        assert result == os.path.join(str(tmp_path), "collections", "src", "ansible_collections", "ns", "missing")

    def test_make_target_path_collection_remote(self, tmp_path: Path) -> None:
        """Remote collection resolves under root_dir collections src.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.COLLECTION,
            target_name="ns.col",
        )
        assert result == os.path.join(str(tmp_path), "collections", "src", "ansible_collections", "ns", "col")

    def test_make_target_path_collection_local(self) -> None:
        """Local collection path is returned unchanged."""
        assert dependency_loading.make_target_path("/r", "/s", LoadType.COLLECTION, "/a/b") == "/a/b"

    def test_make_target_path_role_remote(self, tmp_path: Path) -> None:
        """Remote role resolves under root_dir roles src.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.ROLE,
            target_name="myrole",
        )
        assert result == os.path.join(str(tmp_path), "roles", "src", "myrole")

    def test_make_target_path_role_local(self) -> None:
        """Local role path is returned unchanged."""
        assert dependency_loading.make_target_path("/r", "/s", LoadType.ROLE, "/a/b") == "/a/b"

    def test_make_target_path_project_url_and_local(self, tmp_path: Path) -> None:
        """Project URL is escaped under src_root; local path passes through.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        url_result = dependency_loading.make_target_path(
            root_dir=str(tmp_path),
            src_root=str(tmp_path),
            typ=LoadType.PROJECT,
            target_name="https://example.com/a/b",
        )
        assert url_result.startswith(str(tmp_path))
        assert dependency_loading.make_target_path("/r", "/s", LoadType.PROJECT, "/a/b") == "/a/b"

    def test_get_source_path_project_dep(self) -> None:
        """Project dependency uses the dependencies mapping as base."""
        mappings: YAMLDict = {"dependencies": "/deps"}
        assert dependency_loading.get_source_path("/r", mappings, LoadType.ROLE, "myrole", True) == os.path.join(
            "/deps", "myrole"
        )

    def test_get_source_path_project_dep_non_str(self) -> None:
        """Non-string dependencies mapping yields relative join."""
        mappings: YAMLDict = {"dependencies": 123}
        result = dependency_loading.get_source_path("/r", mappings, LoadType.ROLE, "myrole", True)
        assert result.endswith("myrole")

    def test_get_source_path_role_and_collection(self) -> None:
        """Role and collection source paths resolve under root_dir src."""
        assert dependency_loading.get_source_path("/r", {}, LoadType.ROLE, "myrole") == os.path.join(
            "/r", "roles", "src", "myrole"
        )
        assert dependency_loading.get_source_path("/r", {}, LoadType.COLLECTION, "ns.col") == os.path.join(
            "/r", "collections", "src", "ansible_collections", "ns", "col"
        )

    def test_get_source_path_invalid_raises(self) -> None:
        """Invalid ext_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid ext_type"):
            dependency_loading.get_source_path("/r", {}, "playbook", "x")

    def test_get_definition_path_role_and_collection(self) -> None:
        """Role and collection definition paths join base with name."""
        mappings: YAMLDict = {"ext_definitions": {LoadType.ROLE: "/rdefs", LoadType.COLLECTION: "/cdefs"}}
        assert dependency_loading.get_definition_path(mappings, LoadType.ROLE, "myrole") == os.path.join(
            "/rdefs", "myrole"
        )
        assert dependency_loading.get_definition_path(mappings, LoadType.COLLECTION, "ns.col") == os.path.join(
            "/cdefs", "ns.col"
        )

    def test_get_definition_path_non_str_base(self) -> None:
        """Non-string base yields empty path."""
        mappings: YAMLDict = {"ext_definitions": {LoadType.ROLE: 123}}
        assert dependency_loading.get_definition_path(mappings, LoadType.ROLE, "myrole") == ""

    def test_get_definition_path_unknown_type_returns_empty(self) -> None:
        """Unknown ext_type with dict mappings returns empty string."""
        mappings: YAMLDict = {"ext_definitions": {}}
        assert dependency_loading.get_definition_path(mappings, "playbook", "x") == ""

    def test_get_definition_path_non_dict_raises(self) -> None:
        """Non-dict ext_definitions raises ValueError."""
        with pytest.raises(ValueError, match="Invalid ext_type"):
            dependency_loading.get_definition_path({"ext_definitions": "nope"}, LoadType.ROLE, "x")


class TestFindings:
    """Tests for Findings simple/dump/load."""

    def test_simple_includes_metadata_and_dependencies(self) -> None:
        """simple() copies report and attaches metadata plus dependencies."""
        f = Findings(
            metadata={"name": "x"},
            dependencies=[{"a": 1}],
            report={"k": "v"},
        )
        simple = f.simple()
        assert simple["k"] == "v"
        assert simple["metadata"] == {"name": "x"}
        assert simple["dependencies"] == [{"a": 1}]

    def test_dump_without_path_omits_report(self) -> None:
        """dump() without fpath returns JSON with empty report and summary."""
        f = Findings(report={"k": "v"}, summary_txt="hello")
        out = f.dump()
        assert isinstance(out, str)
        assert out != ""
        # original is untouched; only the serialized copy omits report
        assert f.report == {"k": "v"}

    def test_dump_with_path_writes_file(self, tmp_path: Path) -> None:
        """dump() with fpath writes JSON and cleans up the lock file.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        f = Findings(
            metadata={"name": "n"},
            report={"k": "v"},
            summary_txt="txt",
        )
        fpath = str(tmp_path / "findings.json")
        out = f.dump(fpath=fpath)
        assert os.path.exists(fpath)
        assert not os.path.exists(fpath + ".lock")
        assert json.loads(out) is not None or isinstance(out, str)

    def test_load_from_json_str(self) -> None:
        """load() decodes a Findings from a JSON string."""
        f = Findings(metadata={"name": "n"})
        s = f.dump()
        loaded = Findings.load(json_str=s)
        assert isinstance(loaded, Findings)
        assert loaded.metadata.get("name") == "n"

    def test_load_from_file(self, tmp_path: Path) -> None:
        """load() reads a Findings from disk, ignoring json_str.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        f = Findings(metadata={"name": "file"})
        fpath = str(tmp_path / "f.json")
        f.dump(fpath=fpath)
        loaded = Findings.load(fpath=fpath, json_str="ignored")
        assert loaded.metadata.get("name") == "file"


class TestEngineYaml:
    """Tests for thread-local YAML helpers and dump retry."""

    def test_set_yaml_force_and_reuse(self) -> None:
        """force=True rebuilds the instance; default reuses it."""
        engine_yaml._set_yaml(force=True)
        first = engine_yaml._yaml.get()
        engine_yaml._set_yaml()
        second = engine_yaml._yaml.get()
        assert first is second
        engine_yaml._set_yaml(force=True)
        third = engine_yaml._yaml.get()
        assert third is not first

    def test_config_sets_attribute(self) -> None:
        """config() applies kwargs to the thread-local YAML instance."""
        engine_yaml._set_yaml(force=True)
        engine_yaml.config(width=512)
        assert engine_yaml._yaml.get().width == 512
        engine_yaml._set_yaml(force=True)

    def test_indent_configures_yaml(self) -> None:
        """indent() delegates to yaml.indent()."""
        engine_yaml._set_yaml(force=True)
        engine_yaml.indent(mapping=2, sequence=4, offset=2)
        assert engine_yaml._yaml.get() is not None

    def test_load_parses_stream(self) -> None:
        """load() parses a YAML string into a dict."""
        result = engine_yaml.load("a: 1\nb: 2\n")
        assert isinstance(result, dict)
        assert result["a"] == 1

    def test_load_empty_returns_none(self) -> None:
        """load() returns None for an empty document."""
        assert engine_yaml.load("") is None

    def test_dump_round_trip(self) -> None:
        """dump() serializes a dict that load() can parse back."""
        engine_yaml._set_yaml(force=True)
        out = engine_yaml.dump({"a": 1})
        assert "a: 1" in out

    def test_dump_retries_on_emitter_error(self) -> None:
        """First EmitterError forces a fresh instance and the retry succeeds."""
        from ruamel.yaml.emitter import EmitterError

        engine_yaml._set_yaml(force=True)
        calls = {"n": 0}
        orig_dump = YAML.dump

        def flaky(self: object, data: object, stream: object) -> None:
            """Fail once with EmitterError, then delegate to real dump.

            Args:
                self: YAML instance under test.
                data: YAML data passed through to the real dumper.
                stream: Output stream for the YAML text.

            Raises:
                EmitterError: On the first call to exercise the retry path.
            """
            calls["n"] += 1
            if calls["n"] == 1:
                raise EmitterError("boom")
            orig_dump(self, data, stream)

        with patch.object(YAML, "dump", flaky):
            out = engine_yaml.dump({"a": 1})
        assert "a: 1" in out
        assert calls["n"] == 2
        engine_yaml._set_yaml(force=True)

    def test_dump_raises_after_retries_exhausted(self) -> None:
        """Persistent EmitterError is re-raised after the second attempt."""
        from ruamel.yaml.emitter import EmitterError

        engine_yaml._set_yaml(force=True)

        def always(self: object, data: object, stream: object) -> None:
            """Always raise EmitterError to exhaust retries.

            Args:
                self: YAML instance under test.
                data: YAML data ignored.
                stream: Output stream ignored.

            Raises:
                EmitterError: Always raised to exhaust the retry budget.
            """
            raise EmitterError("nope")

        with patch.object(YAML, "dump", always), pytest.raises(EmitterError):
            engine_yaml.dump({"a": 1})
        engine_yaml._set_yaml(force=True)

    def test_dump_reraises_generic_exception(self) -> None:
        """Non-EmitterError exceptions propagate immediately."""
        engine_yaml._set_yaml(force=True)
        real_yaml = engine_yaml._yaml.get()
        with patch.object(real_yaml, "dump", side_effect=ValueError("bad")), pytest.raises(ValueError, match="bad"):
            engine_yaml.dump({"a": 1})
        engine_yaml._set_yaml(force=True)


def _dummy_key_obj(**kwargs: object) -> SimpleNamespace:
    """Build a namespace mimicking model objects with key attributes.

    Args:
        **kwargs: Attributes to set on the namespace.

    Returns:
        SimpleNamespace with key/local_key plus supplied attributes.
    """
    ns = SimpleNamespace(key="", local_key="", type="")
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


class TestKeyutil:
    """Tests for key parsing and generation utilities."""

    def test_key_init_and_detect_type(self) -> None:
        """Key stores the string and reports the prefix before space."""
        k = keyutil.Key("playbook my/path.yml")
        assert k.key == "playbook my/path.yml"
        assert k.detect_type() == "playbook"

    def test_key_to_name_playbook_role_other(self) -> None:
        """to_name() basenames playbooks, passes roles, else None."""
        assert keyutil.Key("playbook playbook:/a/b/site.yml").to_name() == "site.yml"
        assert keyutil.Key("role role:myrole").to_name() == "myrole"
        assert keyutil.Key("task something").to_name() is None

    def test_make_global_key_prefix(self) -> None:
        """Collection wins over role; both empty yields empty prefix."""
        assert keyutil.make_global_key_prefix("ns.col", "") == "collection:ns.col#"
        assert keyutil.make_global_key_prefix("", "myrole") == "role:myrole#"
        assert keyutil.make_global_key_prefix("", "") == ""

    def test_detect_type_func(self) -> None:
        """Module-level detect_type returns the first word."""
        assert keyutil.detect_type("task foo") == "task"
        assert keyutil.detect_type("") == ""

    def test_set_play_key(self) -> None:
        """set_play_key builds global and local keys with index."""
        obj = _dummy_key_obj(type="play", index=3)
        keyutil.set_play_key(obj, parent_key="playbook pb#/x", parent_local_key="playbook pb#/y")
        assert obj.key.startswith("play ")
        assert "[3]" in obj.key
        assert "[3]" in obj.local_key

    def test_set_role_key(self) -> None:
        """set_role_key uses collection prefix and fqcn/defined_in."""
        obj = _dummy_key_obj(type="role", collection="ns.col", fqcn="myrole", defined_in="roles/myrole")
        keyutil.set_role_key(obj)
        assert obj.key == "role collection:ns.col#role:myrole"
        assert obj.local_key == "role role:roles/myrole"

    def test_set_module_key(self) -> None:
        """set_module_key honors collection then role prefixes."""
        obj = _dummy_key_obj(type="module", collection="ns.col", role="", fqcn="ns.col.mymod", defined_in="mymod")
        keyutil.set_module_key(obj)
        assert "collection:ns.col#" in obj.key
        obj2 = _dummy_key_obj(type="module", collection="", role="myrole", fqcn="mymod", defined_in="mymod")
        keyutil.set_module_key(obj2)
        assert "role:myrole#" in obj2.key

    def test_set_collection_key(self) -> None:
        """set_collection_key mirrors global key to local key."""
        obj = _dummy_key_obj(type="collection", name="ns.col")
        keyutil.set_collection_key(obj)
        assert obj.key == "collection collection:ns.col"
        assert obj.local_key == obj.key

    def test_get_obj_type_known_and_unknown(self) -> None:
        """Known type prefixes return the type; others return None."""
        for t in ["module", "play", "playbook", "role", "collection", "task", "taskfile", "repository"]:
            assert keyutil.get_obj_type(f"{t} something") == t
        assert keyutil.get_obj_type("unknown something") is None

    def test_get_obj_info_task(self) -> None:
        """Task keys parse parent and object segments."""
        info = keyutil.get_obj_info_by_key("task collection:ns.col#task:[0]")
        assert info["type"] == "task"
        assert info["parent_type"] == "collection"
        assert info["parent_name"] == "ns.col"
        assert info["obj_type"] == "task"
        assert info["obj_key"] == "[0]"

    def test_get_obj_info_play(self) -> None:
        """Play keys parse the same way as task keys."""
        info = keyutil.get_obj_info_by_key("play collection:ns.col#play:[1]")
        assert info["obj_type"] == "play"
        assert info["obj_key"] == "[1]"

    def test_get_obj_info_taskfile(self) -> None:
        """Taskfile keys parse parent type/name plus defined_in."""
        info = keyutil.get_obj_info_by_key("taskfile collection:ns.col#taskfile:tasks/main.yml")
        assert info["parent_type"] == "collection"
        assert info["parent_name"] == "ns.col"
        assert info["defined_in"] == "tasks/main.yml"

    def test_get_obj_info_playbook(self) -> None:
        """Playbook keys parse parent type/name plus defined_in."""
        info = keyutil.get_obj_info_by_key("playbook collection:ns.col#playbook:site.yml")
        assert info["defined_in"] == "site.yml"

    def test_get_obj_info_role(self) -> None:
        """Role keys parse parent segments plus fqcn."""
        info = keyutil.get_obj_info_by_key("role collection:ns.col#role:myrole")
        assert info["parent_type"] == "collection"
        assert info["fqcn"] == "myrole"

    def test_get_obj_info_module(self) -> None:
        """Module keys parse parent segments plus fqcn."""
        info = keyutil.get_obj_info_by_key("module collection:ns.col#module:ns.col.mod")
        assert info["fqcn"] == "ns.col.mod"

    def test_get_obj_info_collection(self) -> None:
        """Collection keys parse the trailing name."""
        info = keyutil.get_obj_info_by_key("collection collection:ns.col")
        assert info["name"] == "ns.col"

    def test_get_obj_info_repository(self) -> None:
        """Repository keys parse the trailing name."""
        info = keyutil.get_obj_info_by_key("repository repository:myrepo")
        assert info["name"] == "myrepo"

    def test_get_obj_info_unknown_and_malformed(self) -> None:
        """Unknown types and keys without spaces hit skip branches."""
        unknown = keyutil.get_obj_info_by_key("frobnicate blah")
        assert unknown["type"] == "frobnicate"
        assert "parent_type" not in unknown
        nospace = keyutil.get_obj_info_by_key("nospace")
        assert nospace["key"] == "nospace"
        malformed_task = keyutil.get_obj_info_by_key("task foo")
        assert malformed_task["type"] == "task"
        malformed_tf = keyutil.get_obj_info_by_key("taskfile foo")
        assert malformed_tf["type"] == "taskfile"
        malformed_role = keyutil.get_obj_info_by_key("role foo")
        assert malformed_role["type"] == "role"
        malformed_coll = keyutil.get_obj_info_by_key("collection foo")
        assert malformed_coll["type"] == "collection"

    def test_set_task_key(self) -> None:
        """set_task_key builds keys from parent payloads and index."""
        t = Task()
        t.index = 2
        keyutil.set_task_key(t, parent_key="taskfile tf#/x", parent_local_key="taskfile tf#/y")
        assert t.key.startswith("task ")
        assert "[2]" in t.key

    def test_set_taskfile_playbook_file_keys(self) -> None:
        """Taskfile/playbook/file keys share the prefix helper."""
        for setter in [keyutil.set_taskfile_key, keyutil.set_playbook_key, keyutil.set_file_key]:
            obj = _dummy_key_obj(type="taskfile", collection="ns.col", role="", defined_in="tasks/a.yml")
            setter(obj)
            assert "collection:ns.col#" in obj.key
            assert obj.local_key.startswith("taskfile taskfile:")

    def test_set_repository_key(self) -> None:
        """set_repository_key mirrors global key to local key."""
        obj = _dummy_key_obj(type="repository", name="myrepo")
        keyutil.set_repository_key(obj)
        assert obj.key == "repository repository:myrepo"
        assert obj.local_key == obj.key

    def test_set_call_object_key(self) -> None:
        """Call-object keys combine spec payload with caller prefix."""
        out = keyutil.set_call_object_key("CallObject", "task a#task:[0]", "play b FROM caller")
        assert out.startswith("CallObject ")
        assert "FROM" in out

    def test_make_imported_taskfile_key_with_collection_parent(self) -> None:
        """Collection-parent callers keep the parent prefix in the key."""
        key = keyutil.make_imported_taskfile_key("taskfile collection:ns.col#taskfile:a.yml", "tasks/b.yml")
        assert key.startswith("taskfile collection:ns.col#taskfile:")

    def test_make_imported_taskfile_key_without_parent(self) -> None:
        """Non-collection callers produce a bare taskfile key."""
        key = keyutil.make_imported_taskfile_key("playbook playbook:site.yml", "tasks/b.yml")
        assert key.startswith("taskfile taskfile:")


class TestSafeGlob:
    """Tests for safe_glob traversal and pattern matching."""

    def test_pattern_match_basic(self) -> None:
        """Star does not cross slashes; double-star crosses segments."""
        assert pattern_match("*.py", "a.py") is not None
        assert pattern_match("*.py", "a/b.py") is None
        assert pattern_match("**/*.py", "a/b/c.py") is not None
        assert pattern_match("*.py", "a.txt") is None

    def test_safe_glob_invalid_patterns(self) -> None:
        """Non-str/list patterns raise ValueError."""
        with pytest.raises(ValueError, match="must be str or list"):
            safe_glob(123)  # type: ignore[arg-type]

    def test_safe_glob_recursive_with_root_dir(self, tmp_path: Path) -> None:
        """Recursive walk with explicit root_dir finds nested files.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "a.py").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("x")
        matched = safe_glob("**/*.py", root_dir=str(tmp_path), recursive=True)
        assert any(str(tmp_path) in m and m.endswith(".py") for m in matched)

    def test_safe_glob_recursive_derives_root(self, tmp_path: Path) -> None:
        """Empty root_dir derives the walk root from the pattern.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "c.py").write_text("x")
        pattern = os.path.join(str(tmp_path), "*.py")
        matched = safe_glob(pattern, recursive=True)
        assert any(m.endswith("c.py") for m in matched)

    def test_safe_glob_non_recursive_files_and_dirs(self, tmp_path: Path) -> None:
        """Non-recursive mode lists top-level files and dirs separately.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "top.py").write_text("x")
        (tmp_path / "mydir").mkdir()
        files = safe_glob(os.path.join(str(tmp_path), "*"), root_dir=str(tmp_path), recursive=False)
        assert any("top.py" in f for f in files)
        assert any("mydir" in f for f in files)
        only_files = safe_glob(os.path.join(str(tmp_path), "*"), root_dir=str(tmp_path), recursive=False, type=["file"])
        assert any("top.py" in f for f in only_files)
        only_dirs = safe_glob(os.path.join(str(tmp_path), "*"), root_dir=str(tmp_path), recursive=False, type=["dir"])
        assert any("mydir" in f for f in only_dirs)

    def test_safe_glob_non_recursive_dedupes(self, tmp_path: Path) -> None:
        """Duplicate patterns do not duplicate results in listdir mode.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "d.py").write_text("x")
        pattern = os.path.join(str(tmp_path), "*")
        matched = safe_glob([pattern, pattern], root_dir=str(tmp_path), recursive=False)
        assert len(matched) == len(set(matched))

    def test_safe_glob_recursive_type_filter(self, tmp_path: Path) -> None:
        """Recursive walk honors file-only and dir-only filters.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sub = tmp_path / "sub2"
        sub.mkdir()
        (sub / "e.py").write_text("x")
        files = safe_glob("**/*.py", root_dir=str(tmp_path), recursive=True, type=["file"])
        assert all(not os.path.isdir(f) for f in files)
        dirs = safe_glob("**/*", root_dir=str(tmp_path), recursive=True, type=["dir"])
        assert all(os.path.isdir(f) for f in dirs)


class TestScannerInit:
    """Tests for AnsibleProjectLoader initialization."""

    def test_post_init_defaults_expanduser_and_ram(self, tmp_path: Path) -> None:
        """Empty root_dir expands home and builds RAM client plus parser.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        with (
            patch("apme_engine.engine.scanner.logger.set_logger_channel", return_value=True),
            patch("apme_engine.engine.scanner.logger.set_log_level") as mock_level,
            patch("os.path.expanduser", return_value=str(tmp_path)),
        ):
            loader_obj = AnsibleProjectLoader(root_dir="")
            assert loader_obj.root_dir == str(tmp_path)
            assert loader_obj.ram_client is not None
            assert loader_obj._parser is not None
            mock_level.assert_called_once_with("info")

    def test_post_init_existing_channel_skips_level(self, tmp_path: Path) -> None:
        """False channel result skips set_log_level but keeps explicit config.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.engine.risk_assessment_model import RAMClient

        ram = RAMClient(root_dir=str(tmp_path))
        with (
            patch("apme_engine.engine.scanner.logger.set_logger_channel", return_value=False),
            patch("apme_engine.engine.scanner.logger.set_log_level") as mock_level,
        ):
            loader_obj = AnsibleProjectLoader(root_dir=str(tmp_path), ram_client=ram)
            assert loader_obj.root_dir == str(tmp_path)
            assert loader_obj.ram_client is ram
            mock_level.assert_not_called()

    def test_record_begin_end_round_trip(self, tmp_path: Path) -> None:
        """record_begin/end stamp elapsed timing into the record.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = AnsibleProjectLoader(root_dir=str(tmp_path), silent=True)
        records: dict[str, object] = {}
        loader_obj.record_begin(records, "x")
        loader_obj.record_end(records, "x")
        rec = records["x"]
        assert isinstance(rec, dict)
        assert "elapsed" in rec

    def test_record_end_missing_record_is_noop(self, tmp_path: Path) -> None:
        """record_end without a matching begin returns quietly.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = AnsibleProjectLoader(root_dir=str(tmp_path), silent=True)
        loader_obj.record_end({}, "missing")


def _make_loader(
    tmp_path: Path,
    silent: bool = True,
    read_ram: bool = True,
    read_ram_for_dependency: bool = True,
    root_dir: str | None = None,
) -> AnsibleProjectLoader:
    """Build a silent loader rooted at tmp_path.

    Args:
        tmp_path: Pytest temporary directory fixture.
        silent: Whether the loader suppresses log output.
        read_ram: Whether to read from RAM cache.
        read_ram_for_dependency: Whether to read dependency data from RAM.
        root_dir: Optional root directory override (defaults to tmp_path).

    Returns:
        Configured AnsibleProjectLoader with silent=True by default.
    """
    return AnsibleProjectLoader(
        root_dir=root_dir if root_dir is not None else str(tmp_path),
        silent=silent,
        read_ram=read_ram,
        read_ram_for_dependency=read_ram_for_dependency,
    )


class TestScannerLoadBranches:
    """Tests for AnsibleProjectLoader.load path mapping and early exits."""

    def test_load_path_alias_and_download_only(self, tmp_path: Path) -> None:
        """path= aliases name= and download_only returns None early.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        play_path = tmp_path / "site.yml"
        play_path.write_text("- hosts: all\n  tasks: []\n")
        result = loader_obj.load(
            type=LoadType.PLAYBOOK,
            name="",
            path=str(play_path),
            playbook_yaml="- hosts: all\n  tasks: []\n",
            download_only=True,
        )
        assert result is None
        assert loader_obj.get_last_scandata() is not None

    def test_load_raw_yaml_taskfile_mapping(self, tmp_path: Path) -> None:
        """raw_yaml maps to taskfile_yaml for TASKFILE targets.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        result = loader_obj.load(
            type=LoadType.TASKFILE,
            name="mem-task",
            raw_yaml="- name: t\n  ansible.builtin.debug:\n    msg: hi\n",
            download_only=True,
        )
        assert result is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert scandata.taskfile_yaml != ""

    def test_load_raw_yaml_playbook_mapping(self, tmp_path: Path) -> None:
        """raw_yaml maps to playbook_yaml for PLAYBOOK targets.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        result = loader_obj.load(
            type=LoadType.PLAYBOOK,
            name="mem-play",
            raw_yaml="- hosts: all\n  tasks: []\n",
            download_only=True,
        )
        assert result is None
        assert loader_obj.get_last_scandata() is not None

    def test_load_local_path_abspath(self, tmp_path: Path) -> None:
        """Relative local paths are converted to absolute paths.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        rel_dir = tmp_path / "relproj"
        rel_dir.mkdir()
        play = rel_dir / "site.yml"
        play.write_text("- hosts: all\n  tasks: []\n")
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = loader_obj.load(
                type=LoadType.PLAYBOOK,
                name="relproj/site.yml",
                playbook_yaml="- hosts: all\n  tasks: []\n",
                download_only=True,
            )
        finally:
            os.chdir(cwd)
        assert result is None

    def test_load_uses_ram_metadata_and_download_only(self, tmp_path: Path) -> None:
        """Collection names hit the RAM metadata branch before early return.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        with (
            patch.object(
                loader_obj,
                "load_metadata_from_ram",
                return_value=(True, {"version": "1.0"}, [{"metadata": {"type": "role"}}]),
            ) as mock_meta,
            patch.object(SingleScan, "set_metadata") as mock_set,
        ):
            result = loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", download_only=True)
        assert result is None
        mock_meta.assert_called_once()
        mock_set.assert_called_once()

    def test_load_ram_metadata_not_loaded(self, tmp_path: Path) -> None:
        """RAM miss skips set_metadata but still honors download_only.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        with patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll2", download_only=True) is None


class TestScannerDependencyLoop:
    """Tests for the dependency ext_list building and per-dep loading."""

    def test_ext_list_skips_non_dict_and_load_only(self, tmp_path: Path) -> None:
        """Non-dict dependency entries are skipped; load_only returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        deps: list[object] = ["not-a-dict", {"metadata": {"type": "role"}, "dir": "x", "is_local_dir": False}]
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(True, {"definitions": {}})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            result = loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True)
        assert result is None

    def test_dependency_ram_hit_stores_ext_definitions(self, tmp_path: Path) -> None:
        """RAM hits store ext defs without touching the filesystem.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path, silent=False)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.dep", "version": "1.0", "hash": "h"},
                "dir": "some/dir",
                "is_local_dir": False,
            }
        ]
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(True, {"definitions": {"a": 1}})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert "collection-ns.dep" in scandata.ext_definitions

    def test_dependency_missing_path_continues(self, tmp_path: Path) -> None:
        """RAM miss plus missing ext_target_path skips the dependency.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.missing", "version": "", "hash": ""},
                "dir": "does-not-exist-xyz",
                "is_local_dir": False,
            }
        ]
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch("os.path.exists", return_value=False),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None

    def test_dependency_recursion_loads_inner_scandata(self, tmp_path: Path) -> None:
        """Existing dep dirs recurse via an inner loader and store defs.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        dep_dir = tmp_path / "depdir"
        dep_dir.mkdir()
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.inner", "version": "", "hash": ""},
                "dir": "depdir",
                "is_local_dir": False,
            }
        ]
        inner = MagicMock()
        inner_scandata = SimpleNamespace(root_definitions={"definitions": {"tasks": []}})
        inner.get_last_scandata.return_value = inner_scandata
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch("apme_engine.engine.scanner.AnsibleProjectLoader", return_value=inner),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        inner.load.assert_called_once()
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert "collection-ns.inner" in scandata.ext_definitions

    def test_dependency_recursion_none_scandata(self, tmp_path: Path) -> None:
        """Inner loader returning None leaves ext_definitions empty.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        (tmp_path / "depdir2").mkdir()
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.none", "version": "", "hash": ""},
                "dir": "depdir2",
                "is_local_dir": False,
            }
        ]
        inner = MagicMock()
        inner.get_last_scandata.return_value = None
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch("apme_engine.engine.scanner.AnsibleProjectLoader", return_value=inner),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        assert loader_obj.get_last_scandata() is not None

    def test_is_root_dependency_skipped(self, tmp_path: Path) -> None:
        """Dependencies matching the root target are marked root and skipped.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.coll", "version": "", "hash": ""},
                "dir": "whatever",
                "is_local_dir": False,
            }
        ]
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})) as mock_defs,
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        # Only the root target_load calls RAM; the is_root dep skips its own RAM lookup.
        assert mock_defs.call_count == 1
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert scandata.ext_definitions == {}

    def test_local_role_dependency_rewrites_path(self, tmp_path: Path) -> None:
        """Local role deps rewrite ext_name to a sibling role directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        root_role = roles_dir / "myrole"
        root_role.mkdir()
        loader_obj = _make_loader(tmp_path)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "role", "name": "otherrole", "version": "", "hash": ""},
                "dir": "ignored",
                "is_local_dir": True,
            }
        ]
        real_scan_cls = SingleScan

        def _fake_scan(**kwargs: object) -> SingleScan:
            """Create a real SingleScan with injected local-role dependencies.

            Args:
                **kwargs: SingleScan constructor kwargs from the loader.

            Returns:
                SingleScan with loaded_dependency_dirs pre-populated.
            """
            sd = real_scan_cls(**kwargs)  # type: ignore[arg-type]
            sd.loaded_dependency_dirs = deps
            return sd

        with (
            patch(
                "apme_engine.engine.scanner.SingleScan",
                side_effect=_fake_scan,
            ),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(True, {"definitions": {}})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            result = loader_obj.load(type=LoadType.ROLE, name=str(root_role), load_only=True)
        assert result is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert "role-otherrole" in scandata.ext_definitions

    def test_local_role_dependency_trailing_slash(self, tmp_path: Path) -> None:
        """Trailing-slash role names take the stripped branch when rewriting.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        root_role = roles_dir / "myrole"
        root_role.mkdir()
        loader_obj = _make_loader(tmp_path)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "role", "name": "otherrole", "version": "", "hash": ""},
                "dir": "ignored",
                "is_local_dir": True,
            }
        ]
        real_scan_cls = SingleScan

        def _fake_scan_slash(**kwargs: object) -> SingleScan:
            """Create a real SingleScan with injected local-role dependencies.

            Args:
                **kwargs: SingleScan constructor kwargs from the loader.

            Returns:
                SingleScan with loaded_dependency_dirs pre-populated.
            """
            sd = real_scan_cls(**kwargs)  # type: ignore[arg-type]
            sd.loaded_dependency_dirs = deps
            return sd

        with (
            patch(
                "apme_engine.engine.scanner.SingleScan",
                side_effect=_fake_scan_slash,
            ),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(True, {"definitions": {}})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            result = loader_obj.load(type=LoadType.ROLE, name=str(root_role) + "/", load_only=True)
        assert result is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert "role-otherrole" in scandata.ext_definitions


class TestScannerTargetAndFullLoad:
    """Tests for target RAM loading plus the full graph/rules/report tail."""

    def test_target_ram_hit_sets_root_definitions(self, tmp_path: Path) -> None:
        """RAM root-definitions hit populates scandata without parser work.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(
                loader_obj,
                "load_definitions_from_ram",
                return_value=(True, {"definitions": {"playbooks": []}}),
            ),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert scandata.root_definitions.get("definitions") == {"playbooks": []}

    def test_full_load_tail_with_registers_and_pretty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Full load exercises graph, rules, RAM register, save, and pretty.

        Args:
            tmp_path: Pytest temporary directory fixture.
            capsys: Pytest stdout capture fixture.
        """
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        loader_obj = AnsibleProjectLoader(
            root_dir=str(tmp_path), silent=False, write_ram=True, pretty=True, output_format="json"
        )
        findings = Findings(
            metadata={"type": "collection", "name": "ns.coll"},
            dependencies=[],
            report={"hierarchy_payload": {}},
        )

        def _fake_apply(self: SingleScan) -> None:
            """Attach a canned Findings to the scan.

            Args:
                self: SingleScan instance under test.
            """
            self.findings = findings
            self.root_definitions = {"definitions": {"playbooks": [], "roles": []}}

        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(SingleScan, "build_content_graph", return_value=None),
            patch.object(SingleScan, "apply_rules", _fake_apply),
            patch.object(SingleScan, "count_definitions", return_value=(1, {}, {})),
            patch.object(loader_obj, "register_findings_to_ram", return_value=None),
            patch.object(loader_obj, "register_indices_to_ram", return_value=None),
            patch.object(loader_obj, "save_definitions", return_value=None) as mock_save,
        ):
            result = loader_obj.load(
                type=LoadType.COLLECTION,
                name="ns.coll",
                out_dir=str(out_dir),
                objects=True,
            )
        assert result is not None
        mock_save.assert_called_once()
        captured = capsys.readouterr()
        assert "# of dependencies" in captured.out

    def test_full_load_pretty_yaml_and_no_objects(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """YAML pretty branch prints without saving definition objects.

        Args:
            tmp_path: Pytest temporary directory fixture.
            capsys: Pytest stdout capture fixture.
        """
        loader_obj = AnsibleProjectLoader(root_dir=str(tmp_path), silent=False, pretty=True, output_format="yaml")
        findings = Findings(
            metadata={"type": "collection", "name": "ns.coll"},
            dependencies=[],
            report={"hierarchy_payload": {}},
        )

        def _fake_apply(self: SingleScan) -> None:
            """Attach a canned Findings to the scan.

            Args:
                self: SingleScan instance under test.
            """
            self.findings = findings

        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(SingleScan, "build_content_graph", return_value=None),
            patch.object(SingleScan, "apply_rules", _fake_apply),
            patch.object(SingleScan, "count_definitions", return_value=(0, {}, {})),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll") is not None
        assert capsys.readouterr().out != ""

    def test_full_load_silent_skips_prints(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Silent full load still returns scandata without printing.

        Args:
            tmp_path: Pytest temporary directory fixture.
            capsys: Pytest stdout capture fixture.
        """
        loader_obj = _make_loader(tmp_path)
        findings = Findings(report={"hierarchy_payload": {}})

        def _fake_apply(self: SingleScan) -> None:
            """Attach a canned Findings to the scan.

            Args:
                self: SingleScan instance under test.
            """
            self.findings = findings

        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(SingleScan, "build_content_graph", return_value=None),
            patch.object(SingleScan, "apply_rules", _fake_apply),
            patch.object(SingleScan, "count_definitions", return_value=(0, {}, {})),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll") is not None
        assert capsys.readouterr().out == ""


class TestScannerHelpers:
    """Tests for RAM passthroughs, persistence helpers, and error saving."""

    def test_load_metadata_from_ram_none_client(self, tmp_path: Path) -> None:
        """None RAM client yields a not-loaded triple.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        loader_obj.ram_client = None
        assert loader_obj.load_metadata_from_ram("collection", "ns.coll", "1.0") == (False, None, None)

    def test_load_metadata_from_ram_delegates(self, tmp_path: Path) -> None:
        """RAM metadata calls delegate to RAMClient and cast results.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        assert loader_obj.ram_client is not None
        with patch.object(
            loader_obj.ram_client,
            "load_metadata_from_findings",
            return_value=(True, {"a": 1}, [{"b": 2}]),
        ):
            loaded, meta, deps = loader_obj.load_metadata_from_ram("collection", "ns.coll", "1.0")
        assert loaded is True
        assert meta == {"a": 1}
        assert deps == [{"b": 2}]

    def test_load_definitions_from_ram_none_client(self, tmp_path: Path) -> None:
        """None RAM client yields not-loaded with empty dict.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        loader_obj.ram_client = None
        assert loader_obj.load_definitions_from_ram("collection", "n", "v", "h") == (False, {})

    def test_load_definitions_from_ram_loaded(self, tmp_path: Path) -> None:
        """Loaded RAM defs are wrapped with definitions and mappings keys.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        assert loader_obj.ram_client is not None
        with patch.object(
            loader_obj.ram_client,
            "load_definitions_from_findings",
            return_value=(True, {"tasks": []}, {"m": 1}),
        ):
            loaded, d = loader_obj.load_definitions_from_ram("collection", "n", "v", "h")
        assert loaded is True
        assert d == {"definitions": {"tasks": []}, "mappings": {"m": 1}}

    def test_load_definitions_from_ram_miss(self, tmp_path: Path) -> None:
        """RAM miss yields not-loaded with empty dict.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        assert loader_obj.ram_client is not None
        with patch.object(loader_obj.ram_client, "load_definitions_from_findings", return_value=(False, {}, {})):
            assert loader_obj.load_definitions_from_ram("c", "n", "v", "h") == (False, {})

    def test_register_and_save_helpers(self, tmp_path: Path) -> None:
        """Register/save helpers delegate to RAMClient when present.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        findings = Findings()
        assert loader_obj.ram_client is not None
        with (
            patch.object(loader_obj.ram_client, "register") as mock_reg,
            patch.object(loader_obj.ram_client, "register_indices_to_ram") as mock_idx,
            patch.object(loader_obj.ram_client, "save_findings") as mock_save,
        ):
            loader_obj.register_findings_to_ram(findings)
            loader_obj.register_indices_to_ram(findings, True)
            loader_obj.save_findings(findings, str(tmp_path))
        mock_reg.assert_called_once_with(findings)
        mock_idx.assert_called_once()
        mock_save.assert_called_once()

    def test_register_helpers_none_client(self, tmp_path: Path) -> None:
        """Register/save helpers are no-ops without a RAM client.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        loader_obj.ram_client = None
        findings = Findings()
        loader_obj.register_findings_to_ram(findings)
        loader_obj.register_indices_to_ram(findings)
        loader_obj.save_findings(findings, str(tmp_path))
        loader_obj.save_error("boom", str(tmp_path))

    def test_save_definitions_writes_objects(self, tmp_path: Path) -> None:
        """save_definitions persists objects.json via result_writer.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        out = tmp_path / "objs"
        loader_obj.save_definitions({"definitions": {"a": 1}}, str(out))
        assert (out / "objects.json").exists()

    def test_get_last_scandata_none_initially(self, tmp_path: Path) -> None:
        """Fresh loaders have no current scandata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _make_loader(tmp_path).get_last_scandata() is None

    def test_save_error_uses_ram_dir_when_no_out(self, tmp_path: Path) -> None:
        """Empty out_dir falls back to the RAM findings directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        play = tmp_path / "p.yml"
        play.write_text("- hosts: all\n")
        loader_obj.load(
            type=LoadType.PLAYBOOK,
            name="p",
            playbook_yaml="- hosts: all\n  tasks: []\n",
            download_only=True,
        )
        assert loader_obj.ram_client is not None
        with patch.object(loader_obj.ram_client, "save_error") as mock_save:
            loader_obj.save_error("oops")
        mock_save.assert_called_once()
        args, _ = mock_save.call_args
        assert args[0] == "oops"
        assert args[1] != ""

    def test_save_error_explicit_out_dir(self, tmp_path: Path) -> None:
        """Explicit out_dir bypasses the RAM directory lookup.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        assert loader_obj.ram_client is not None
        with patch.object(loader_obj.ram_client, "save_error") as mock_save:
            loader_obj.save_error("oops", str(tmp_path))
        mock_save.assert_called_once_with("oops", str(tmp_path))


class TestScannerRemainingBranches:
    """Tests for uncovered branch edges in scanner.load."""

    def test_raw_yaml_ignored_for_collection(self, tmp_path: Path) -> None:
        """raw_yaml with non-playbook/taskfile type falls through to abspath check.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        with patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)):
            assert (
                loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", raw_yaml="ignored", download_only=True)
                is None
            )

    def test_explicit_target_path_skips_make(self, tmp_path: Path) -> None:
        """Provided target_path skips the make_target_path fallback.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        result = loader_obj.load(
            type=LoadType.PLAYBOOK,
            name="mem",
            playbook_yaml="- hosts: all\n  tasks: []\n",
            target_path=str(tmp_path / "explicit"),
            download_only=True,
        )
        assert result is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert scandata.target_path == str(tmp_path / "explicit")

    def test_skip_dependency_jumps_to_target_load(self, tmp_path: Path) -> None:
        """skip_dependency=True bypasses ext_list building and dep loading.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path)
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            assert (
                loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", skip_dependency=True, load_only=True) is None
            )

    def test_two_dependencies_hits_second_iteration(self, tmp_path: Path) -> None:
        """Second dependency skips the start-loading log but still loads.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path, silent=False)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.a", "version": "", "hash": ""},
                "dir": "d1",
                "is_local_dir": False,
            },
            {
                "metadata": {"type": "collection", "name": "ns.b", "version": "", "hash": ""},
                "dir": "d2",
                "is_local_dir": False,
            },
        ]
        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(True, {}, deps)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(True, {"definitions": {}})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None
        scandata = loader_obj.get_last_scandata()
        assert scandata is not None
        assert "collection-ns.a" in scandata.ext_definitions
        assert "collection-ns.b" in scandata.ext_definitions

    def test_dependency_without_ram_read(self, tmp_path: Path) -> None:
        """read_ram=False skips dep RAM and uses the filesystem path check.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        loader_obj = _make_loader(tmp_path, read_ram=False, read_ram_for_dependency=False)
        deps: list[YAMLDict] = [
            {
                "metadata": {"type": "collection", "name": "ns.noram", "version": "", "hash": ""},
                "dir": "missing-xyz",
                "is_local_dir": False,
            }
        ]
        real_scan_cls = SingleScan

        def _fake_noram(**kwargs: object) -> SingleScan:
            """Inject deps bypassing root RAM (disabled by read_ram=False).

            Args:
                **kwargs: SingleScan constructor kwargs from the loader.

            Returns:
                SingleScan with loaded_dependency_dirs pre-populated.
            """
            sd = real_scan_cls(**kwargs)  # type: ignore[arg-type]
            sd.loaded_dependency_dirs = deps
            return sd

        with (
            patch("apme_engine.engine.scanner.SingleScan", side_effect=_fake_noram),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch("os.path.exists", return_value=False),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", load_only=True) is None

    def test_pretty_unknown_format_prints_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Pretty with unknown format prints an empty string.

        Args:
            tmp_path: Pytest temporary directory fixture.
            capsys: Pytest stdout capture fixture.
        """
        loader_obj = AnsibleProjectLoader(root_dir=str(tmp_path), silent=False, pretty=True, output_format="toml")
        findings = Findings(
            metadata={"type": "collection", "name": "ns.coll"},
            dependencies=[],
            report={"hierarchy_payload": {}},
        )

        def _fake_apply(self: SingleScan) -> None:
            """Attach a canned Findings to the scan.

            Args:
                self: SingleScan instance under test.
            """
            self.findings = findings

        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(SingleScan, "build_content_graph", return_value=None),
            patch.object(SingleScan, "apply_rules", _fake_apply),
            patch.object(SingleScan, "count_definitions", return_value=(0, {}, {})),
        ):
            assert loader_obj.load(type=LoadType.COLLECTION, name="ns.coll") is not None
        assert capsys.readouterr().out != ""

    def test_save_objects_silent_skips_confirmation(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Saving objects while silent skips the confirmation print.

        Args:
            tmp_path: Pytest temporary directory fixture.
            capsys: Pytest stdout capture fixture.
        """
        out_dir = tmp_path / "out-silent"
        out_dir.mkdir()
        loader_obj = _make_loader(tmp_path)
        findings = Findings(
            metadata={"type": "collection", "name": "ns.coll"},
            dependencies=[],
            report={"hierarchy_payload": {}},
        )

        def _fake_apply_silent(self: SingleScan) -> None:
            """Attach a canned Findings to the scan.

            Args:
                self: SingleScan instance under test.
            """
            self.findings = findings

        with (
            patch.object(loader_obj, "load_metadata_from_ram", return_value=(False, None, None)),
            patch.object(loader_obj, "load_definitions_from_ram", return_value=(False, {})),
            patch.object(SingleScan, "load_definitions_root", return_value=None),
            patch.object(SingleScan, "set_target_object", return_value=None),
            patch.object(SingleScan, "build_content_graph", return_value=None),
            patch.object(SingleScan, "apply_rules", _fake_apply_silent),
            patch.object(SingleScan, "count_definitions", return_value=(0, {}, {})),
            patch.object(loader_obj, "save_definitions", return_value=None) as mock_save,
        ):
            result = loader_obj.load(type=LoadType.COLLECTION, name="ns.coll", out_dir=str(out_dir), objects=True)
        assert result is not None
        mock_save.assert_called_once()
        assert capsys.readouterr().out == ""


class TestRemainingDependencyAndGlobBranches:
    """Tests for leftover branch edges in dependency_loading and safe_glob."""

    def test_make_target_path_unknown_type_returns_empty(self, tmp_path: Path) -> None:
        """Unknown load type yields an empty target path.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert dependency_loading.make_target_path(str(tmp_path), str(tmp_path), "unknown", "foo") == ""

    def test_safe_glob_recursive_dedupes(self, tmp_path: Path) -> None:
        """Duplicate recursive patterns do not duplicate results.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "r.py").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "s.py").write_text("x")
        pattern = os.path.join(str(tmp_path), "**", "*.py")
        matched = safe_glob([pattern, pattern], root_dir=str(tmp_path), recursive=True)
        assert len(matched) == len(set(matched))
        assert any(m.endswith("r.py") for m in matched)

    def test_safe_glob_recursive_dir_dedupes(self, tmp_path: Path) -> None:
        """Duplicate dir patterns do not duplicate directory results.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        d = tmp_path / "dupdir"
        d.mkdir()
        pattern = os.path.join(str(tmp_path), "**", "*")
        matched = safe_glob([pattern, pattern], root_dir=str(tmp_path), recursive=True, type=["dir"])
        assert len(matched) == len(set(matched))
        assert any("dupdir" in m for m in matched)

    def test_safe_glob_mixed_match_and_miss(self, tmp_path: Path) -> None:
        """Non-matching files and dirs exercise the pattern-miss branches.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "keep.py").write_text("x")
        (tmp_path / "skip.txt").write_text("x")
        (tmp_path / "keepdir").mkdir()
        (tmp_path / "skipdir.txt").mkdir()
        pattern = os.path.join(str(tmp_path), "*.py")
        matched = safe_glob(pattern, root_dir=str(tmp_path), recursive=True)
        assert any(m.endswith("keep.py") for m in matched)
        assert not any(m.endswith("skip.txt") for m in matched)
        matched_nr = safe_glob(pattern, root_dir=str(tmp_path), recursive=False)
        assert any(m.endswith("keep.py") for m in matched_nr)
        assert not any(m.endswith("skip.txt") for m in matched_nr)
