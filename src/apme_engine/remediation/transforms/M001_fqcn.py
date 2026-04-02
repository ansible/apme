"""M001/M003/L005: Rewrite short module names to FQCN.

M001 violations carry ``resolved_fqcn`` from ansible-core's plugin loader.
L005 violations carry ``resolved_fqcn`` from the engine's hierarchy payload.
When the violation lacks a resolved FQCN the transform falls back to parsing
the message text (``"Use FQCN: apt -> ansible.builtin.apt"``).  If that also
fails the violation escalates to AI (Tier 2).
"""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

_FQCN_FROM_MSG_RE = re.compile(
    r"(?:Use FQCN|FQCN for module):\s*\S+\s*->\s*(\S+)",
)


def _resolve_fqcn(violation: ViolationDict, current_key: str) -> str | None:
    """Get the target FQCN from the violation metadata.

    Checks ``resolved_fqcn`` (from Ansible validator M001 / OPA L005)
    and ``fqcn`` (from native validator L026).  Falls back to parsing
    the violation message for ``"X -> Y"`` patterns.  Returns None when
    no source carries a usable FQCN so the violation escalates to AI.

    Args:
        violation: Violation dict (may have resolved_fqcn or fqcn).
        current_key: Current short module name.

    Returns:
        FQCN string, or None if not resolvable.
    """
    for field in ("resolved_fqcn", "fqcn"):
        fqcn = violation.get(field)
        if fqcn is not None and _is_valid_fqcn(str(fqcn), current_key):
            return str(fqcn)

    msg = str(violation.get("message", ""))
    m = _FQCN_FROM_MSG_RE.search(msg)
    if m:
        candidate = m.group(1)
        if _is_valid_fqcn(candidate, current_key):
            return candidate

    return None


def _is_valid_fqcn(candidate: str, current_key: str) -> bool:
    """Return True if candidate looks like a real FQCN, not an engine key.

    Args:
        candidate: String to validate as a FQCN.
        current_key: The current YAML module key (to reject identity matches).

    Returns:
        True if candidate is a well-formed FQCN.
    """
    if candidate == current_key:
        return False
    if " " in candidate or "#" in candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return False
    if candidate.startswith("taskfile"):
        return False
    if candidate.endswith((".yml", ".yaml", ".json")):
        return False
    return "." in candidate


def fix_fqcn(task: CommentedMap, violation: ViolationDict) -> bool:
    """Rename a short module name to its FQCN.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line and optional resolved_fqcn.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None:
        return False

    fqcn = _resolve_fqcn(violation, module_key)
    if fqcn is None or fqcn == module_key:
        return False

    rename_key(task, module_key, fqcn)
    return True
