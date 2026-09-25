"""Unit tests for RAMClient risk assessment model loading, search, and index persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from apme_engine.engine.findings import Findings
from apme_engine.engine.models import (
    ActionGroupMetadata,
    Collection,
    ExecutableType,
    Module,
    ModuleMetadata,
    ObjectList,
    Role,
    RoleMetadata,
    Task,
    TaskFile,
    TaskFileMetadata,
    YAMLDict,
    YAMLList,
)
from apme_engine.engine.risk_assessment_model import (
    RAMClient,
    _collect_offspring_objects,
    _get_modules_list,
    _get_roles_list,
    _get_taskfiles_list,
    _get_tasks_list,
    _path_to_collection_name,
    _path_to_reversed_version_num,
    _safe_dict,
    _safe_list,
    _safe_str,
    _version_to_num,
    action_group_index_name,
    module_index_name,
    role_index_name,
    sort_by_version,
    taskfile_index_name,
)


def _make_module(
    name: str = "mymod",
    fqcn: str = "ns.coll.mymod",
    defined_in: str = "lib/mymod.py",
    collection: str = "ns.coll",
) -> Module:
    """Build a minimal Module for registrar and search tests.

    Args:
        name: Short module name.
        fqcn: Fully qualified module name.
        defined_in: Path where the module is defined.
        collection: Collection the module belongs to.

    Returns:
        Configured Module instance.
    """
    mod = Module(name=name, fqcn=fqcn, collection=collection, defined_in=defined_in)
    mod.key = "module collection:" + collection + "#module:" + fqcn
    return mod


def _make_role(
    name: str = "myrole",
    fqcn: str = "ns.coll.myrole",
    defined_in: str = "roles/myrole",
    collection: str = "ns.coll",
) -> Role:
    """Build a minimal Role for registrar and search tests.

    Args:
        name: Role name.
        fqcn: Fully qualified role name.
        defined_in: Path where the role is defined.
        collection: Collection the role belongs to.

    Returns:
        Configured Role instance.
    """
    role = Role(name=name, fqcn=fqcn, defined_in=defined_in, collection=collection)
    role.key = "role collection:" + collection + "#role:" + fqcn
    return role


def _make_taskfile(taskfile_key: str = "taskfile collection:ns.coll#taskfile:tasks/main.yml") -> TaskFile:
    """Build a minimal TaskFile with the given key.

    Args:
        taskfile_key: Full taskfile key string.

    Returns:
        Configured TaskFile instance.
    """
    taskfile = TaskFile(name="main.yml", defined_in="tasks/main.yml", key=taskfile_key)
    return taskfile


def _make_task(
    task_key: str = "task collection:ns.coll#task:[0]",
    task_name: str = "do thing",
    executable: str = "ns.coll.mymod",
    executable_type: str = "Module",
    defined_in: str = "tasks/main.yml",
) -> Task:
    """Build a minimal Task for search tests.

    Args:
        task_key: Full task key string.
        task_name: Human-readable task name.
        executable: Executable reference (module/role/taskfile).
        executable_type: One of Module, Role, TaskFile, or unknown.
        defined_in: Path where the task is defined.

    Returns:
        Configured Task instance.
    """
    task = Task(name=task_name, executable=executable, executable_type=executable_type, defined_in=defined_in)
    task.key = task_key
    return task


def _make_collection(collection_name: str = "ns.coll") -> Collection:
    """Build a minimal Collection with the given name.

    Args:
        collection_name: Collection name.

    Returns:
        Configured Collection instance.
    """
    coll = Collection(name=collection_name)
    coll.key = "collection collection:" + collection_name
    return coll


def _make_findings(
    type_name: str = "collection",
    target_name: str = "ns.coll",
    version: str = "1.0.0",
    hash_value: str = "abc123",
) -> Findings:
    """Build Findings with standard metadata and empty definitions.

    Args:
        type_name: Load type string.
        target_name: Target name string.
        version: Target version string.
        hash_value: Target content hash string.

    Returns:
        Findings instance with metadata set.
    """
    meta: YAMLDict = {"type": type_name, "name": target_name, "version": version, "hash": hash_value}
    return Findings(metadata=meta, root_definitions=cast(YAMLDict, {"definitions": {}, "mappings": {}}))


def _make_client(root: Path) -> RAMClient:
    """Build a RAMClient rooted at the given directory.

    Args:
        root: Root directory for RAM data files.

    Returns:
        RAMClient with empty indices.
    """
    return RAMClient(root_dir=str(root))


def test_safe_str_none_default() -> None:
    """None converts to the empty default string."""
    assert _safe_str(None) == ""


def test_safe_str_none_custom_default() -> None:
    """None converts to a custom default string."""
    assert _safe_str(None, "dflt") == "dflt"


def test_safe_str_value() -> None:
    """String values pass through unchanged."""
    assert _safe_str("hello") == "hello"


def test_safe_str_int_value() -> None:
    """Integer values stringify instead of returning the default."""
    assert _safe_str(5) == "5"


def test_safe_dict_with_dict() -> None:
    """Dict input returns the same dict."""
    assert _safe_dict({"a": "b"}) == {"a": "b"}


def test_safe_dict_with_list() -> None:
    """List input yields an empty dict."""
    assert _safe_dict(["x"]) == {}


def test_safe_dict_with_none() -> None:
    """None input yields an empty dict."""
    assert _safe_dict(None) == {}


def test_safe_dict_with_str() -> None:
    """String input yields an empty dict."""
    assert _safe_dict("nope") == {}


def test_safe_list_with_list() -> None:
    """List input returns an equal list."""
    assert _safe_list(["a", "b"]) == ["a", "b"]


def test_safe_list_with_dict() -> None:
    """Dict input yields an empty list."""
    assert _safe_list({"a": "b"}) == []


def test_safe_list_with_none() -> None:
    """None input yields an empty list."""
    assert _safe_list(None) == []


def test_safe_list_with_str() -> None:
    """String input yields an empty list."""
    assert _safe_list("nope") == []


def test_get_modules_list_objectlist() -> None:
    """ObjectList definitions expose their items."""
    mod = _make_module()
    obj_list = ObjectList(items=[mod])
    defs = cast(YAMLDict, {"modules": obj_list})
    assert _get_modules_list(defs) == [mod]


def test_get_modules_list_plain_list() -> None:
    """Plain list definitions return a copy of the list."""
    mod = _make_module()
    defs = cast(YAMLDict, {"modules": [mod]})
    assert _get_modules_list(defs) == [mod]


def test_get_modules_list_missing() -> None:
    """Missing modules key yields an empty list."""
    assert _get_modules_list({}) == []


def test_get_modules_list_wrong_type() -> None:
    """Non-list modules value yields an empty list."""
    defs = cast(YAMLDict, {"modules": "junk"})
    assert _get_modules_list(defs) == []


def test_get_roles_list_objectlist() -> None:
    """ObjectList role definitions expose their items."""
    role = _make_role()
    obj_list = ObjectList(items=[role])
    defs = cast(YAMLDict, {"roles": obj_list})
    assert _get_roles_list(defs) == [role]


def test_get_roles_list_plain_list() -> None:
    """Plain list role definitions return a copy."""
    role = _make_role()
    defs = cast(YAMLDict, {"roles": [role]})
    assert _get_roles_list(defs) == [role]


def test_get_roles_list_missing() -> None:
    """Missing roles key yields an empty list."""
    assert _get_roles_list({}) == []


def test_get_roles_list_wrong_type() -> None:
    """Non-list roles value yields an empty list."""
    defs = cast(YAMLDict, {"roles": 42})
    assert _get_roles_list(defs) == []


def test_get_taskfiles_list_objectlist() -> None:
    """ObjectList taskfile definitions expose their items."""
    taskfile = _make_taskfile()
    obj_list = ObjectList(items=[taskfile])
    defs = cast(YAMLDict, {"taskfiles": obj_list})
    assert _get_taskfiles_list(defs) == [taskfile]


def test_get_taskfiles_list_plain_list() -> None:
    """Plain list taskfile definitions return a copy."""
    taskfile = _make_taskfile()
    defs = cast(YAMLDict, {"taskfiles": [taskfile]})
    assert _get_taskfiles_list(defs) == [taskfile]


def test_get_taskfiles_list_missing() -> None:
    """Missing taskfiles key yields an empty list."""
    assert _get_taskfiles_list({}) == []


def test_get_taskfiles_list_wrong_type() -> None:
    """Non-list taskfiles value yields an empty list."""
    defs = cast(YAMLDict, {"taskfiles": "junk"})
    assert _get_taskfiles_list(defs) == []


def test_get_tasks_list_objectlist() -> None:
    """ObjectList task definitions expose their items."""
    task = _make_task()
    obj_list = ObjectList(items=[task])
    defs = cast(YAMLDict, {"tasks": obj_list})
    assert _get_tasks_list(defs) == [task]


def test_get_tasks_list_plain_list() -> None:
    """Plain list task definitions return a copy."""
    task = _make_task()
    defs = cast(YAMLDict, {"tasks": [task]})
    assert _get_tasks_list(defs) == [task]


def test_get_tasks_list_missing() -> None:
    """Missing tasks key yields an empty list."""
    assert _get_tasks_list({}) == []


def test_get_tasks_list_wrong_type() -> None:
    """Non-list tasks value yields an empty list."""
    defs = cast(YAMLDict, {"tasks": 7})
    assert _get_tasks_list(defs) == []


def test_collect_offspring_empty_results() -> None:
    """Empty search results append nothing."""
    out: YAMLDict = {}
    out_list: list[YAMLDict] = []
    assert out == {}
    _collect_offspring_objects([], out_list)
    assert out_list == []


def test_collect_offspring_non_dict_first() -> None:
    """Non-dict first result appends nothing."""
    out: list[YAMLDict] = []
    _collect_offspring_objects(cast(list[YAMLDict], ["junk"]), out)
    assert out == []


def test_collect_offspring_missing_key() -> None:
    """First result without offspring_objects appends nothing."""
    out: list[YAMLDict] = []
    _collect_offspring_objects([{"type": "task"}], out)
    assert out == []


def test_collect_offspring_skips_non_dict_entries() -> None:
    """Non-dict offspring entries are skipped."""
    out: list[YAMLDict] = []
    first = cast(YAMLDict, {"offspring_objects": ["junk", 42]})
    _collect_offspring_objects([first], out)
    assert out == []


def test_collect_offspring_skips_missing_object() -> None:
    """Offspring entries without an object are skipped."""
    out: list[YAMLDict] = []
    first = cast(YAMLDict, {"offspring_objects": [{"name": "x"}]})
    _collect_offspring_objects([first], out)
    assert out == []


def test_collect_offspring_skips_object_without_key() -> None:
    """Offspring objects lacking a key attribute are skipped."""

    class _NoKey:
        """Helper without a key attribute."""

    out: list[YAMLDict] = []
    first = cast(YAMLDict, {"offspring_objects": [{"object": _NoKey()}]})
    _collect_offspring_objects([first], out)
    assert out == []


def test_collect_offspring_dedups_by_key() -> None:
    """Duplicate offspring keys appear only once."""
    mod = _make_module()
    entry_one = cast(YAMLDict, {"object": mod})
    entry_two = cast(YAMLDict, {"object": mod})
    first = cast(YAMLDict, {"offspring_objects": [entry_one, entry_two]})
    out: list[YAMLDict] = []
    _collect_offspring_objects([first], out)
    assert out == [entry_one]


def test_collect_offspring_appends_unique() -> None:
    """Distinct offspring keys are all appended."""
    mod_one = _make_module(name="one", fqcn="ns.coll.one")
    mod_two = _make_module(name="two", fqcn="ns.coll.two")
    entry_one = cast(YAMLDict, {"object": mod_one})
    entry_two = cast(YAMLDict, {"object": mod_two})
    first = cast(YAMLDict, {"offspring_objects": [entry_one, entry_two]})
    out: list[YAMLDict] = []
    _collect_offspring_objects([first], out)
    assert out == [entry_one, entry_two]


def test_post_init_no_indices(tmp_path: Path) -> None:
    """Empty root leaves all indices empty.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.module_index == {}
    assert client.role_index == {}
    assert client.taskfile_index == {}
    assert client.action_group_index == {}


def test_post_init_loads_all_indices(tmp_path: Path) -> None:
    """Present index files populate all four indices.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    indices = tmp_path / "indices"
    indices.mkdir()
    (indices / module_index_name).write_text(json.dumps({"m": "v"}))
    (indices / role_index_name).write_text(json.dumps({"r": "v"}))
    (indices / taskfile_index_name).write_text(json.dumps({"t": "v"}))
    (indices / action_group_index_name).write_text(json.dumps({"a": "v"}))
    client = _make_client(tmp_path)
    assert client.module_index == {"m": "v"}
    assert client.role_index == {"r": "v"}
    assert client.taskfile_index == {"t": "v"}
    assert client.action_group_index == {"a": "v"}


def test_post_init_partial_indices(tmp_path: Path) -> None:
    """Only existing index files are loaded.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    indices = tmp_path / "indices"
    indices.mkdir()
    (indices / module_index_name).write_text(json.dumps({"m": "v"}))
    client = _make_client(tmp_path)
    assert client.module_index == {"m": "v"}
    assert client.role_index == {}
    assert client.taskfile_index == {}
    assert client.action_group_index == {}


def test_remove_old_item_under_limit(tmp_path: Path) -> None:
    """Cache under the limit is unchanged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    data: YAMLDict = {"a": "1", "b": "2"}
    client._remove_old_item(data, 5)
    assert data == {"a": "1", "b": "2"}


def test_remove_old_item_at_limit(tmp_path: Path) -> None:
    """Cache exactly at the limit is unchanged.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    data: YAMLDict = {"a": "1", "b": "2"}
    client._remove_old_item(data, 2)
    assert data == {"a": "1", "b": "2"}


def test_remove_old_item_evicts_oldest(tmp_path: Path) -> None:
    """Overflow evicts the oldest inserted keys first.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    data: YAMLDict = {"a": "1", "b": "2", "c": "3", "d": "4"}
    client._remove_old_item(data, 2)
    assert data == {"c": "3", "d": "4"}


def test_clear_old_cache_evicts_all(tmp_path: Path) -> None:
    """All five caches shrink to max_cache_size.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.max_cache_size = 2
    client.findings_cache = {"a": "1", "b": "2", "c": "3"}
    client.module_search_cache = {"a": "1", "b": "2", "c": "3"}
    client.role_search_cache = {"a": "1", "b": "2", "c": "3"}
    client.taskfile_search_cache = {"a": "1", "b": "2", "c": "3"}
    client.task_search_cache = {"a": "1", "b": "2", "c": "3"}
    client.clear_old_cache()
    assert len(client.findings_cache) == 2
    assert len(client.module_search_cache) == 2
    assert len(client.role_search_cache) == 2
    assert len(client.taskfile_search_cache) == 2
    assert len(client.task_search_cache) == 2


def test_clear_old_cache_under_limit_noop(tmp_path: Path) -> None:
    """Small caches are left untouched.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.findings_cache = {"a": "1"}
    client.clear_old_cache()
    assert client.findings_cache == {"a": "1"}


def test_register_saves_and_clears(tmp_path: Path) -> None:
    """Register writes findings to the derived directory.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.save_findings") as mock_save,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.clear_old_cache") as mock_clear,
    ):
        client.register(findings)
    assert mock_save.call_count == 1
    assert mock_clear.call_count == 1
    out_dir = str(mock_save.call_args[0][1])
    assert out_dir == client.make_findings_dir_path("collection", "ns.coll", "1.0.0", "abc123")


def test_register_empty_metadata_defaults(tmp_path: Path) -> None:
    """Missing metadata keys default to empty strings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = Findings(metadata={})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.save_findings") as mock_save,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.clear_old_cache"),
    ):
        client.register(findings)
    out_dir = str(mock_save.call_args[0][1])
    assert out_dir == client.make_findings_dir_path("", "", "", "")


def test_register_indices_delegates(tmp_path: Path) -> None:
    """register_indices_to_ram fans out to all four registrars.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.register_module_index_to_ram") as m_mod,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.register_role_index_to_ram") as m_role,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.register_taskfile_index_to_ram") as m_tf,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.register_action_group_index_to_ram") as m_ag,
    ):
        client.register_indices_to_ram(findings, include_test_contents=True)
    assert m_mod.call_count == 1
    assert m_role.call_count == 1
    assert m_tf.call_count == 1
    assert m_ag.call_count == 1
    assert bool(m_mod.call_args[1]["include_test_contents"]) is True


def test_register_module_index_new_module(tmp_path: Path) -> None:
    """A new module is persisted to the module index.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings)
    loaded = client.load_module_index()
    assert "mymod" in loaded


def test_register_module_index_duplicate_skips_save(tmp_path: Path) -> None:
    """Re-registering the same module performs no second save.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_skips_non_module(tmp_path: Path) -> None:
    """Non-Module entries in the modules list are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    defs = cast(YAMLDict, {"modules": ["junk", 42]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_skips_test_content(tmp_path: Path) -> None:
    """Test-path modules are skipped when the flag is set.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(defined_in="tests/integration/foo.py")
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings, include_test_contents=True)
    assert mock_save.call_count == 0


def test_register_module_index_includes_test_without_flag(tmp_path: Path) -> None:
    """Test-path modules are kept when the flag is unset.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(defined_in="tests/integration/foo.py")
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings, include_test_contents=False)
    assert "mymod" in client.load_module_index()


def test_register_module_index_existing_dict_duplicate(tmp_path: Path) -> None:
    """Dict-encoded existing entries compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_existing_object_duplicate(tmp_path: Path) -> None:
    """ModuleMetadata object entries compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    findings = _make_findings()
    meta = ModuleMetadata.from_module(mod, findings.metadata)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = {"mymod": [meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
            defs = cast(YAMLDict, {"modules": [mod]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_junk_existing_entry(tmp_path: Path) -> None:
    """Junk existing entries are skipped before appending the new module.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"mymod": ["junk"]})
        defs = cast(YAMLDict, {"modules": [mod]})
        findings = _make_findings()
        findings.root_definitions = cast(YAMLDict, {"definitions": defs})
        client.register_module_index_to_ram(findings)
    assert "mymod" in client.load_module_index()


def test_register_module_index_routing_redirect(tmp_path: Path) -> None:
    """Plugin routing redirects create deprecated index entries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": "ns.coll.newmod"}}}})
    defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings)
    assert "oldmod" in client.load_module_index()


def test_register_module_index_routing_empty_redirect(tmp_path: Path) -> None:
    """Routing entries without a redirect are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": ""}}}})
    defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_routing_duplicate(tmp_path: Path) -> None:
    """Duplicate routing redirects are not saved twice.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": "ns.coll.newmod"}}}})
    defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_module_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_routing_junk_existing(tmp_path: Path) -> None:
    """Junk routing entries are skipped before appending.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": "ns.coll.newmod"}}}})
    defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"oldmod": [42]})
        client.register_module_index_to_ram(findings)
    assert "oldmod" in client.load_module_index()


def test_register_module_index_skips_non_collection(tmp_path: Path) -> None:
    """Non-Collection entries in collections are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    defs = cast(YAMLDict, {"modules": [], "collections": ["junk"]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_module_index_collection_no_runtime(tmp_path: Path) -> None:
    """Collections without meta_runtime add no routing entries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = {}
    defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
        client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_role_index_new_role(tmp_path: Path) -> None:
    """A new role is persisted to the role index.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    defs = cast(YAMLDict, {"roles": [role]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_role_index_to_ram(findings)
    assert "ns.coll.myrole" in client.load_role_index()


def test_register_role_index_duplicate(tmp_path: Path) -> None:
    """Re-registering the same role performs no second save.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    defs = cast(YAMLDict, {"roles": [role]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_role_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_role_index") as mock_save:
        client.register_role_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_role_index_skips_non_role(tmp_path: Path) -> None:
    """Non-Role entries are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    defs = cast(YAMLDict, {"roles": ["junk"]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_role_index") as mock_save:
        client.register_role_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_role_index_skips_test_content(tmp_path: Path) -> None:
    """Test-path roles are skipped when the flag is set.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(defined_in="molecule/default")
    defs = cast(YAMLDict, {"roles": [role]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_role_index") as mock_save:
        client.register_role_index_to_ram(findings, include_test_contents=True)
    assert mock_save.call_count == 0


def test_register_role_index_object_duplicate(tmp_path: Path) -> None:
    """RoleMetadata object entries compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    findings = _make_findings()
    meta = RoleMetadata.from_role(role, findings.metadata)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_role_index") as mock_load:
        mock_load.return_value = {"ns.coll.myrole": [meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_role_index") as mock_save:
            defs = cast(YAMLDict, {"roles": [role]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_role_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_role_index_junk_existing(tmp_path: Path) -> None:
    """Junk existing role entries are skipped before appending.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_role_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"ns.coll.myrole": [None]})
        defs = cast(YAMLDict, {"roles": [role]})
        findings = _make_findings()
        findings.root_definitions = cast(YAMLDict, {"definitions": defs})
        client.register_role_index_to_ram(findings)
    assert "ns.coll.myrole" in client.load_role_index()


def test_register_taskfile_index_new(tmp_path: Path) -> None:
    """A new taskfile is persisted to the taskfile index.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    defs = cast(YAMLDict, {"taskfiles": [taskfile]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_taskfile_index_to_ram(findings)
    assert taskfile.key in client.load_taskfile_index()


def test_register_taskfile_index_duplicate(tmp_path: Path) -> None:
    """Re-registering the same taskfile performs no second save.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    defs = cast(YAMLDict, {"taskfiles": [taskfile]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_taskfile_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_taskfile_index") as mock_save:
        client.register_taskfile_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_taskfile_index_skips_non_taskfile(tmp_path: Path) -> None:
    """Non-TaskFile entries are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    defs = cast(YAMLDict, {"taskfiles": ["junk"]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_taskfile_index") as mock_save:
        client.register_taskfile_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_taskfile_index_skips_test_content(tmp_path: Path) -> None:
    """Test-path taskfiles are skipped when the flag is set.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    taskfile.defined_in = "tests/integration/tasks.yml"
    defs = cast(YAMLDict, {"taskfiles": [taskfile]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_taskfile_index") as mock_save:
        client.register_taskfile_index_to_ram(findings, include_test_contents=True)
    assert mock_save.call_count == 0


def test_register_taskfile_index_object_duplicate(tmp_path: Path) -> None:
    """TaskFileMetadata object entries compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    findings = _make_findings()
    meta = TaskFileMetadata.from_taskfile(taskfile, findings.metadata)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_taskfile_index") as mock_load:
        mock_load.return_value = {taskfile.key: [meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_taskfile_index") as mock_save:
            defs = cast(YAMLDict, {"taskfiles": [taskfile]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_taskfile_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_taskfile_index_junk_existing(tmp_path: Path) -> None:
    """Junk existing taskfile entries are skipped before appending.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_taskfile_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {taskfile.key: [None]})
        defs = cast(YAMLDict, {"taskfiles": [taskfile]})
        findings = _make_findings()
        findings.root_definitions = cast(YAMLDict, {"definitions": defs})
        client.register_taskfile_index_to_ram(findings)
    assert taskfile.key in client.load_taskfile_index()


def test_register_action_group_new(tmp_path: Path) -> None:
    """Action groups register both short and fully qualified names.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": ["ns.coll.mod1", "ns.coll.mod2"]}})
    defs = cast(YAMLDict, {"collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_action_group_index_to_ram(findings)
    loaded = client.load_action_group_index()
    assert "group/aws" in loaded
    assert "group/ns.coll.aws" in loaded


def test_register_action_group_duplicate(tmp_path: Path) -> None:
    """Re-registering the same action group performs no second save.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": ["ns.coll.mod1"]}})
    defs = cast(YAMLDict, {"collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    client.register_action_group_index_to_ram(findings)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_action_group_index") as mock_save:
        client.register_action_group_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_action_group_skips_non_collection(tmp_path: Path) -> None:
    """Non-Collection entries are ignored.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    defs = cast(YAMLDict, {"collections": ["junk"]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_action_group_index") as mock_save:
        client.register_action_group_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_action_group_no_runtime(tmp_path: Path) -> None:
    """Collections without meta_runtime add no groups.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = {}
    defs = cast(YAMLDict, {"collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_action_group_index") as mock_save:
        client.register_action_group_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_action_group_empty_modules(tmp_path: Path) -> None:
    """Empty group module lists yield no crash and no duplicate crash.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": []}})
    defs = cast(YAMLDict, {"collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    agm_none = ActionGroupMetadata.from_action_group("group/aws", [], findings.metadata)
    assert agm_none is None
    client.register_action_group_index_to_ram(findings)
    loaded = client.load_action_group_index()
    assert "group/aws" in loaded


def test_register_action_group_junk_existing(tmp_path: Path) -> None:
    """Junk existing action group entries are skipped before appending.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": ["ns.coll.mod1"]}})
    defs = cast(YAMLDict, {"collections": [coll]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_action_group_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"group/aws": [None], "group/ns.coll.aws": ["junk"]})
        client.register_action_group_index_to_ram(findings)
    loaded = client.load_action_group_index()
    assert "group/aws" in loaded


def test_register_action_group_object_duplicate(tmp_path: Path) -> None:
    """ActionGroupMetadata object entries compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    modules_list = [_make_module()]
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": modules_list}})
    findings = _make_findings()
    agm1 = ActionGroupMetadata.from_action_group("group/aws", modules_list, findings.metadata)
    agm2 = ActionGroupMetadata.from_action_group("group/ns.coll.aws", modules_list, findings.metadata)
    assert agm1 is not None
    assert agm2 is not None
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_action_group_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"group/aws": [agm1], "group/ns.coll.aws": [agm2]})
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_action_group_index") as mock_save:
            defs = cast(YAMLDict, {"collections": [coll]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_action_group_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_make_findings_dir_path_collection(tmp_path: Path) -> None:
    """Collection paths use the plain name without escaping.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out = client.make_findings_dir_path("collection", "ns.coll", "1.0", "abc")
    assert out == os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "abc")


def test_make_findings_dir_path_project_escapes(tmp_path: Path) -> None:
    """Project paths escape URL characters.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out = client.make_findings_dir_path("project", "https://example.com/a/b", "1.0", "abc")
    assert "https__example.com_a_b" in out
    assert out.startswith(os.path.join(str(tmp_path), "projects"))


def test_make_findings_dir_path_playbook_escapes(tmp_path: Path) -> None:
    """Playbook paths escape URL characters.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out = client.make_findings_dir_path("playbook", "https://example.com/p.yml", "2.0", "h")
    assert out.startswith(os.path.join(str(tmp_path), "playbooks"))


def test_make_findings_dir_path_taskfile_escapes(tmp_path: Path) -> None:
    """Taskfile paths escape URL characters.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out = client.make_findings_dir_path("taskfile", "https://example.com/t.yml", "2.0", "h")
    assert out.startswith(os.path.join(str(tmp_path), "taskfiles"))


def test_make_findings_dir_path_unknown_version_hash(tmp_path: Path) -> None:
    """Empty version and hash become unknown placeholders.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out = client.make_findings_dir_path("role", "myrole", "", "")
    assert out == os.path.join(str(tmp_path), "roles", "findings", "myrole", "unknown", "unknown")


def test_load_metadata_not_found(tmp_path: Path) -> None:
    """Missing findings return a not-loaded tuple.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient._search_findings") as mock_search:
        mock_search.return_value = None
        loaded, meta, deps = client.load_metadata_from_findings("collection", "ns.coll", "1.0")
    assert loaded is False
    assert meta is None
    assert deps is None


def test_load_metadata_non_findings(tmp_path: Path) -> None:
    """Non-Findings search results return a not-loaded tuple.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient._search_findings") as mock_search:
        mock_search.return_value = cast(Findings | None, cast(object, "junk"))
        loaded, meta, deps = client.load_metadata_from_findings("collection", "ns.coll", "1.0")
    assert loaded is False
    assert meta is None
    assert deps is None


def test_load_metadata_success(tmp_path: Path) -> None:
    """Matching findings return metadata and dependencies.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    findings.dependencies = cast(YAMLList, ["dep"])
    with patch("apme_engine.engine.risk_assessment_model.RAMClient._search_findings") as mock_search:
        mock_search.return_value = findings
        loaded, meta, deps = client.load_metadata_from_findings("collection", "ns.coll", "1.0")
    assert loaded is True
    assert meta == findings.metadata
    assert deps == findings.dependencies


def test_load_definitions_missing_file(tmp_path: Path) -> None:
    """Absent findings.json yields empty definitions and mappings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists:
        mock_exists.return_value = False
        loaded, defs, maps = client.load_definitions_from_findings("collection", "ns.coll", "1.0", "abc")
    assert loaded is False
    assert defs == {}
    assert maps == {}


def test_load_definitions_none_findings(tmp_path: Path) -> None:
    """Findings.load returning None yields empty results.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = None
        loaded, defs, maps = client.load_definitions_from_findings("collection", "ns.coll", "1.0", "abc")
    assert loaded is False
    assert defs == {}
    assert maps == {}


def test_load_definitions_unresolved_blocked(tmp_path: Path) -> None:
    """Extra requirements block loading unless allowed.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    findings.extra_requirements = cast(YAMLList, ["req"])
    findings.root_definitions = cast(YAMLDict, {"definitions": {"modules": []}, "mappings": {"a": "b"}})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        loaded, _defs, _maps = client.load_definitions_from_findings(
            "collection", "ns.coll", "1.0", "abc", allow_unresolved=False
        )
    assert loaded is False


def test_load_definitions_unresolved_allowed(tmp_path: Path) -> None:
    """Allow-unresolved loads definitions despite extra requirements.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    findings.extra_requirements = cast(YAMLList, ["req"])
    findings.root_definitions = cast(YAMLDict, {"definitions": {"modules": []}, "mappings": {"a": "b"}})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        loaded, defs, maps = client.load_definitions_from_findings(
            "collection", "ns.coll", "1.0", "abc", allow_unresolved=True
        )
    assert loaded is True
    assert defs == {"modules": []}
    assert maps == {"a": "b"}


def test_load_definitions_empty_mappings(tmp_path: Path) -> None:
    """Empty mappings leave the loaded flag false.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": {"modules": []}, "mappings": {}})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        loaded, _defs, _maps = client.load_definitions_from_findings("collection", "ns.coll", "1.0", "abc")
    assert loaded is False


def test_search_builtin_module_cache_hit(tmp_path: Path) -> None:
    """Cached builtin modules avoid the loader call.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(name="ping", fqcn="ansible.builtin.ping", collection="ansible.builtin")
    client.builtin_modules_cache = cast(YAMLDict, {"ping": mod})
    with patch("apme_engine.engine.risk_assessment_model.load_builtin_modules") as mock_loader:
        result = client.search_builtin_module("ping", used_in="/x.yml")
    assert mock_loader.call_count == 0
    assert len(result) == 1
    assert result[0]["name"] == "ansible.builtin.ping"
    assert result[0]["used_in"] == "/x.yml"


def test_search_builtin_module_loads_and_strips_fqcn(tmp_path: Path) -> None:
    """FQCN input strips to the short name before lookup.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(name="ping", fqcn="ansible.builtin.ping", collection="ansible.builtin")
    with patch("apme_engine.engine.risk_assessment_model.load_builtin_modules") as mock_loader:
        mock_loader.return_value = {"ping": mod}
        result = client.search_builtin_module("ansible.builtin.ping")
    assert len(result) == 1
    assert client.builtin_modules_cache != {}


def test_search_builtin_module_miss(tmp_path: Path) -> None:
    """Unknown builtin names return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with patch("apme_engine.engine.risk_assessment_model.load_builtin_modules") as mock_loader:
        mock_loader.return_value = {}
        result = client.search_builtin_module("nosuchmod")
    assert result == []


def test_load_from_indice_collection(tmp_path: Path) -> None:
    """Collection-typed metadata builds a module wrapper.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    meta = cast(
        YAMLDict, {"type": "collection", "name": "ns.coll", "fqcn": "ns.coll.mymod", "version": "1.0", "hash": "h"}
    )
    wrapper = client.load_from_indice("mymod", meta, used_in="/u.yml")
    assert wrapper["type"] == "module"
    assert wrapper["name"] == "ns.coll.mymod"
    assert wrapper["used_in"] == "/u.yml"
    assert cast(Module, cast(object, wrapper["object"])).collection == "ns.coll"
    assert cast(YAMLDict, cast(object, wrapper["defined_in"]))["name"] == "ns.coll"


def test_load_from_indice_role(tmp_path: Path) -> None:
    """Role-typed metadata records the role name.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    meta = cast(YAMLDict, {"type": "role", "name": "myrole", "fqcn": "ns.coll.mymod", "version": "1.0", "hash": "h"})
    wrapper = client.load_from_indice("mymod", meta)
    assert cast(Module, cast(object, wrapper["object"])).role == "myrole"
    assert cast(YAMLDict, cast(object, wrapper["defined_in"]))["name"] == "myrole"


def test_load_from_indice_other_type(tmp_path: Path) -> None:
    """Unknown index types leave collection and role empty.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    meta = cast(YAMLDict, {"type": "", "name": "n", "fqcn": "f.q.m", "version": "", "hash": ""})
    wrapper = client.load_from_indice("m", meta)
    assert wrapper["type"] == "module"
    assert cast(YAMLDict, cast(object, wrapper["defined_in"]))["type"] == "module"


def test_search_module_max_match_zero(tmp_path: Path) -> None:
    """max_match zero short-circuits to an empty list.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_module("anything", max_match=0) == []


def test_search_module_cache_hit(tmp_path: Path) -> None:
    """Cached module searches return without index work.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    import json as _json

    client = _make_client(tmp_path)
    args_str = _json.dumps(["mymod", False, -1, "", ""])
    cached = cast(YAMLDict, {"type": "module"})
    client.module_search_cache[args_str] = cast(object, [cached])  # type: ignore[assignment]
    assert client.search_module("mymod") == [cached]


def test_search_module_builtin_hit(tmp_path: Path) -> None:
    """Builtin matches are cached and returned directly.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    sentinel = cast(YAMLDict, {"type": "module"})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b:
        mock_b.return_value = [sentinel]
        result = client.search_module("ping")
    assert result == [sentinel]
    assert len(client.module_search_cache) == 1


def test_search_module_index_miss(tmp_path: Path) -> None:
    """Names absent from the index return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b:
        mock_b.return_value = []
        result = client.search_module("nosuchmod_xyz")
    assert result == []


def test_search_module_index_empty_list(tmp_path: Path) -> None:
    """Empty index lists behave like a missing index.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.module_index = cast(YAMLDict, {"mymod": []})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b:
        mock_b.return_value = []
        result = client.search_module("mymod")
    assert result == []


def test_search_module_index_path_missing(tmp_path: Path) -> None:
    """Indexed entries without findings files return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
    ):
        mock_b.return_value = []
        mock_exists.return_value = False
        result = client.search_module("mymod")
    assert result == []


def test_search_module_fuzzy_match_via_findings(tmp_path: Path) -> None:
    """Fuzzy short-name matches resolve through the findings cache.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    defs = cast(YAMLDict, {"modules": [mod]})
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": defs})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("mymod")
    assert len(result) == 1
    assert result[0]["name"] == "ns.coll.mymod"


def test_findings_path_parts_windows_backslashes() -> None:
    """Backslash-separated findings paths split into usable components."""
    from apme_engine.engine.risk_assessment_model import _findings_path_parts

    parts = _findings_path_parts(r"C:\cache\collections\findings\ns.coll\1.0.0\abc123\findings.json")
    assert len(parts) >= 6
    assert parts[-4] == "ns.coll"
    assert parts[-3] == "1.0.0"
    assert parts[-2] == "abc123"


def test_search_module_findings_cache_hit(tmp_path: Path) -> None:
    """Populated findings caches avoid Findings.load calls.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    findings_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1", "h", "findings.json")
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    client.findings_cache[findings_path] = cast(object, {"modules": [mod]})  # type: ignore[assignment]
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        result = client.search_module("mymod")
    assert mock_load.call_count == 0
    assert len(result) == 1


def test_search_module_load_not_findings(tmp_path: Path) -> None:
    """Non-Findings loads are skipped gracefully.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = None
        result = client.search_module("mymod")
    assert result == []


def test_search_module_skips_non_module(tmp_path: Path) -> None:
    """Non-Module definitions never match.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": ["junk"]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("mymod")
    assert result == []


def test_search_module_exact_match(tmp_path: Path) -> None:
    """Exact matching requires full FQCN equality.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        matched = client.search_module("ns.coll.mymod", exact_match=True)
    assert len(matched) == 1
    client2 = _make_client(tmp_path)
    client2.module_index = client.module_index
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b2,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
    ):
        mock_b2.return_value = []
        mock_exists2.return_value = True
        mock_load2.return_value = findings
        missed = client2.search_module("ns.coll.other", exact_match=True)
    assert missed == []


def test_search_module_fqcn_suffix_match(tmp_path: Path) -> None:
    """Short names match the trailing FQCN component.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(fqcn="other.coll.mymod")
    client.module_index = cast(
        YAMLDict,
        {
            "mymod": [
                {"fqcn": "other.coll.mymod", "type": "collection", "name": "other.coll", "version": "1", "hash": "h"}
            ]
        },
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("mymod")
    assert len(result) == 1


def test_search_module_fqcn_name_match(tmp_path: Path) -> None:
    """FQCN queries match findings entries by full name.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("ns.coll.mymod")
    assert len(result) == 1


def test_search_module_max_match_limits(tmp_path: Path) -> None:
    """max_match stops the scan after enough matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod_one = _make_module(name="mymod", fqcn="ns.coll.mymod")
    mod_two = _make_module(name="mymod", fqcn="other.mymod")
    mod_two.fqcn = "ns.coll.mymod"
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod_one, mod_two]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("mymod", max_match=1)
    assert len(result) == 1


def test_search_module_deprecated_preference(tmp_path: Path) -> None:
    """FQCN lookups prefer non-deprecated index entries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    client.module_index = cast(
        YAMLDict,
        {
            "mymod": [
                {"fqcn": "ns.coll.mymod", "deprecated": True, "type": "c", "name": "old", "version": "1", "hash": "h"},
                {"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "2", "hash": "h2"},
                "junk-entry",
            ]
        },
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("ns.coll.mymod")
    assert len(result) == 1
    assert cast(YAMLDict, cast(object, result[0]["defined_in"]))["version"] == "2"


def test_search_module_all_deprecated_fallback(tmp_path: Path) -> None:
    """All-deprecated lists fall back to the first index entry.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    client.module_index = cast(
        YAMLDict,
        {
            "mymod": [
                {"fqcn": "ns.coll.mymod", "deprecated": True, "type": "c", "name": "old", "version": "9", "hash": "h"},
            ]
        },
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_module("mymod")
    assert len(result) == 1


def test_search_module_nondict_first_index(tmp_path: Path) -> None:
    """Non-dict first entries resolve to no index and miss.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.module_index = cast(YAMLDict, {"mymod": ["junk"]})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b:
        mock_b.return_value = []
        result = client.search_module("mymod")
    assert result == []


def test_search_role_max_match_zero(tmp_path: Path) -> None:
    """max_match zero short-circuits role search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_role("r", max_match=0) == []


def test_search_role_cache_hit(tmp_path: Path) -> None:
    """Cached role searches return directly.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    import json as _json

    client = _make_client(tmp_path)
    args_str = _json.dumps(["myrole", False, -1])
    cached = cast(YAMLDict, {"type": "role"})
    client.role_search_cache[args_str] = cast(object, [cached])  # type: ignore[assignment]
    assert client.search_role("myrole") == [cached]


def test_search_role_index_miss(tmp_path: Path) -> None:
    """Roles absent from the index return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_role("nosuchrole") == []


def test_search_role_index_empty_list(tmp_path: Path) -> None:
    """Empty role index lists behave like a miss.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.role_index = cast(YAMLDict, {"myrole": []})
    assert client.search_role("myrole") == []


def test_search_role_nondict_index(tmp_path: Path) -> None:
    """Non-dict role index entries resolve to no findings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.role_index = cast(YAMLDict, {"myrole": ["junk"]})
    assert client.search_role("myrole") == []


def test_search_role_path_missing(tmp_path: Path) -> None:
    """Indexed roles without findings files return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.role_index = cast(
        YAMLDict, {"myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    with patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists:
        mock_exists.return_value = False
        assert client.search_role("myrole") == []


def test_search_role_match_with_offspring(tmp_path: Path) -> None:
    """Role matches collect taskfile offspring recursively.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    taskfile = _make_taskfile()
    role.taskfiles = [taskfile]
    client.role_index = cast(
        YAMLDict, {"ns.coll.myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    child = cast(YAMLDict, {"object": taskfile, "offspring_objects": []})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = [child]
        result = client.search_role("ns.coll.myrole")
    assert len(result) == 1
    assert result[0]["name"] == "ns.coll.myrole"
    assert len(cast(list[YAMLDict], cast(object, result[0]["offspring_objects"]))) >= 1


def test_search_role_string_taskfile_key(tmp_path: Path) -> None:
    """String taskfile references are resolved via search_taskfile.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    role.taskfiles = ["some-string-key"]
    client.role_index = cast(
        YAMLDict, {"myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = []
        result = client.search_role("myrole")
    assert len(result) == 1
    assert cast(list[YAMLDict], cast(object, result[0]["offspring_objects"])) == []


def test_search_role_exact_and_fuzzy(tmp_path: Path) -> None:
    """Exact matching rejects suffixes that fuzzy matching accepts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(fqcn="ns.coll.myrole")
    client.role_index = cast(
        YAMLDict, {"myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = []
        fuzzy = client.search_role("myrole")
    assert len(fuzzy) == 1
    client2 = _make_client(tmp_path)
    client2.role_index = client.role_index
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf2,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = findings
        mock_tf2.return_value = []
        exact_miss = client2.search_role("myrole", exact_match=True)
    assert exact_miss == []


def test_search_role_findings_cache_and_non_findings(tmp_path: Path) -> None:
    """Findings cache hits avoid loads; non-Findings loads are skipped.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(fqcn="ns.coll.myrole")
    findings_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1", "h", "findings.json")
    client.role_index = cast(
        YAMLDict, {"ns.coll.myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    client.findings_cache[findings_path] = cast(object, {"roles": [role]})  # type: ignore[assignment]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_tf.return_value = []
        result = client.search_role("ns.coll.myrole")
    assert mock_load.call_count == 0
    assert len(result) == 1
    client2 = _make_client(tmp_path)
    client2.role_index = client.role_index
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = None
        assert client2.search_role("ns.coll.myrole") == []


def test_search_role_skips_non_role_and_max_match(tmp_path: Path) -> None:
    """Non-Role definitions are skipped and max_match limits output.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(fqcn="ns.coll.myrole")
    client.role_index = cast(
        YAMLDict, {"ns.coll.myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": ["junk", role, role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = []
        result = client.search_role("ns.coll.myrole", max_match=1)
    assert len(result) == 1


def test_search_role_falsy_child_offspring(tmp_path: Path) -> None:
    """Falsy taskfile children skip the direct append but still collect.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(fqcn="ns.coll.myrole")
    role.taskfiles = [_make_taskfile()]
    client.role_index = cast(
        YAMLDict, {"ns.coll.myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = cast(list[YAMLDict], [{}])
        result = client.search_role("ns.coll.myrole")
    assert len(result) == 1


def test_make_taskfile_key_candidates_empty(tmp_path: Path) -> None:
    """Empty from_path yields no candidates.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.make_taskfile_key_candidates("tasks/a.yml", "", "k") == []


def test_make_taskfile_key_candidates_simple(tmp_path: Path) -> None:
    """Sibling references yield a single candidate key.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    cands = client.make_taskfile_key_candidates("other.yml", "/repo/tasks/main.yml", "taskfile k")
    assert len(cands) == 1
    assert "other.yml" in cands[0]


def test_make_taskfile_key_candidates_roles(tmp_path: Path) -> None:
    """Role-relative references yield two candidate keys.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    cands = client.make_taskfile_key_candidates(
        "roles/other/tasks/x.yml", "/repo/roles/myrole/tasks/main.yml", "taskfile k"
    )
    assert len(cands) == 2


def test_search_taskfile_max_match_zero(tmp_path: Path) -> None:
    """max_match zero short-circuits taskfile search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_taskfile("x", is_key=True, max_match=0) == []


def test_search_taskfile_requires_from_path(tmp_path: Path) -> None:
    """Non-key lookups without from_path return nothing.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_taskfile("tasks/a.yml") == []


def test_search_taskfile_cache_hit(tmp_path: Path) -> None:
    """Cached taskfile searches return directly.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    import json as _json

    client = _make_client(tmp_path)
    args_str = _json.dumps(["k", "", "", -1, True])
    cached = cast(YAMLDict, {"type": "taskfile"})
    client.taskfile_search_cache[args_str] = cast(object, [cached])  # type: ignore[assignment]
    assert client.search_taskfile("k", is_key=True) == [cached]


def test_search_taskfile_index_miss(tmp_path: Path) -> None:
    """Unknown taskfile keys return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_taskfile("unknown-key", is_key=True) == []


def test_search_taskfile_nondict_index(tmp_path: Path) -> None:
    """Non-dict taskfile index entries resolve to no findings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.taskfile_index = cast(YAMLDict, {"mykey": ["junk"]})
    assert client.search_taskfile("mykey", is_key=True) == []


def test_search_taskfile_path_missing(tmp_path: Path) -> None:
    """Indexed taskfiles without findings files return nothing.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    with patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists:
        mock_exists.return_value = False
        assert client.search_taskfile(tkey, is_key=True) == []


def test_search_taskfile_match_with_offspring(tmp_path: Path) -> None:
    """Taskfile matches collect task offspring recursively.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    task = _make_task()
    taskfile = _make_taskfile(tkey)
    taskfile.tasks = [task]
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": [taskfile]})})
    child = cast(YAMLDict, {"object": task, "offspring_objects": []})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = [child]
        result = client.search_taskfile(tkey, is_key=True)
    assert len(result) == 1
    assert result[0]["name"] == tkey


def test_search_taskfile_string_task_key(tmp_path: Path) -> None:
    """String task references resolve through search_task.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    taskfile = _make_taskfile(tkey)
    taskfile.tasks = ["task-string-key"]
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": [taskfile]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = []
        result = client.search_taskfile(tkey, is_key=True)
    assert len(result) == 1


def test_search_taskfile_findings_cache_and_non_findings(tmp_path: Path) -> None:
    """Findings cache hits avoid loads; bad loads are skipped.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    taskfile = _make_taskfile(tkey)
    taskfile.tasks = []
    findings_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1", "h", "findings.json")
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    client.findings_cache[findings_path] = cast(object, {"taskfiles": [taskfile]})  # type: ignore[assignment]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_task.return_value = []
        result = client.search_taskfile(tkey, is_key=True)
    assert mock_load.call_count == 0
    assert len(result) == 1
    client2 = _make_client(tmp_path)
    client2.taskfile_index = client.taskfile_index
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = None
        assert client2.search_taskfile(tkey, is_key=True) == []


def test_search_taskfile_skips_non_taskfile_and_max_match(tmp_path: Path) -> None:
    """Non-TaskFile entries are skipped and max_match limits output.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    taskfile = _make_taskfile(tkey)
    taskfile.tasks = []
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(
        YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": ["junk", taskfile, taskfile]})}
    )
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = []
        result = client.search_taskfile(tkey, is_key=True, max_match=1)
    assert len(result) == 1


def test_search_taskfile_via_reference_path(tmp_path: Path) -> None:
    """Non-key references build candidate keys from the call site.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    from apme_engine.engine.keyutil import make_imported_taskfile_key

    from_key = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    cand = make_imported_taskfile_key(from_key, os.path.normpath("/repo/tasks/other.yml"))
    taskfile = TaskFile(name="other.yml", defined_in="/repo/tasks/other.yml", key=cand)
    client.taskfile_index = cast(
        YAMLDict, {cand: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": [taskfile]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = []
        result = client.search_taskfile("other.yml", from_path="/repo/tasks/main.yml", from_key=from_key)
    assert len(result) == 1


def test_search_taskfile_falsy_child(tmp_path: Path) -> None:
    """Falsy task children skip the direct append but still collect.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    tkey = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    taskfile = _make_taskfile(tkey)
    taskfile.tasks = [_make_task()]
    client.taskfile_index = cast(
        YAMLDict, {tkey: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": [taskfile]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = cast(list[YAMLDict], [{}])
        result = client.search_taskfile(tkey, is_key=True)
    assert len(result) == 1


def test_search_task_max_match_zero(tmp_path: Path) -> None:
    """max_match zero short-circuits task search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_task("x", content_info=cast(YAMLDict, {"type": "c"}), max_match=0) == []


def test_search_task_no_content_info(tmp_path: Path) -> None:
    """Missing content info returns no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_task("x") == []
    assert client.search_task("x", content_info=None) == []


def test_search_task_non_dict_content_info(tmp_path: Path) -> None:
    """Non-dict content info returns no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_task("x", content_info=cast(YAMLDict, cast(object, "junk"))) == []


def test_search_task_empty_content_info(tmp_path: Path) -> None:
    """Empty content info returns no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_task("x", content_info={}) == []


def test_search_task_cache_hit(tmp_path: Path) -> None:
    """Cached task searches return directly.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    import json as _json

    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    args_str = _json.dumps(["k", False, -1, True, info])
    cached = cast(YAMLDict, {"type": "task"})
    client.task_search_cache[args_str] = cast(object, [cached])  # type: ignore[assignment]
    assert client.search_task("k", is_key=True, content_info=info) == [cached]


def test_search_task_path_missing(tmp_path: Path) -> None:
    """Missing findings files return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    with patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists:
        mock_exists.return_value = False
        assert client.search_task("k", is_key=True, content_info=info) == []


def test_search_task_empty_type_content(tmp_path: Path) -> None:
    """Content info without a type still builds a findings path.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "", "name": "n", "version": "1", "hash": "h"})
    task = _make_task(task_key="k", task_name="hello")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_task("k", is_key=True, content_info=info)
    assert len(result) == 1


def test_search_task_by_key_module_offspring(tmp_path: Path) -> None:
    """Key matches with module executables collect module offspring.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="mykey", executable="ns.coll.mymod", executable_type=ExecutableType.MODULE_TYPE)
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    child = cast(YAMLDict, {"object": _make_module(), "offspring_objects": []})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_module") as mock_mod,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_mod.return_value = [child]
        result = client.search_task("mykey", is_key=True, content_info=info)
    assert len(result) == 1
    assert len(cast(list[YAMLDict], cast(object, result[0]["offspring_objects"]))) == 1


def test_search_task_role_offspring(tmp_path: Path) -> None:
    """Role executables resolve through role search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k2", executable="ns.coll.myrole", executable_type=ExecutableType.ROLE_TYPE)
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    child = cast(YAMLDict, {"object": _make_role(), "offspring_objects": []})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_role") as mock_role,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_role.return_value = [child]
        result = client.search_task("k2", is_key=True, content_info=info)
    assert len(result) == 1


def test_search_task_taskfile_offspring(tmp_path: Path) -> None:
    """Taskfile executables resolve through taskfile search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k3", executable="other.yml", executable_type=ExecutableType.TASKFILE_TYPE)
    task.defined_in = "/repo/tasks/main.yml"
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    child = cast(YAMLDict, {"object": _make_taskfile(), "offspring_objects": []})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = [child]
        result = client.search_task("k3", is_key=True, content_info=info)
    assert len(result) == 1


def test_unknown_executable_type_yields_no_offspring(tmp_path: Path) -> None:
    """Matched unknown-type tasks return one result with empty offspring.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="mystery", executable="whatever", executable_type="UnknownType")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_task("mystery", is_key=True, content_info=info)
    assert len(result) == 1
    assert result[0]["offspring_objects"] == []


def test_search_task_empty_executable_type_no_offspring(tmp_path: Path) -> None:
    """Empty executable types also yield empty offspring.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k4", executable="x", executable_type="")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_task("k4", is_key=True, content_info=info)
    assert len(result) == 1
    assert result[0]["offspring_objects"] == []


def test_search_task_name_exact_and_fuzzy(tmp_path: Path) -> None:
    """Exact name search rejects partial names that fuzzy accepts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k5", task_name="install nginx server", executable_type="")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        fuzzy = client.search_task("nginx", content_info=info)
    assert len(fuzzy) == 1
    client2 = _make_client(tmp_path)
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = findings
        exact_miss = client2.search_task("nginx", exact_match=True, content_info=info)
    assert exact_miss == []
    client3 = _make_client(tmp_path)
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists3,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load3,
    ):
        mock_exists3.return_value = True
        mock_load3.return_value = findings
        exact_hit = client3.search_task("install nginx server", exact_match=True, content_info=info)
    assert len(exact_hit) == 1


def test_search_task_empty_name_never_fuzzy_matches(tmp_path: Path) -> None:
    """Tasks with empty names never match fuzzy queries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k6", task_name="", executable_type="")
    task.name = ""
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_task("anything", content_info=info)
    assert result == []


def test_search_task_findings_cache_and_skips(tmp_path: Path) -> None:
    """Findings cache hits avoid loads; bad entries are skipped.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task = _make_task(task_key="k7", executable_type="")
    findings_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1", "h", "findings.json")
    client.findings_cache[findings_path] = cast(object, {"tasks": ["junk", task]})  # type: ignore[assignment]
    with patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists:
        mock_exists.return_value = True
        with patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load:
            result = client.search_task("k7", is_key=True, content_info=info)
    assert mock_load.call_count == 0
    assert len(result) == 1
    client2 = _make_client(tmp_path)
    client2.findings_cache[findings_path] = client.findings_cache[findings_path]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = None
        assert client2.search_task("other", is_key=True, content_info=info) == []


def test_search_task_max_match_and_falsy_child(tmp_path: Path) -> None:
    """max_match limits tasks and falsy children skip direct appends.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    task_one = _make_task(task_key="dup", task_name="n", executable_type="")
    task_two = _make_task(task_key="dup", task_name="n", executable_type="")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task_one, task_two]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client.search_task("dup", is_key=True, content_info=info, max_match=1)
    assert len(result) == 1
    client2 = _make_client(tmp_path)
    task_three = _make_task(task_key="k8", executable="m", executable_type=ExecutableType.MODULE_TYPE)
    findings2 = _make_findings()
    findings2.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"tasks": [task_three]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists2,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load2,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_module") as mock_mod,
    ):
        mock_exists2.return_value = True
        mock_load2.return_value = findings2
        mock_mod.return_value = cast(list[YAMLDict], [{}])
        result2 = client2.search_task("k8", is_key=True, content_info=info)
    assert len(result2) == 1
    assert result2[0]["offspring_objects"] == []


def test_search_action_group_max_zero(tmp_path: Path) -> None:
    """max_match zero short-circuits action group search.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_action_group("group/aws", max_match=0) == []


def test_search_action_group_miss(tmp_path: Path) -> None:
    """Unknown groups return no matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.search_action_group("group/missing") == []


def test_search_action_group_hit(tmp_path: Path) -> None:
    """Known groups return their indexed entries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    entry = cast(YAMLDict, {"group_name": "group/aws"})
    client.action_group_index = cast(YAMLDict, {"group/aws": [entry]})
    assert client.search_action_group("group/aws") == [entry]


def test_search_action_group_max_match_slice(tmp_path: Path) -> None:
    """max_match truncates long group lists.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    one = cast(YAMLDict, {"group_name": "a"})
    two = cast(YAMLDict, {"group_name": "b"})
    three = cast(YAMLDict, {"group_name": "c"})
    client.action_group_index = cast(YAMLDict, {"group/aws": [one, two, three]})
    assert client.search_action_group("group/aws", max_match=2) == [one, two]
    assert client.search_action_group("group/aws", max_match=10) == [one, two, three]


def test_get_object_by_key_hit(tmp_path: Path) -> None:
    """Matching keys return the object plus defined-in metadata.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    obj_list = ObjectList(items=[mod])
    obj_list.update_dict()
    coll_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "root", "modules.json")
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.ObjectList.from_json") as mock_from,
    ):
        mock_glob.side_effect = lambda pattern: [coll_path] if "collections" in str(pattern) else []
        mock_from.return_value = obj_list
        result = client.get_object_by_key(mod.key)
    assert result is not None
    assert cast(Module, cast(object, result["object"])) is mod
    assert "defined_in" in result


def test_get_object_by_key_miss(tmp_path: Path) -> None:
    """Unknown keys return None.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.ObjectList.from_json") as mock_from,
    ):
        mock_glob.return_value = []
        mock_from.return_value = ObjectList(items=[mod])
        assert client.get_object_by_key(mod.key) is None


def test_get_object_by_key_role_path(tmp_path: Path) -> None:
    """Role-side findings files are also searched.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    obj_list = ObjectList(items=[mod])
    obj_list.update_dict()
    role_path = os.path.join(str(tmp_path), "roles", "findings", "myrole", "1.0", "h", "root", "modules.json")
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.ObjectList.from_json") as mock_from,
    ):
        mock_glob.side_effect = lambda pattern: [] if "collections" in str(pattern) else [role_path]
        mock_from.return_value = obj_list
        result = client.get_object_by_key(mod.key)
    assert result is not None


def test_get_object_by_key_find_miss_in_file(tmp_path: Path) -> None:
    """Files without the key yield None.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    other = _make_module(name="other", fqcn="ns.coll.other")
    obj_list = ObjectList(items=[other])
    obj_list.update_dict()
    coll_path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "root", "modules.json")
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.ObjectList.from_json") as mock_from,
    ):
        mock_glob.side_effect = lambda pattern: [coll_path] if "collections" in str(pattern) else []
        mock_from.return_value = obj_list
        assert client.get_object_by_key(mod.key) is None


def test_init_findings_json_list_cache(tmp_path: Path) -> None:
    """Cache combines collection and role findings paths.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = os.path.join(str(tmp_path), "collections", "findings", "a", "1", "h", "findings.json")
    role = os.path.join(str(tmp_path), "roles", "findings", "b", "1", "h", "findings.json")
    with patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob:
        mock_glob.side_effect = lambda pattern: [coll] if "collections" in str(pattern) else [role]
        client._init_findings_json_list_cache()
    assert client._findings_json_list_cache == [coll, role]


def test_search_findings_empty_name_raises(tmp_path: Path) -> None:
    """Empty target names raise ValueError.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client._findings_json_list_cache = ["x"]
    with pytest.raises(ValueError):
        client._search_findings("", "1.0")


def test_search_findings_cache_hit(tmp_path: Path) -> None:
    """Cached findings searches return without scanning.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    import json as _json

    findings = _make_findings()
    args_str = _json.dumps(["ns.coll", "1.0", None])
    client._findings_json_list_cache = ["x"]
    client._findings_search_cache[args_str] = cast(object, findings)  # type: ignore[assignment]
    assert client._search_findings("ns.coll", "1.0") is findings


def test_search_findings_single_match(tmp_path: Path) -> None:
    """A single matching path loads and caches its findings.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    other = os.path.join(str(tmp_path), "collections", "findings", "other", "2.0", "h", "findings.json")
    client._findings_json_list_cache = [path, other]
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client._search_findings("ns.coll", "*")
    assert result is findings


def test_search_findings_version_filter_skips(tmp_path: Path) -> None:
    """Version-filtered searches skip non-matching versions.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "9.9", "h", "findings.json")
    client._findings_json_list_cache = [path]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.return_value = False
        mock_load.return_value = None
        result = client._search_findings("ns.coll", "1.0")
    assert result is None


def test_search_findings_type_filter(tmp_path: Path) -> None:
    """Type filters skip non-matching entries.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    client._findings_json_list_cache = [path]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.return_value = False
        mock_load.return_value = None
        result = client._search_findings("ns.coll", "*", target_type="collection")
    assert result is None


def test_search_findings_empty_version_defaults_star(tmp_path: Path) -> None:
    """Empty versions behave like wildcard searches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    client._findings_json_list_cache = [path]
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        assert client._search_findings("ns.coll", "") is findings


def test_search_findings_multiple_picks_newest(tmp_path: Path) -> None:
    """Multiple matches select the newest mtime path.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    old = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    new = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h2", "findings.json")
    client._findings_json_list_cache = [old, new]
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.getmtime") as mock_mtime,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_mtime.side_effect = lambda p: 200.0 if str(p) == new else 100.0
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client._search_findings("ns.coll", "*")
        assert mock_load.call_args[0][0] == new
    assert result is findings


def test_search_findings_multiple_first_newest(tmp_path: Path) -> None:
    """The first path wins when it already has the newest mtime.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    first = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    second = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h2", "findings.json")
    client._findings_json_list_cache = [first, second]
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.getmtime") as mock_mtime,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_mtime.side_effect = lambda p: 300.0 if str(p) == first else 100.0
        mock_exists.return_value = True
        mock_load.return_value = findings
        result = client._search_findings("ns.coll", "*")
        assert mock_load.call_args[0][0] == first
    assert result is findings


def test_search_findings_inits_cache_when_empty(tmp_path: Path) -> None:
    """Empty list caches trigger a glob-backed initialization.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client._findings_json_list_cache == []
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    findings = _make_findings()
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_glob.side_effect = lambda pattern: [path] if "collections" in str(pattern) else []
        mock_exists.return_value = True
        mock_load.return_value = findings
        assert client._search_findings("ns.coll", "*") is findings
    assert client._findings_json_list_cache == [path]


def test_load_findings_from_file(tmp_path: Path) -> None:
    """File paths load findings.json from their directory.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    target = tmp_path / "out" / "findings.json"
    with patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load:
        mock_load.return_value = findings
        assert client._load_findings(str(target)) is findings
        assert mock_load.call_args[1]["fpath"] == os.path.join(str(tmp_path / "out"), "findings.json")


def test_load_findings_from_dir(tmp_path: Path) -> None:
    """Directory paths append findings.json before loading.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    findings = _make_findings()
    target = tmp_path / "outdir"
    with patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load:
        mock_load.return_value = findings
        assert client._load_findings(str(target)) is findings
        assert mock_load.call_args[1]["fpath"] == os.path.join(str(target), "findings.json")


def test_save_findings_empty_dir_raises(tmp_path: Path) -> None:
    """Empty output directories raise ValueError.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with pytest.raises(ValueError):
        client.save_findings(_make_findings(), "")


def test_save_findings_creates_dir(tmp_path: Path) -> None:
    """Missing directories are created and findings.json is written.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out_dir = str(tmp_path / "newdir" / "nested")
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": {}, "mappings": {}})
    client.save_findings(findings, out_dir)
    assert os.path.exists(os.path.join(out_dir, "findings.json"))


def test_save_findings_existing_dir(tmp_path: Path) -> None:
    """Existing directories are reused without recreation errors.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out_dir = str(tmp_path / "exists")
    os.makedirs(out_dir)
    client.save_findings(_make_findings(), out_dir)
    assert os.path.exists(os.path.join(out_dir, "findings.json"))


def test_save_index_roundtrip(tmp_path: Path) -> None:
    """Saved indices load back the same content.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client.save_index(cast(YAMLDict, {"k": "v"}), module_index_name)
    assert client.load_index(module_index_name) == {"k": "v"}
    client.save_index(cast(YAMLDict, {"k2": "v2"}), module_index_name)
    assert client.load_index(module_index_name) == {"k2": "v2"}


def test_load_index_missing_returns_empty(tmp_path: Path) -> None:
    """Missing index files load as empty dicts.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    assert client.load_index("does-not-exist.json") == {}


def test_index_wrapper_delegation(tmp_path: Path) -> None:
    """Typed wrappers delegate to save_index and load_index.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    payload = cast(YAMLDict, {"x": "y"})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_index") as mock_save:
        client.save_module_index(payload)
        assert mock_save.call_args[0][1] == module_index_name
        client.save_role_index(payload)
        assert mock_save.call_args[0][1] == role_index_name
        client.save_taskfile_index(payload)
        assert mock_save.call_args[0][1] == taskfile_index_name
        client.save_action_group_index(payload)
        assert mock_save.call_args[0][1] == action_group_index_name
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_index") as mock_load:
        mock_load.return_value = payload
        assert client.load_module_index() == payload
        assert mock_load.call_args[0][0] == module_index_name
        assert client.load_role_index() == payload
        assert client.load_taskfile_index() == payload
        assert client.load_action_group_index() == payload


def test_save_error_empty_dir_raises(tmp_path: Path) -> None:
    """Empty output directories raise ValueError for errors.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    with pytest.raises(ValueError):
        client.save_error("boom", "")


def test_save_error_writes_file(tmp_path: Path) -> None:
    """Error text lands in error.log inside new directories.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out_dir = str(tmp_path / "errdir")
    client.save_error("boom", out_dir)
    assert (Path(out_dir) / "error.log").read_text() == "boom"


def test_save_error_existing_dir(tmp_path: Path) -> None:
    """Existing directories accept additional error writes.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    out_dir = str(tmp_path / "errexists")
    os.makedirs(out_dir)
    client.save_error("again", out_dir)
    assert (Path(out_dir) / "error.log").read_text() == "again"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "case",
    [
        ("unknown", 0.0),
        ("1", 1.0),
        ("1.2", 1.002),
        ("2.0.1", 2.000001),
        ("1.2.3-suffix", 1.002003),
        ("abc", 0.0),
        ("1.x.3", 1.000003),
        ("", 0.0),
    ],
)
def test_version_to_num_cases(case: tuple[str, float]) -> None:
    """Version strings convert to comparable numbers.

    Args:
        case: Version string paired with its expected numeric value.
    """
    ver, expected = case
    assert _version_to_num(ver) == pytest.approx(expected)


def test_version_to_num_two_parts_nonnumeric_minor() -> None:
    """Non-numeric minor versions contribute only the major part."""
    assert _version_to_num("3.x") == 3.0


def test_version_to_num_nonnumeric_patch() -> None:
    """Non-numeric patch versions keep major and minor parts."""
    assert _version_to_num("1.2.x") == pytest.approx(1.002)


def test_path_to_reversed_version_num() -> None:
    """Higher versions sort first via negated numbers."""
    path = "/r/collections/findings/ns.coll/2.0.0/hash/findings.json"
    assert _path_to_reversed_version_num(path) == pytest.approx(-2.0)


def test_path_to_collection_name() -> None:
    """Collection names extract from findings paths."""
    path = "/r/collections/findings/ns.coll/1.0/hash/findings.json"
    assert _path_to_collection_name(path) == "ns.coll"


def test_sort_by_version_groups_and_orders() -> None:
    """Paths group by collection with newest versions first."""
    base = "/r/collections/findings"
    paths = [
        base + "/b/1.0.0/h/findings.json",
        base + "/a/2.0.0/h/findings.json",
        base + "/a/1.0.0/h/findings.json",
        base + "/b/2.0.0/h/findings.json",
    ]
    ordered = sort_by_version(paths)
    assert ordered[0] == base + "/a/2.0.0/h/findings.json"
    assert ordered[1] == base + "/a/1.0.0/h/findings.json"
    assert ordered[2] == base + "/b/2.0.0/h/findings.json"
    assert ordered[3] == base + "/b/1.0.0/h/findings.json"


def test_sort_by_version_empty() -> None:
    """Empty path lists sort to empty lists."""
    assert sort_by_version([]) == []


def test_register_module_index_mismatch_appends(tmp_path: Path) -> None:
    """Differing existing module entries append the new metadata.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    findings = _make_findings()
    other_meta = ModuleMetadata.from_module(
        mod, cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "9.9", "hash": "h"})
    )
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = {"mymod": [other_meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
            defs = cast(YAMLDict, {"modules": [mod]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 1


def test_register_module_index_routing_mismatch_appends(tmp_path: Path) -> None:
    """Differing routing entries append the new redirect metadata.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": "ns.coll.newmod"}}}})
    findings = _make_findings()
    existing = cast(YAMLDict, {"fqcn": "other.mod", "type": "c", "name": "x", "version": "1", "hash": "h"})
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"oldmod": [existing]})
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
            defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 1


def test_register_module_index_routing_object_duplicate(tmp_path: Path) -> None:
    """Routing ModuleMetadata objects compare equal and skip saving.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"plugin_routing": {"modules": {"oldmod": {"redirect": "ns.coll.newmod"}}}})
    findings = _make_findings()
    existing = ModuleMetadata.from_routing("ns.coll.newmod", findings.metadata)
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_module_index") as mock_load:
        mock_load.return_value = {"oldmod": [existing]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_module_index") as mock_save:
            defs = cast(YAMLDict, {"modules": [], "collections": [coll]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_module_index_to_ram(findings)
    assert mock_save.call_count == 0


def test_register_role_index_mismatch_appends(tmp_path: Path) -> None:
    """Differing existing role entries append the new metadata.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role()
    findings = _make_findings()
    other_meta = RoleMetadata.from_role(
        role, cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "9.9", "hash": "h"})
    )
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_role_index") as mock_load:
        mock_load.return_value = {"ns.coll.myrole": [other_meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_role_index") as mock_save:
            defs = cast(YAMLDict, {"roles": [role]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_role_index_to_ram(findings)
    assert mock_save.call_count == 1


def test_register_taskfile_index_mismatch_appends(tmp_path: Path) -> None:
    """Differing existing taskfile entries append the new metadata.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    taskfile = _make_taskfile()
    findings = _make_findings()
    other_meta = TaskFileMetadata.from_taskfile(taskfile, findings.metadata)
    other_meta.version = "9.9"
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_taskfile_index") as mock_load:
        mock_load.return_value = {taskfile.key: [other_meta]}
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_taskfile_index") as mock_save:
            defs = cast(YAMLDict, {"taskfiles": [taskfile]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_taskfile_index_to_ram(findings)
    assert mock_save.call_count == 1


def test_register_action_group_mismatch_appends(tmp_path: Path) -> None:
    """Differing action group entries append new metadata for both names.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    coll = _make_collection()
    coll.meta_runtime = cast(YAMLDict, {"action_groups": {"aws": [_make_module()]}})
    findings = _make_findings()
    short_meta = ActionGroupMetadata()
    short_meta.group_name = "group/aws"
    short_meta.type = "collection"
    short_meta.name = "ns.coll"
    short_meta.version = "9.9"
    short_meta.hash = "h"
    fq_meta = ActionGroupMetadata()
    fq_meta.group_name = "group/ns.coll.aws"
    fq_meta.type = "collection"
    fq_meta.name = "ns.coll"
    fq_meta.version = "9.9"
    fq_meta.hash = "h"
    with patch("apme_engine.engine.risk_assessment_model.RAMClient.load_action_group_index") as mock_load:
        mock_load.return_value = cast(YAMLDict, {"group/aws": [short_meta], "group/ns.coll.aws": [fq_meta]})
        with patch("apme_engine.engine.risk_assessment_model.RAMClient.save_action_group_index") as mock_save:
            defs = cast(YAMLDict, {"collections": [coll]})
            findings.root_definitions = cast(YAMLDict, {"definitions": defs})
            client.register_action_group_index_to_ram(findings)
    assert mock_save.call_count == 1


def test_search_module_exact_miss_with_findings(tmp_path: Path) -> None:
    """Exact searches miss when findings hold a different FQCN.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(name="different", fqcn="ns.coll.different")
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        assert client.search_module("ns.coll.mymod", exact_match=True) == []


def test_search_module_fuzzy_miss_with_findings(tmp_path: Path) -> None:
    """Fuzzy searches miss when no FQCN component matches.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module(name="different", fqcn="ns.coll.different")
    client.module_index = cast(
        YAMLDict,
        {"mymod": [{"fqcn": "ns.coll.mymod", "type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]},
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"modules": [mod]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_builtin_module") as mock_b,
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_b.return_value = []
        mock_exists.return_value = True
        mock_load.return_value = findings
        assert client.search_module("mymod") == []


def test_search_role_exact_hit(tmp_path: Path) -> None:
    """Exact role searches hit on full FQCN equality.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(fqcn="ns.coll.myrole")
    client.role_index = cast(
        YAMLDict, {"ns.coll.myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = []
        result = client.search_role("ns.coll.myrole", exact_match=True)
    assert len(result) == 1


def test_search_role_fuzzy_miss_with_findings(tmp_path: Path) -> None:
    """Fuzzy role searches miss when findings hold another role.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    role = _make_role(name="other", fqcn="ns.coll.other")
    client.role_index = cast(
        YAMLDict, {"myrole": [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"roles": [role]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_taskfile") as mock_tf,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_tf.return_value = []
        assert client.search_role("myrole") == []


def test_search_taskfile_key_mismatch(tmp_path: Path) -> None:
    """Taskfile searches miss when findings hold another key.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    wanted = "taskfile collection:ns.coll#taskfile:tasks/main.yml"
    other = "taskfile collection:ns.coll#taskfile:tasks/other.yml"
    taskfile = _make_taskfile(other)
    client.taskfile_index = cast(
        YAMLDict, {wanted: [{"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"}]}
    )
    findings = _make_findings()
    findings.root_definitions = cast(YAMLDict, {"definitions": cast(YAMLDict, {"taskfiles": [taskfile]})})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
        patch("apme_engine.engine.risk_assessment_model.RAMClient.search_task") as mock_task,
    ):
        mock_exists.return_value = True
        mock_load.return_value = findings
        mock_task.return_value = []
        assert client.search_taskfile(wanted, is_key=True) == []


def test_search_task_load_not_findings(tmp_path: Path) -> None:
    """Task searches skip non-Findings loads gracefully.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    info = cast(YAMLDict, {"type": "collection", "name": "ns.coll", "version": "1", "hash": "h"})
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.Findings.load") as mock_load,
    ):
        mock_exists.return_value = True
        mock_load.return_value = None
        assert client.search_task("whatever", is_key=True, content_info=info) == []


def test_search_findings_version_filter_returns_match(tmp_path: Path) -> None:
    """Version-filtered searches return the row with the matching version.

    Guards the ``target_version`` comparison: the path version must be
    compared against the requested version, not the target name, so a
    filtered search finds its row while other versions are skipped.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    path = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "1.0", "h", "findings.json")
    other = os.path.join(str(tmp_path), "collections", "findings", "ns.coll", "2.0", "h2", "findings.json")
    client._findings_json_list_cache = [path, other]
    findings = _make_findings(version="1.0")
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.side_effect = lambda p: bool(p)
        mock_load.return_value = findings
        result = client._search_findings("ns.coll", "1.0")
    assert result is findings
    assert mock_load.call_args[0][0] == path


def test_search_findings_skips_short_path(tmp_path: Path) -> None:
    """Findings paths too short to index into are skipped, not crashed on.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    client._findings_json_list_cache = ["short.json"]
    with (
        patch("apme_engine.engine.risk_assessment_model.os.path.exists") as mock_exists,
        patch("apme_engine.engine.risk_assessment_model.RAMClient._load_findings") as mock_load,
    ):
        mock_exists.side_effect = lambda p: bool(p)
        mock_load.return_value = None
        assert client._search_findings("ns.coll", "*") is None
    assert mock_load.call_count == 0


def test_get_object_by_key_skips_short_path(tmp_path: Path) -> None:
    """Object JSON paths too short to index into are skipped, not crashed on.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    client = _make_client(tmp_path)
    mod = _make_module()
    obj_list = ObjectList(items=[mod])
    obj_list.update_dict()
    with (
        patch("apme_engine.engine.risk_assessment_model.safe_glob") as mock_glob,
        patch("apme_engine.engine.risk_assessment_model.ObjectList.from_json") as mock_from,
    ):
        # Short enough (< 6 segments) to hit the guard, but shaped so the
        # version sort key derivation does not crash first.
        mock_glob.side_effect = lambda pattern: ["findings/n/1"] if "collections" in str(pattern) else []
        mock_from.return_value = obj_list
        assert client.get_object_by_key(mod.key) is None
