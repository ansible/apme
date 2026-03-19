"""Shared helpers for navigating ruamel YAML AST by line number."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from apme_engine.engine.models import ViolationDict

_TASK_INDEX_RE = re.compile(r"task:\[(\d+)\]")


def violation_line_to_int(violation: ViolationDict) -> int:
    """Extract 1-indexed line number from violation dict.

    Args:
        violation: Violation dict with optional line field.

    Returns:
        1-indexed line number, or 0 if missing/invalid.
    """
    line = violation.get("line", 0)
    if isinstance(line, list | tuple) and line:
        val = line[0]
        return int(val) if isinstance(val, int | float | str) else 0
    if isinstance(line, int | float):
        return int(line)
    if isinstance(line, str):
        raw = line.lstrip("L")
        raw = raw.split("-")[0]
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def violation_task_index(violation: ViolationDict) -> int | None:
    """Extract the task index from the violation ``path`` field.

    The native validator encodes the task position as ``task:[N]`` in the
    hierarchy node key.  This is useful when ``line`` is ``None``.

    Args:
        violation: Violation dict with optional path field.

    Returns:
        0-based task index, or None if not present.
    """
    path = violation.get("path", "")
    if not isinstance(path, str):
        return None
    m = _TASK_INDEX_RE.search(path)
    return int(m.group(1)) if m else None


def find_task_by_index(data: CommentedMap | CommentedSeq, index: int) -> CommentedMap | None:
    """Return the Nth task from a top-level task list.

    Works on bare task lists (CommentedSeq) and play mappings that contain
    a ``tasks``, ``pre_tasks``, ``post_tasks``, or ``handlers`` key.

    Args:
        data: Playbook root (CommentedMap or CommentedSeq).
        index: 0-based task index.

    Returns:
        Task CommentedMap, or None if index is out of range.
    """
    seq: CommentedSeq | None = None
    if isinstance(data, CommentedSeq):
        seq = data
    elif isinstance(data, CommentedMap):
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers"):
            candidate = data.get(key)
            if isinstance(candidate, CommentedSeq):
                seq = candidate
                break
    if seq is not None and 0 <= index < len(seq):
        item = seq[index]
        if isinstance(item, CommentedMap):
            return item
    return None


def find_task_at_line(data: CommentedMap | CommentedSeq, line: int) -> CommentedMap | None:
    """Walk a playbook structure and return the task mapping at the given line.

    ``line`` is 1-indexed (from the violation); ruamel uses 0-indexed internally.

    Args:
        data: Playbook root (CommentedMap or CommentedSeq).
        line: 1-indexed line number from violation.

    Returns:
        Task CommentedMap at that line, or None if not found.
    """
    target = line - 1

    if isinstance(data, CommentedSeq):
        for item in data:
            result = _search_node(item, target)
            if result is not None:
                return result
    elif isinstance(data, CommentedMap):
        result = _search_node(data, target)
        if result is not None:
            return result

    return None


def _search_node(node: CommentedMap | CommentedSeq, target_line: int) -> CommentedMap | None:
    """Recursively search for a task node at the target 0-indexed line.

    Args:
        node: Current YAML node (CommentedMap or CommentedSeq).
        target_line: 0-indexed target line number.

    Returns:
        Task CommentedMap at target_line, or None.
    """
    if not isinstance(node, CommentedMap):
        return None

    if hasattr(node, "lc") and node.lc.line == target_line:
        return node

    for task_list_key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
        tasks = node.get(task_list_key)
        if isinstance(tasks, CommentedSeq):
            for task in tasks:
                result = _search_node(task, target_line)
                if result is not None:
                    return result

    return None


_TASK_META_KEYS = frozenset(
    {
        "name",
        "when",
        "changed_when",
        "failed_when",
        "register",
        "notify",
        "listen",
        "become",
        "become_user",
        "become_method",
        "become_flags",
        "delegate_to",
        "run_once",
        "connection",
        "ignore_errors",
        "ignore_unreachable",
        "no_log",
        "tags",
        "environment",
        "vars",
        "args",
        "loop",
        "loop_control",
        "with_items",
        "with_dict",
        "with_fileglob",
        "with_subelements",
        "with_sequence",
        "with_nested",
        "with_first_found",
        "block",
        "rescue",
        "always",
        "any_errors_fatal",
        "max_fail_percentage",
        "check_mode",
        "diff",
        "throttle",
        "timeout",
        "retries",
        "delay",
        "until",
        "debugger",
        "module_defaults",
        "collections",
        "local_action",
    }
)


def get_module_key(task: CommentedMap) -> str | None:
    """Return the module/action key in a task mapping.

    The module key is the first key that isn't a known Ansible task keyword.

    Args:
        task: Task CommentedMap.

    Returns:
        Module key string, or None if no module found.
    """
    for key in task:
        if key not in _TASK_META_KEYS:
            return str(key)
    return None


def rename_key(mapping: CommentedMap, old_key: str, new_key: str) -> None:
    """Rename a key in a CommentedMap while preserving insertion order and value.

    Args:
        mapping: CommentedMap to modify.
        old_key: Key to rename.
        new_key: New key name.
    """
    if old_key not in mapping:
        return

    items = list(mapping.items())
    mapping.clear()
    for k, v in items:
        mapping[new_key if k == old_key else k] = v
