"""Unit segmenter — extract fixable units from hierarchy and group violations.

A *FixableUnit* is a self-contained code segment (a single task, play
header, or block) that can be sent to an LLM for independent repair.
The segmenter uses the ``NodeIndex`` built in Phase 2 to identify unit
boundaries, then maps each Tier 2 violation to the unit that owns it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from apme_engine.engine.models import ViolationDict
from apme_engine.engine.node_index import NodeDict, NodeIndex
from apme_engine.remediation.transforms._helpers import violation_line_to_int


@dataclass
class FixableUnit:
    """A code segment that can be independently fixed by AI.

    Attributes:
        node_key: Hierarchy node key (e.g. ``TaskCall playbook:site.yml#play:[0]#task:[3]``).
        node_type: Node type string (``taskcall``, ``playcall``, etc.).
        file: Absolute path to the source file.
        line_start: 1-based first line of the unit (inclusive).
        line_end: 1-based last line of the unit (inclusive).
        snippet: Extracted YAML text for just this unit.
        violations: Violations mapped to this unit.
    """

    node_key: str
    node_type: str
    file: str
    line_start: int
    line_end: int
    snippet: str
    violations: list[ViolationDict] = field(default_factory=list)


def _node_line_range(node: NodeDict) -> tuple[int, int] | None:
    """Extract (start, end) 1-based line range from a node dict.

    Args:
        node: Node dict with a ``line`` field.

    Returns:
        (line_start, line_end) tuple or None if unavailable.
    """
    line = node.get("line")
    if isinstance(line, list | tuple) and len(line) >= 2:
        return (int(line[0]), int(line[1]))
    if isinstance(line, int):
        return (line, line)
    return None


def _paths_match(node_file: str, file_path: str) -> bool:
    """Check if a node's file field matches the given file path.

    Handles the mismatch between hierarchy's relative paths (e.g. 'site.yml')
    and the engine's absolute paths.

    Args:
        node_file: File path from hierarchy node (often relative/basename).
        file_path: File path from engine (typically absolute).

    Returns:
        True if the paths refer to the same file.
    """
    if node_file == file_path:
        return True
    if file_path.endswith("/" + node_file) or file_path.endswith("\\" + node_file):
        return True
    return node_file.endswith("/" + file_path) or node_file.endswith("\\" + file_path)


def extract_units(
    file_path: str,
    file_content: str,
    node_index: NodeIndex,
) -> list[FixableUnit]:
    """Extract all fixable units for a single file from the hierarchy.

    Scans all nodes in the index that belong to *file_path* and have
    ``taskcall`` type.  Each task becomes a ``FixableUnit`` with its
    YAML snippet extracted from *file_content*.

    Args:
        file_path: Absolute path to the YAML file.
        file_content: Full content of the file.
        node_index: Pre-built NodeIndex.

    Returns:
        List of FixableUnit sorted by line_start ascending.
    """
    lines = file_content.splitlines(keepends=True)
    total = len(lines)
    units: list[FixableUnit] = []

    for key, node in node_index.items():
        node_file = str(node.get("file", ""))
        if not _paths_match(node_file, file_path):
            continue

        node_type = str(node.get("type", ""))
        if node_type != "taskcall":
            continue

        lr = _node_line_range(node)
        if lr is None:
            continue

        start, end = lr
        if start < 1 or end > total:
            continue

        snippet = "".join(lines[start - 1 : end])
        units.append(
            FixableUnit(
                node_key=key,
                node_type=node_type,
                file=file_path,
                line_start=start,
                line_end=end,
                snippet=snippet,
            )
        )

    units.sort(key=lambda u: u.line_start)
    return units


def assign_violations_to_units(
    units: list[FixableUnit],
    violations: list[ViolationDict],
) -> list[ViolationDict]:
    """Map each violation to the unit whose line range contains it.

    Violations whose ``path`` matches a unit's ``node_key`` are assigned
    directly.  Fallback: violations are matched by ``(file, line)``
    containment within a unit's line range.

    Args:
        units: FixableUnits for one file, sorted by line_start.
        violations: Tier 2 violations for the same file.

    Returns:
        Violations that could not be assigned to any unit (orphans).
    """
    by_key = {u.node_key: u for u in units}
    orphans: list[ViolationDict] = []

    for v in violations:
        path = str(v.get("path", "") or "")
        if path and path in by_key:
            by_key[path].violations.append(v)
            continue

        vline = violation_line_to_int(v)
        assigned = False
        for unit in units:
            if unit.line_start <= vline <= unit.line_end:
                unit.violations.append(v)
                assigned = True
                break
        if not assigned:
            orphans.append(v)

    return orphans


def group_violations_by_file(
    violations: list[ViolationDict],
    resolve_file: Callable[[str], str | None],
) -> dict[str, list[ViolationDict]]:
    """Group violations by resolved file path.

    Args:
        violations: All Tier 2 violations.
        resolve_file: Callable mapping raw file strings to canonical paths.

    Returns:
        Dict of file_path -> violations.
    """
    by_file: dict[str, list[ViolationDict]] = defaultdict(list)
    for v in violations:
        vf_raw = str(v.get("file", ""))
        vf = resolve_file(vf_raw)
        if vf is not None:
            by_file[vf].append(v)
    return dict(by_file)
