"""L012: Replace state: latest with state: present."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key


def fix_latest(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace ``state: latest`` with ``state: present``.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None:
        return False

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and module_args.get("state") == "latest":
        module_args["state"] = "present"
        return True

    return False
