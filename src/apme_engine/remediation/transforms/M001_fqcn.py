"""Rewrite module names to a resolved FQCN target.

M001 violations carry ``resolved_fqcn`` from ansible-core's plugin loader.
L005 carries ``resolved_fqcn`` from the engine's hierarchy payload.
When the violation lacks a resolved FQCN the transform falls back to parsing
the message text (``"Use FQCN: apt -> ansible.builtin.apt"``). Findings with
no canonical target are kept for manual review rather than guessed.
"""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key, rename_key
from apme_engine.rule_ids import normalize_rule_id

_FQCN_FROM_MSG_RE = re.compile(
    r"(?:Use FQCN|FQCN for module):\s*\S+\s*->\s*(\S+)",
)
_FQCN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){2,}")


def enrich_fqcn_targets(violations: list[ViolationDict]) -> None:
    """Share runtime resolver targets with short-name findings for the same task.

    Only canonical-name and redirect findings supply targets. A builtin twin
    suggestion does not prove equivalence. Match the file, node ID, and original
    module together so a resolution cannot leak between tasks or stale scans.

    Args:
        violations: Findings from one scan, updated in place.
    """
    targets: dict[tuple[str, str, str], set[str]] = {}
    for violation in violations:
        if normalize_rule_id(str(violation.get("rule_id", ""))) not in {"M001", "M003"}:
            continue
        original = str(violation.get("original_module", ""))
        node_id = str(violation.get("path", ""))
        target = _resolve_fqcn(violation, original)
        if original and node_id and target:
            key = (str(violation.get("file", "")), node_id, original)
            targets.setdefault(key, set()).add(target)

    for violation in violations:
        if normalize_rule_id(str(violation.get("rule_id", ""))) not in {"L002", "L026"}:
            continue
        if has_fqcn_target(violation):
            continue
        key = (
            str(violation.get("file", "")),
            str(violation.get("path", "")),
            str(violation.get("original_module", "")),
        )
        candidates = targets.get(key, set())
        if len(candidates) == 1:
            violation["resolved_fqcn"] = next(iter(candidates))


def _resolve_fqcn(violation: ViolationDict, current_key: str) -> str | None:
    """Get the target FQCN from the violation metadata.

    Checks ``resolved_fqcn`` (from Ansible validator findings and OPA L005)
    and any supplied ``fqcn`` field. Falls back to parsing
    the violation message for ``"X -> Y"`` patterns. Returns None when no
    source carries a usable target; those findings remain for manual review.

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


def has_fqcn_target(violation: ViolationDict) -> bool:
    """Return whether a finding carries a deterministic FQCN target.

    Args:
        violation: Violation dict with optional target and module metadata.

    Returns:
        True when the finding has a well-formed, non-identity target.
    """
    current_key = str(violation.get("original_module") or violation.get("module") or "")
    return _resolve_fqcn(violation, current_key) is not None


def _is_valid_fqcn(candidate: str, current_key: str) -> bool:
    """Return True if candidate looks like a real FQCN, not an engine key.

    Args:
        candidate: String to validate as a FQCN.
        current_key: The current YAML module key (to reject identity matches).

    Returns:
        True if candidate is a well-formed FQCN.
    """
    return (
        candidate != current_key
        and not candidate.startswith("taskfile")
        and not candidate.endswith((".yml", ".yaml", ".json"))
        and _FQCN_RE.fullmatch(candidate) is not None
    )


def fix_fqcn(task: CommentedMap, violation: ViolationDict) -> bool:
    """Rewrite a module key to its resolved FQCN target.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line and optional resolved_fqcn.

    Returns:
        True if a change was applied.
    """
    for action_key in ("action", "local_action"):
        if action_key in task:
            action = task[action_key]
            value = action.get("module") if isinstance(action, dict) else action
            if not isinstance(value, str):
                return False
            match = re.match(r"^(\s*)(\S+)([\s\S]*)$", value)
            if match is None:
                return False
            original = str(violation.get("original_module", ""))
            current = match.group(2)
            if original and original != current:
                return False
            target = _resolve_fqcn(violation, current)
            if target is None:
                return False
            replacement = type(value)(match.group(1) + target + match.group(3))
            if isinstance(action, dict):
                action["module"] = replacement
            else:
                task[action_key] = replacement
            return True

    module_key = get_module_key(task)
    if module_key is None:
        return False
    original = str(violation.get("original_module", ""))
    if original and module_key != original:
        return False

    fqcn = _resolve_fqcn(violation, module_key)
    if fqcn is None or fqcn == module_key:
        return False

    rename_key(task, module_key, fqcn)
    if module_key in task.ca.items:
        task.ca.items[fqcn] = task.ca.items.pop(module_key)
    return True
