"""Tests for apme_engine.engine.finder."""

from __future__ import annotations

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
