"""Violation enrichment -- link violations to hierarchy tree nodes.

``enrich_violations`` verifies or fills the ``path`` field on each
violation using the ``NodeIndex``.  ``build_reverse_index`` groups
violations by their ``path`` for node-centric queries.
"""

from __future__ import annotations

from collections import defaultdict

from apme_engine.engine.models import ViolationDict
from apme_engine.engine.node_index import NodeIndex
from apme_engine.remediation.transforms._helpers import violation_line_to_int


def enrich_violations(
    violations: list[ViolationDict],
    node_index: NodeIndex,
) -> None:
    """Verify or fill the ``path`` field on each violation.

    For violations that already carry a valid ``path`` present in the
    index the field is left as-is.  Otherwise the function attempts a
    ``(file, line)`` lookup and writes the matching node key back into
    ``path``.  Violations that cannot be matched keep ``path`` empty.

    Mutates *violations* in place.

    Args:
        violations: List of violation dicts to enrich.
        node_index: Pre-built NodeIndex from hierarchy payload.
    """
    for v in violations:
        path = str(v.get("path", "") or "")

        if path and path in node_index:
            continue

        file_path = str(v.get("file", "") or "")
        line = violation_line_to_int(v)
        if not file_path or line <= 0:
            continue

        node = node_index.find_by_file_line(file_path, line)
        if node is not None:
            v["path"] = str(node.get("key", ""))


def build_reverse_index(
    violations: list[ViolationDict],
) -> dict[str, list[ViolationDict]]:
    """Group violations by their ``path`` (tree node key).

    Violations without a ``path`` are collected under the empty string
    key ``""``.

    Args:
        violations: List of violation dicts (should be enriched first).

    Returns:
        Dict mapping node key to the list of violations at that node.
    """
    index: dict[str, list[ViolationDict]] = defaultdict(list)
    for v in violations:
        key = str(v.get("path", "") or "")
        index[key].append(v)
    return dict(index)
