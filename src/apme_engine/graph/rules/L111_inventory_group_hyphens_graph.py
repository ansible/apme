"""GraphRule L111: inventory group names should not contain hyphens.

Detects hyphens in inventory group names which cause issues with Jinja2
dot notation access (e.g., ``groups.web-servers`` fails as Python syntax).

Scans INI and YAML inventory files discovered relative to playbook locations.
Addresses AAPRFE-2997.
"""

from __future__ import annotations

import logging
import os
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

# Reserved group names to skip
_RESERVED_GROUPS = frozenset({"all", "ungrouped"})

# INI section pattern: [group-name] or [group-name:children] or [group-name:vars]
_INI_SECTION_RE = re.compile(r"^\[([^\]:]+)(?::(children|vars))?\]")

# Common inventory file patterns
_INVENTORY_PATTERNS = (
    "inventory",
    "inventory.ini",
    "inventory.yml",
    "inventory.yaml",
    "hosts",
    "hosts.ini",
    "hosts.yml",
    "hosts.yaml",
)


def _find_inventory_files(playbook_path: str) -> Iterator[Path]:
    """Find inventory files relative to a playbook.

    Searches the playbook's directory and parent for common inventory patterns.

    Args:
        playbook_path: Path to a playbook file.

    Yields:
        Path: Discovered inventory file paths.
    """
    playbook = Path(playbook_path)
    search_dirs = [playbook.parent]

    # Also check parent (playbook might be in playbooks/ subdir)
    if playbook.parent.name.lower() in ("playbooks", "plays"):
        search_dirs.append(playbook.parent.parent)

    # Check for inventory/ subdirectory
    for base in list(search_dirs):
        inv_dir = base / "inventory"
        if inv_dir.is_dir():
            search_dirs.append(inv_dir)
        inv_dir = base / "inventories"
        if inv_dir.is_dir():
            search_dirs.append(inv_dir)

    seen: set[Path] = set()
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for pattern in _INVENTORY_PATTERNS:
            candidate = search_dir / pattern
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                yield candidate


def _parse_ini_groups(content: str, file_path: str) -> Iterator[tuple[str, int]]:
    """Extract group names and line numbers from INI inventory.

    Args:
        content: INI file content.
        file_path: Path for error context (unused but available).

    Yields:
        tuple[str, int]: Tuples of (group_name, line_number).
    """
    in_children_section = False
    for line_num, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        section_match = _INI_SECTION_RE.match(stripped)
        if section_match:
            group_name = section_match.group(1)
            section_type = section_match.group(2)
            in_children_section = section_type == "children"
            if group_name not in _RESERVED_GROUPS:
                yield group_name, line_num
            continue

        if in_children_section:
            if stripped.startswith("#") or stripped.startswith(";"):
                continue
            if stripped not in _RESERVED_GROUPS:
                yield stripped, line_num


def _parse_yaml_groups(
    data: object,
    path: tuple[str, ...] = (),
    line_map: dict[str, int] | None = None,
) -> Iterator[tuple[str, int]]:
    """Extract group names from YAML inventory structure.

    Recursively walks YAML looking for keys under 'children' at any depth.

    Args:
        data: Parsed YAML data.
        path: Current path for context.
        line_map: Optional mapping of dotted paths to line numbers.

    Yields:
        tuple[str, int]: Tuples of (group_name, line_number).
    """
    if not isinstance(data, dict):
        return

    # Check if this dict has children key
    children = data.get("children")
    if isinstance(children, dict):
        for group_name in children:
            if group_name in _RESERVED_GROUPS:
                continue
            # Approximate line number (YAML parsing loses this)
            line = line_map.get(f"{'.'.join(path)}.children.{group_name}", 1) if line_map else 1
            yield group_name, line
            # Recurse into child groups
            yield from _parse_yaml_groups(children[group_name], (*path, "children", group_name), line_map)

    # Also check top-level keys in 'all' structure
    if "all" in data and isinstance(data["all"], dict):
        yield from _parse_yaml_groups(data["all"], (*path, "all"), line_map)


def _get_yaml_line_map(content: str) -> dict[str, int]:
    """Build approximate line number map for YAML keys.

    Simple regex-based approach since ruamel.yaml roundtrip is heavyweight.

    Args:
        content: YAML file content.

    Returns:
        Dict mapping dotted key paths to line numbers.
    """
    line_map: dict[str, int] = {}
    key_re = re.compile(r"^(\s*)([a-zA-Z0-9_-]+):")
    path_stack: list[tuple[int, str]] = []

    for line_num, line in enumerate(content.splitlines(), start=1):
        match = key_re.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2)

        # Pop stack to current indent level
        while path_stack and path_stack[-1][0] >= indent:
            path_stack.pop()

        path_stack.append((indent, key))
        dotted = ".".join(k for _, k in path_stack)
        line_map[dotted] = line_num

    return line_map


@dataclass
class InventoryGroupHyphensGraphRule(GraphRule):
    """Detect hyphens in inventory group names.

    Hyphens in group names cause Jinja2 dot notation access to fail
    (``groups.web-servers`` is invalid Python syntax).

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
        precedence: Evaluation order.
    """

    rule_id: str = "L111"
    description: str = "Inventory group names should not contain hyphens"
    enabled: bool = True
    name: str = "InventoryGroupHyphens"
    version: str = "v0.0.1"
    severity: Severity = Severity.LOW
    tags: tuple[str, ...] = (Tag.QUALITY,)
    scope: str = RuleScope.INVENTORY
    precedence: int = 100  # Run late, after structural rules

    # Per-scan state: directories already scanned (reset via reset_scan_state)
    _scanned_dirs: set[str] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        """Initialize mutable state and validate rule metadata."""
        super().__post_init__()
        object.__setattr__(self, "_scanned_dirs", set())

    def reset_scan_state(self) -> None:
        """Clear directory deduplication state for a new scan pass."""
        self._scanned_dirs.clear()

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
        """Scan inventory files for group names with hyphens.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult with violations found, or None if not applicable.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        playbook_dir = os.path.dirname(node.file_path)
        if playbook_dir in self._scanned_dirs:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        self._scanned_dirs.add(playbook_dir)

        violations: list[YAMLValue] = []

        for inv_file in _find_inventory_files(node.file_path):
            try:
                content = inv_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.debug("Skipping unreadable inventory file: %s", inv_file)
                continue

            groups_with_hyphens: list[tuple[str, int]] = []

            # Detect format by extension or content
            suffix = inv_file.suffix.lower()
            if suffix in (".yml", ".yaml"):
                groups_with_hyphens.extend(_parse_yaml_inventory(content, str(inv_file)))
            elif suffix == ".ini" or _looks_like_ini(content):
                seen_ini: set[str] = set()
                for group_name, line in _parse_ini_groups(content, str(inv_file)):
                    if "-" in group_name and group_name not in seen_ini:
                        seen_ini.add(group_name)
                        groups_with_hyphens.append((group_name, line))
            elif suffix == "" and _looks_like_yaml(content):
                # Extensionless inventory files (e.g., "inventory", "hosts")
                groups_with_hyphens.extend(_parse_yaml_inventory(content, str(inv_file)))

            for group_name, line in groups_with_hyphens:
                violations.append(
                    {
                        "file": str(inv_file),
                        "line": line,
                        "group_name": group_name,
                        "suggested": group_name.replace("-", "_"),
                        "message": (
                            f"Group '{group_name}' contains hyphens; "
                            f"use '{group_name.replace('-', '_')}' for Jinja2 dot notation compatibility"
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
            "message": f"Found {len(violations)} inventory group(s) with hyphens",
            "violations": violations,
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )


def _looks_like_ini(content: str) -> bool:
    """Check if content looks like INI format.

    Args:
        content: File content to inspect.

    Returns:
        True if content appears to be INI format.
    """
    for line in content.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return True
    return False


def _looks_like_yaml(content: str) -> bool:
    """Check if content looks like YAML inventory format.

    Looks for YAML indicators: leading dashes, colons after keys,
    or 'all:' / 'children:' inventory keywords.

    Args:
        content: File content to inspect.

    Returns:
        True if content appears to be YAML format.
    """
    yaml_indicators = ("all:", "children:", "hosts:", "vars:")
    for line in content.splitlines()[:30]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Check for YAML inventory keywords
        if any(stripped.startswith(kw) for kw in yaml_indicators):
            return True
        # Check for key: value pattern (but not INI sections)
        if ":" in stripped and not stripped.startswith("["):
            return True
    return False


def _parse_yaml_inventory(content: str, source: str = "") -> list[tuple[str, int]]:
    """Parse YAML inventory and return groups with hyphens.

    Args:
        content: YAML file content.
        source: Inventory file path for log context.

    Returns:
        List of (group_name, line_number) tuples for groups containing hyphens.
    """
    groups_with_hyphens: list[tuple[str, int]] = []
    seen: set[str] = set()

    try:
        data = yaml.safe_load(content)
        line_map = _get_yaml_line_map(content)
        for group_name, line in _parse_yaml_groups(data, line_map=line_map):
            if "-" in group_name and group_name not in seen:
                seen.add(group_name)
                groups_with_hyphens.append((group_name, line))
    except yaml.YAMLError as err:
        logger.warning("Failed to parse YAML inventory %s: %s", source or "<unknown>", err)

    return groups_with_hyphens
