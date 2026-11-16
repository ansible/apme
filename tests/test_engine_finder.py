"""Tests for apme_engine.engine.finder."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.engine.finder import find_module_name, flatten_block_tasks, identify_lines_with_jsonpath
from apme_engine.engine.models import YAMLDict


class TestIdentifyLinesWithJsonpathEmptyBlocks:
    """identify_lines_with_jsonpath returns (None, None) when blocks are missing or OOB."""

    def test_key_based_empty_blocks_returns_none(self) -> None:
        """When find_child_yaml_block returns no blocks for a key, returns (None, None).

        Requesting .block on a task that has no block/rescue/always key yields
        no blocks and should not raise IndexError.
        """
        yaml_str = "tasks:\n  - name: single task\n    ansible.builtin.debug:\n      msg: x"
        # Path: .tasks -> task list block, .0 -> first task block, .block -> no such key
        result_lines, result_range = identify_lines_with_jsonpath(
            yaml_str=yaml_str,
            jsonpath=".tasks.0.block",
        )
        assert result_lines is None
        assert result_range is None

    def test_numeric_index_out_of_range_returns_none(self) -> None:
        """When numeric index is >= len(blocks), returns (None, None).

        Requesting .tasks.2 when there are only two tasks (indices 0 and 1)
        should not raise IndexError.
        """
        yaml_str = (
            "tasks:\n"
            "  - name: first\n"
            "    ansible.builtin.debug:\n"
            "      msg: a\n"
            "  - name: second\n"
            "    ansible.builtin.debug:\n"
            "      msg: b\n"
        )
        result_lines, result_range = identify_lines_with_jsonpath(
            yaml_str=yaml_str,
            jsonpath=".tasks.2",
        )
        assert result_lines is None
        assert result_range is None

    def test_numeric_index_negative_returns_none(self) -> None:
        """When path segment is negative (e.g. .-1), returns (None, None).

        Parsed as int, then p_num < 0 triggers the OOB guard.
        """
        yaml_str = "tasks:\n  - name: only\n    debug:\n      msg: x"
        result_lines, result_range = identify_lines_with_jsonpath(
            yaml_str=yaml_str,
            jsonpath=".tasks.-1",
        )
        assert result_lines is None
        assert result_range is None

    def test_non_numeric_path_segment_returns_none(self) -> None:
        """When a path segment is not a known key and not an int, returns (None, None)."""
        yaml_str = "tasks:\n  - name: x\n    debug:\n      msg: y"
        result_lines, result_range = identify_lines_with_jsonpath(
            yaml_str=yaml_str,
            jsonpath=".tasks.not_an_index",
        )
        assert result_lines is None
        assert result_range is None


class TestIdentifyLinesWithJsonpathSuccess:
    """identify_lines_with_jsonpath returns valid result when path exists."""

    def test_valid_key_path_returns_fragment_and_range(self) -> None:
        """When path exists, returns (yaml_fragment, (start_line, end_line))."""
        yaml_str = "- hosts: localhost\n  tasks:\n    - name: hello\n      ansible.builtin.debug:\n        msg: world\n"
        result_lines, result_range = identify_lines_with_jsonpath(
            yaml_str=yaml_str,
            jsonpath=".0.tasks.0",
        )
        assert result_lines is not None
        assert result_range is not None
        assert result_range[0] <= result_range[1]
        assert "name: hello" in result_lines or "ansible.builtin.debug" in result_lines


class TestFlattenBlockTasks:
    """flatten_block_tasks emits block wrappers intact."""

    def test_non_block_task_returned_as_is(self) -> None:
        """A plain task dict is returned as a single entry."""
        task: YAMLDict = {"name": "hi", "ansible.builtin.debug": {"msg": "x"}}
        result = flatten_block_tasks(task, ".tasks.0")
        assert len(result) == 1
        assert result[0] == (task, ".tasks.0")

    def test_block_wrapper_emitted_intact(self) -> None:
        """A dict with block: key is emitted as one entry, not flattened."""
        task: YAMLDict = {
            "name": "my block",
            "block": [
                {"name": "t1", "ansible.builtin.debug": {"msg": "1"}},
                {"name": "t2", "ansible.builtin.debug": {"msg": "2"}},
            ],
        }
        result = flatten_block_tasks(task, ".tasks.0")
        assert len(result) == 1
        assert result[0][0] is task
        assert result[0][0]["block"] is task["block"]

    def test_rescue_always_wrapper_emitted_intact(self) -> None:
        """Block with rescue/always is emitted as a single wrapper."""
        task: YAMLDict = {
            "block": [{"ansible.builtin.debug": {"msg": "main"}}],
            "rescue": [{"ansible.builtin.debug": {"msg": "rescue"}}],
            "always": [{"ansible.builtin.debug": {"msg": "always"}}],
        }
        result = flatten_block_tasks(task, ".tasks.0")
        assert len(result) == 1
        assert "rescue" in result[0][0]
        assert "always" in result[0][0]

    def test_none_returns_empty(self) -> None:
        """None input returns empty list."""
        assert flatten_block_tasks(None) == []


class TestFindModuleNameBlockKeywords:
    """find_module_name does not treat block/rescue/always as modules."""

    def test_block_not_module(self) -> None:
        """A dict with only block: key returns empty module."""
        task: YAMLDict = {
            "name": "my block",
            "block": [{"ansible.builtin.debug": {"msg": "hi"}}],
        }
        assert find_module_name(task) == ""

    def test_rescue_not_module(self) -> None:
        """Rescue is a task keyword, not a module name."""
        task: YAMLDict = {"rescue": [{"ansible.builtin.debug": {"msg": "hi"}}]}
        assert find_module_name(task) == ""

    def test_always_not_module(self) -> None:
        """Always is a task keyword, not a module name."""
        task: YAMLDict = {"always": [{"ansible.builtin.debug": {"msg": "hi"}}]}
        assert find_module_name(task) == ""


class TestTaskfileClassification:
    """Distinguish role requirements and named data from task sources."""

    def test_role_requirements_are_not_tasks(self) -> None:
        """Both requirements extensions support identifiers, includes, and pins."""
        from apme_engine.engine.finder import could_be_taskfile, label_yml_file

        content = (
            "- name: web_role\n  src: acme.web\n  version: 1.0.0\n"
            "- src: https://example.org/role.git\n  scm: git\n"
            "- include: more.yml\n- acme.common\n"
        )
        for filename in ("roles/requirements.yml", "meta/requirements.yaml"):
            assert not could_be_taskfile(body=content, fpath=filename)
            assert label_yml_file(yml_path=filename, yml_body=content)[0] == "others"

    def test_role_test_requirements_remain_data(self, tmp_path: Path) -> None:
        """Role tests may mix Galaxy inputs and task files sharing the same basename.

        Args:
            tmp_path: Temporary project directory.
        """
        from apme_engine.engine.finder import could_be_taskfile
        from apme_engine.engine.model_loader import load_role
        from apme_engine.formatter import format_directory

        role = tmp_path / "roles" / "web"
        main = role / "tasks" / "main.yml"
        main.parent.mkdir(parents=True)
        main.write_text("- ansible.builtin.debug: {}\n")
        requirements = role / "tests" / "requirements.yml"
        requirements.parent.mkdir()
        content = "- {name: web_role, src: acme.web, version: 1.0.0}\n- include: more.yml\n- acme.common\n"
        requirements.write_text(content)
        executable = role / "tests" / "tasks" / "requirements.yml"
        executable.parent.mkdir()
        executable.write_text("- name: actual task\n  custom_module: value\n")
        assert not could_be_taskfile(body=content, fpath=str(requirements), root_path=str(role), task_context=True)
        assert could_be_taskfile(fpath=str(executable), root_path=str(role), task_context=True)
        loaded = load_role(str(role), use_ansible_doc=False, include_test_contents=True, load_children=False)
        assert len(loaded.taskfiles) == 2
        assert all("tests/requirements.yml" not in str(path) for path in loaded.taskfiles)
        format_directory(role)
        assert "web_role" in requirements.read_text()
        assert "Web_role" not in requirements.read_text()

    def test_name_alone_is_not_task_evidence(self) -> None:
        """Arbitrarily named list data is not an Ansible task file."""
        from apme_engine.engine.finder import label_yml_file

        assert label_yml_file(yml_path="data.yml", yml_body="- name: production\n")[0] == "others"

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "filename", ["playbooks/requirements.yml", "playbooks/files/users.yml", "/srv/tasks/repo/requirements.yml"]
    )
    def test_data_keys_are_not_module_evidence(self, filename: str) -> None:
        """Arbitrary data keys and checkout ancestors cannot identify tasks.

        Args:
            filename: Candidate source path, including misleading ancestors.
        """
        from apme_engine.engine.finder import could_be_taskfile, label_yml_file

        for body in (
            "- name: community.general\n  source: galaxy\n  type: galaxy\n",
            "- name: alice\n  uid: 1001\n",
            "- name: web_role\n  src: acme.web\n  version: 1.0.0\n",
            "- name: just data\n",
        ):
            assert not could_be_taskfile(body=body, fpath=filename)
            assert label_yml_file(yml_path=filename, yml_body=body)[0] == "others"

    def test_directory_discovery_checks_later_tasks(self, tmp_path: Path) -> None:
        """A malformed first task does not hide subsequent actions or named includes.

        Args:
            tmp_path: Temporary repository directory.
        """
        from apme_engine.engine.finder import search_taskfiles_for_playbooks

        playbooks = tmp_path / "playbooks"
        playbooks.mkdir()
        (playbooks / "setup.yml").write_text("- name: missing action\n- debug:\n    msg: hello\n")
        (playbooks / "requirements.yml").write_text("- name: legacy include\n  include: child.yml\n")
        (playbooks / "dependencies.yml").write_text("- include: more.yml\n")
        assert {Path(path).name for path in search_taskfiles_for_playbooks(str(tmp_path))} == {
            "setup.yml",
            "requirements.yml",
        }

    def test_additional_directories_have_their_own_boundary(self, tmp_path: Path) -> None:
        """Explicit external searches retain task evidence without borrowing ancestors.

        Args:
            tmp_path: Temporary parent for separate project and supplemental directories.
        """
        from apme_engine.engine.finder import could_be_taskfile, search_taskfiles_for_playbooks

        project = tmp_path / "project"
        project.mkdir()
        extra = tmp_path / "tasks" / "shared"
        extra.mkdir(parents=True)
        content = "- name: external task\n  ansible.builtin.debug: {msg: hello}\n"
        external = extra / "setup.yml"
        external.write_text(content)
        custom = extra / "custom.yml"
        custom.write_text("- name: custom task\n  custom_module:\n")
        (extra / "data.yml").write_text("- name: identifier\n  uid: 1001\n")
        assert could_be_taskfile(body=content, fpath=str(external), root_path=str(project))
        requested = [str(extra)]
        assert set(search_taskfiles_for_playbooks(str(project), requested)) == {str(external), str(custom)}
        assert requested == [str(extra)]

    def test_task_context_is_bounded_to_project(self, tmp_path: Path) -> None:
        """Nested custom actions survive while checkout ancestors and data do not.

        Args:
            tmp_path: Temporary parent containing a misleading tasks directory.
        """
        from apme_engine.engine.finder import could_be_taskfile

        root = tmp_path / "tasks" / "repo"
        custom = "- name: custom action\n  custom_module: value\n"
        assert could_be_taskfile(body=custom, fpath=str(root / "tasks/setup/users.yml"), root_path=str(root))
        assert not could_be_taskfile(body=custom, fpath=str(root / "data.yml"), root_path=str(root))
        assert not could_be_taskfile(
            body="- name: alice\n  group: staff\n", fpath=str(root / "playbooks/files/users.yml"), root_path=str(root)
        )
        assert could_be_taskfile(body=custom, fpath="roles/web/tasks/setup/users.yml")

    def test_enclosing_role_is_outside_selected_project(self, tmp_path: Path) -> None:
        """An enclosing role cannot grant custom-action permission to a new project.

        Args:
            tmp_path: Temporary parent used for an enclosing role layout.
        """
        from apme_engine.engine.finder import could_be_taskfile, taskfile_source_context

        project = tmp_path / "roles/web/tests/project"
        path = project / "requirements.yml"
        body = "- name: web_role\n  src: acme.web\n"
        context = taskfile_source_context(str(path), str(project))
        assert not context.role_source
        assert not could_be_taskfile(body=body, fpath=str(path), root_path=str(project))

    def test_nested_module_identity_is_discovered(self, tmp_path: Path) -> None:
        """Collection modules in nested plugin directories remain task evidence.

        Args:
            tmp_path: Temporary project directory.
        """
        from apme_engine.engine.finder import could_be_taskfile, label_yml_file, search_taskfiles_for_playbooks

        content = "- name: configure routing\n  acme.network.routing.configure: {}\n"
        path = tmp_path / "playbooks" / "setup.yml"
        path.parent.mkdir()
        path.write_text(content)
        assert could_be_taskfile(body=content, fpath=str(path), root_path=str(tmp_path))
        assert label_yml_file(yml_path=str(path), root_path=str(tmp_path))[0] == "taskfile"
        assert str(path) in search_taskfiles_for_playbooks(str(tmp_path))

    def test_role_includes_supply_task_context(self, tmp_path: Path) -> None:
        """Role discovery preserves custom includes without accepting arbitrary include data.

        Args:
            tmp_path: Temporary root containing a standalone role and unrelated data.
        """
        from apme_engine.engine.finder import could_be_taskfile
        from apme_engine.engine.model_loader import load_role

        role = tmp_path / "web"
        source = role / "includes" / "nested" / "setup.yml"
        source.parent.mkdir(parents=True)
        content = "- name: configure service\n  custom_module: value\n"
        source.write_text(content)
        test_source = role / "tests" / "smoke.yml"
        test_source.parent.mkdir()
        test_source.write_text(content)
        data_source = role / "tests" / "vars" / "users.yml"
        data_source.parent.mkdir()
        data_source.write_text("- name: alice\n  group: staff\n")
        for directory in ("data", "fixtures", "fixtures/tasks", "vars/includes", "files/includes"):
            fixture = role / "tests" / directory / "users.yml"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text("- name: alice\n  uid: 1001\n")
        loaded = load_role(str(role), use_ansible_doc=False)
        assert len(loaded.taskfiles) == 1
        loaded_with_tests = load_role(str(role), use_ansible_doc=False, include_test_contents=True)
        assert len(loaded_with_tests.taskfiles) == 2
        assert not could_be_taskfile(body=content, fpath="includes/nested/setup.yml")

    def test_nested_role_data_cannot_create_a_new_role_boundary(self, tmp_path: Path) -> None:
        """Task-like names beneath role data do not reset the source boundary.

        Args:
            tmp_path: Temporary project root.
        """
        from apme_engine.engine.finder import could_be_taskfile, taskfile_source_context

        for relative in (
            "roles/r/vars/includes/users.yml",
            "roles/r/tests/fixtures/tasks/users.yml",
            "roles/r/vars/includes/roles/child/tasks/users.yml",
        ):
            path = tmp_path / relative
            context = taskfile_source_context(str(path), str(tmp_path), (str(tmp_path / "roles/r"),))
            assert context.data_directory
            assert not could_be_taskfile(
                body="- name: alice\n  group: staff\n", fpath=str(path), source_context=context
            )

    def test_known_task_sources_remain_discoverable(self) -> None:
        """Blocks, explicit actions, custom modules and malformed known tasks survive."""
        from apme_engine.engine.finder import could_be_taskfile

        for content in (
            "- name: block label\n  block: []\n",
            "- name: action label\n  action: debug msg=hello\n",
            "- name: local label\n  local_action: debug msg=hello\n",
            "- name: legacy include\n  include: child.yml\n",
            "- name: custom label\n  custom_module: value\n",
            "- name: custom src label\n  src:\n    path: file\n",
            "- name: debug label\n  ansible.builtin.debug:\n    msg: hello\n",
        ):
            assert could_be_taskfile(body=content, fpath="tasks/requirements.yml")
        assert could_be_taskfile(body="- name: missing action\n", fpath="roles/web/tasks/main.yml")
        assert could_be_taskfile(body="- name: missing action\n", fpath="roles/web/handlers/main.yml")
