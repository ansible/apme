"""GraphRule M047: inventory plugin ``disable_lookups`` arg has no effect.

In ansible-core 2.23 the ``disable_lookups`` argument to inventory plugin
``_compose()`` is a no-op (deprecated in 2.22, removed in 2.23). Inventory
plugin configs (e.g. ``constructed`` YAML inventory) that still pass it
should drop the key.

This rule covers the YAML inventory-config manifestation. REQ-018 reserves
M040 (``constructable_disable_lookups``) for the same deprecated argument
in Python plugin *source* via AST analysis — a different layer.

Follows the L111 pattern: matches PLAYBOOK nodes and scans inventory YAML
files discovered near the playbook.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict, YAMLValue
from apme_engine.graph.types import RuleTag as Tag

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Inventory file names checked for plugin options. Bare ``inventory`` /
# ``hosts`` (no extension) are accepted when their content parses as a YAML
# mapping (INI content never does); ``*.ini`` cannot express plugin args.
_INVENTORY_PATTERNS = (
    "inventory",
    "inventory.yml",
    "inventory.yaml",
    "hosts",
    "hosts.yml",
    "hosts.yaml",
)

# Top-level mapping keys only: YAML forbids tab indentation, so a leading
# non-space indent means nested. Optional single/double quotes accepted.
_DISABLE_LOOKUPS_RE = re.compile(r"""^disable_lookups\s*:|^["']disable_lookups["']\s*:""")


def _find_inventory_files(playbook_path: str) -> Iterator[Path]:
    """Find YAML inventory files relative to a playbook.

    Searches the playbook's directory (and parent when the playbook lives
    under ``playbooks/``/``plays/``), plus ``inventory/``/``inventories/``
    subdirectories.

    Args:
        playbook_path: Path to a playbook file.

    Yields:
        Path: Discovered YAML inventory file paths.
    """
    playbook = Path(playbook_path)
    search_dirs = [playbook.parent]
    inv_subdirs: list[Path] = []

    if playbook.parent.name.lower() in ("playbooks", "plays"):
        search_dirs.append(playbook.parent.parent)

    for base in search_dirs:
        for sub in ("inventory", "inventories"):
            inv_dir = base / sub
            if inv_dir.is_dir():
                inv_subdirs.append(inv_dir)

    seen: set[Path] = set()
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for pattern in _INVENTORY_PATTERNS:
            candidate = search_dir / pattern
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                yield candidate

    for inv_dir in inv_subdirs:
        try:
            candidates = sorted(inv_dir.iterdir())
        except OSError as exc:
            logger.debug("Skipping unreadable inventory directory %s: %s", inv_dir, exc)
            continue
        for candidate in candidates:
            if candidate.suffix.lower() not in (".yml", ".yaml"):
                continue
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                yield candidate


def _top_level_disable_lookups_line(content: str) -> int | None:
    """Return the 1-based line of a top-level ``disable_lookups:`` key.

    The caller must have verified via YAML parsing that the mapping really
    contains the key; this only locates it for reporting (comment lines
    are ignored).

    Args:
        content: Raw inventory file text.

    Returns:
        1-based line number, or None when not found.
    """
    for line_num, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _DISABLE_LOOKUPS_RE.match(line):
            return line_num
    return None


@dataclass
class DisableLookupsNoEffectGraphRule(GraphRule):
    """Flag the no-op ``disable_lookups`` inventory plugin argument.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope (inventory-level).
    """

    rule_id: str = "M047"
    description: str = "Inventory plugin disable_lookups argument has no effect (removed in 2.23)"
    enabled: bool = True
    name: str = "DisableLookupsNoEffect"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.CODING,)
    scope: str = RuleScope.INVENTORY

    # Per-scan state: inventory files already scanned (reset via reset_scan_state)
    _scanned_files: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        """Initialize mutable state and validate rule metadata."""
        super().__post_init__()
        object.__setattr__(self, "_scanned_files", set())

    def reset_scan_state(self) -> None:
        """Clear inventory-file deduplication state for a new scan pass."""
        self._scanned_files.clear()

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match PLAYBOOK nodes to trigger inventory file scanning.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node is a PLAYBOOK.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.PLAYBOOK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Scan nearby YAML inventory files for ``disable_lookups``.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the playbook node.

        Returns:
            GraphRuleResult with violations found, or None if not applicable.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        violations: list[YAMLValue] = []
        for inv_file in _find_inventory_files(node.file_path):
            inv_key = str(inv_file.resolve())
            if inv_key in self._scanned_files:
                continue
            self._scanned_files.add(inv_key)
            try:
                content = inv_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.debug("Skipping unreadable inventory file: %s", inv_file)
                continue
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError as exc:
                logger.debug("Skipping unparseable inventory file %s: %s", inv_file, exc)
                continue
            if not isinstance(data, dict) or "plugin" not in data or "disable_lookups" not in data:
                continue
            line = _top_level_disable_lookups_line(content) or 1
            violations.append(
                {
                    "file": str(inv_file),
                    "line": line,
                    "message": (
                        "The 'disable_lookups' inventory plugin argument has no "
                        "effect and is removed in ansible-core 2.23; remove it"
                    ),
                }
            )

        if not violations:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        detail: YAMLDict = {
            "message": ("Inventory plugin 'disable_lookups' argument has no effect (removed in ansible-core 2.23)"),
            "violations": violations,
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
