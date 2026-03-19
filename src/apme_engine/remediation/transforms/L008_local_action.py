"""L008: Replace local_action with delegate_to: localhost."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int


def fix_local_action(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Convert local_action to the module key + delegate_to: localhost.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation), violation)
    if task is None:
        return False

    la_value = task.get("local_action")
    if la_value is None:
        return False

    if isinstance(la_value, str):
        parts = la_value.split(None, 1)
        module_name = parts[0]
        free_form = parts[1] if len(parts) > 1 else None

        del task["local_action"]
        if free_form:
            task[module_name] = free_form
        else:
            task[module_name] = CommentedMap()
        task["delegate_to"] = "localhost"

    elif isinstance(la_value, CommentedMap):
        module_name = la_value.pop("module", None)
        if not module_name:
            return False

        del task["local_action"]
        task[module_name] = la_value if la_value else CommentedMap()
        task["delegate_to"] = "localhost"

    else:
        return False

    return True
