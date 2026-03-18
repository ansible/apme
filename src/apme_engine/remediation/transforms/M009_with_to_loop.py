"""M009: Convert with_items/with_dict/etc to loop:."""

from __future__ import annotations

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int

_WITH_SIMPLE = frozenset(
    {
        "with_items",
        "with_list",
        "with_flattened",
    }
)


def fix_with_to_loop(sf: StructuredFile, violation: ViolationDict) -> bool:
    """Convert simple ``with_items`` to ``loop:``.

    Only handles the straightforward cases (with_items, with_list,
    with_flattened -> loop).  More complex with_* forms (with_dict,
    with_subelements) need manual review or AI.

    Args:
        sf: Parsed YAML file to modify in-place.
        violation: Violation dict with line and optional with_key.

    Returns:
        True if a change was applied.
    """
    task = sf.find_task(violation_line_to_int(violation))
    if task is None:
        return False

    with_key = violation.get("with_key", "")

    if with_key in _WITH_SIMPLE and with_key in task:
        value = task.pop(with_key)
        task["loop"] = value
        return True

    for k in list(_WITH_SIMPLE):
        if k in task:
            value = task.pop(k)
            task["loop"] = value
            return True

    return False
