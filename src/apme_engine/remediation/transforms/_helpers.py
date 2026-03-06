"""Shared helpers for navigating ruamel YAML AST by line number."""

from __future__ import annotations

from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq


def find_task_at_line(data: Any, line: int) -> CommentedMap | None:
    """Walk a playbook structure and return the task mapping at the given line.

    ``line`` is 1-indexed (from the violation); ruamel uses 0-indexed internally.
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


def _search_node(node: Any, target_line: int) -> CommentedMap | None:
    if not isinstance(node, CommentedMap):
        return None

    if hasattr(node, "lc") and node.lc.line == target_line:
        return node

    for task_list_key in ("tasks", "pre_tasks", "post_tasks", "handlers",
                          "block", "rescue", "always"):
        tasks = node.get(task_list_key)
        if isinstance(tasks, CommentedSeq):
            for task in tasks:
                result = _search_node(task, target_line)
                if result is not None:
                    return result

    return None


_TASK_META_KEYS = frozenset({
    "name", "when", "changed_when", "failed_when",
    "register", "notify", "listen",
    "become", "become_user", "become_method", "become_flags",
    "delegate_to", "run_once", "connection",
    "ignore_errors", "ignore_unreachable",
    "no_log", "tags", "environment", "vars", "args",
    "loop", "loop_control",
    "with_items", "with_dict", "with_fileglob", "with_subelements",
    "with_sequence", "with_nested", "with_first_found",
    "block", "rescue", "always",
    "any_errors_fatal", "max_fail_percentage",
    "check_mode", "diff", "throttle", "timeout",
    "retries", "delay", "until",
    "debugger", "module_defaults", "collections",
    "local_action",
})


def get_module_key(task: CommentedMap) -> str | None:
    """Return the module/action key in a task mapping.

    The module key is the first key that isn't a known Ansible task keyword.
    """
    for key in task:
        if key not in _TASK_META_KEYS:
            return key
    return None


def rename_key(mapping: CommentedMap, old_key: str, new_key: str) -> None:
    """Rename a key in a CommentedMap while preserving insertion order and value."""
    if old_key not in mapping:
        return

    items = list(mapping.items())
    mapping.clear()
    for k, v in items:
        mapping[new_key if k == old_key else k] = v
