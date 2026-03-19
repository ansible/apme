"""L012: Replace state: latest with state: present."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import get_module_key, violation_line_to_int


def fix_latest(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Replace ``state: latest`` with ``state: present``.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation), violation)
    if task is None:
        return False

    module_key = get_module_key(task)
    if module_key is None:
        return False

    module_args = task.get(module_key)
    if isinstance(module_args, dict) and module_args.get("state") == "latest":
        module_args["state"] = "present"
        return True

    return False
