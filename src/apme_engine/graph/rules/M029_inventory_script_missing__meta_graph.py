"""GraphRule M029: inventory scripts must include ``_meta.hostvars``.

Ansible 2.23 enforces that dynamic inventory scripts include ``_meta``
with ``hostvars`` in their JSON output.  Without ``_meta``, Ansible must
call the script once per host (``--host <hostname>``), which is
deprecated.

Since runtime execution of inventory scripts is out of scope for static
analysis, this rule uses a **heuristic**: it scans for Python files in
standard inventory locations (``inventory/``, ``inventories/``) relative
to each playbook's directory.  Executable Python scripts without a
``.py`` suffix are included when they have a Python shebang.  Files that
look like executable inventory scripts (contain ``--list`` argument
handling) but do not reference
``_meta`` in their source are flagged.

Limitations:
- Cannot verify the actual JSON output structure.
- Only checks source-level ``_meta`` references — a script that
  constructs the key dynamically may produce a false positive.
- Only scans directories adjacent to playbook files.
"""

from __future__ import annotations

import ast
import logging
import os
import tokenize
from dataclasses import dataclass, field

from apme_engine.graph.content_graph import ContentGraph, NodeType
from apme_engine.graph.rule_base import GraphRule, GraphRuleResult
from apme_engine.graph.types import RuleScope, Severity, YAMLDict
from apme_engine.graph.types import RuleTag as Tag

logger = logging.getLogger(__name__)

_INVENTORY_DIR_NAMES = frozenset({"inventory", "inventories"})

# ``--list`` is required: helpers in inventory/ may use argparse or --host without
# being dynamic inventory scripts.
_LIST_FLAG = "--list"
_META_KEY = "_meta"


def _is_python_inventory_candidate(path: str) -> bool:
    """Return True when *path* is a Python inventory script candidate.

    Accepts ``.py`` files (except ``__init__.py``) and extensionless files
    that are executable and start with a Python shebang.

    Args:
        path: Absolute path to a file under an inventory directory.

    Returns:
        True when the file should be scanned for inventory-script markers.
    """
    basename = os.path.basename(path)
    if basename == "__init__.py":
        return False
    if basename.endswith(".py"):
        return True
    if not os.access(path, os.X_OK):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            first_line = f.readline()
    except OSError:
        return False
    return first_line.startswith("#!") and "python" in first_line.lower()


def _find_inventory_scripts(playbook_dir: str) -> list[str]:
    """Discover Python files in standard inventory directories.

    Walks ``inventory/`` and ``inventories/`` subdirectories of the given
    path looking for ``.py`` files and executable extensionless Python scripts.

    Args:
        playbook_dir: Directory containing the playbook.

    Returns:
        Sorted list of absolute paths to candidate Python files.
    """
    candidates: list[str] = []
    for inv_dir_name in _INVENTORY_DIR_NAMES:
        inv_dir = os.path.join(playbook_dir, inv_dir_name)
        if not os.path.isdir(inv_dir):
            continue
        for dirpath, _dirs, files in os.walk(inv_dir):
            for fname in files:
                path = os.path.join(dirpath, fname)
                if _is_python_inventory_candidate(path):
                    candidates.append(path)
    return sorted(candidates)


def _read_script_source(script_path: str) -> str | None:
    """Read inventory script source, honoring declared encodings.

    Args:
        script_path: Absolute path to the script file.

    Returns:
        Source text, or None when the file cannot be read or decoded.
    """
    try:
        with tokenize.open(script_path) as f:
            return f.read()
    except (OSError, UnicodeError, SyntaxError):
        return None


def _parse_inventory_script_source(source: str) -> ast.Module | None:
    """Parse inventory script source for syntax-aware marker checks.

    Args:
        source: Python source code text.

    Returns:
        Parsed module AST, or None when the source is not valid Python.
    """
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _looks_like_inventory_script(source: str) -> bool:
    """Return True if source appears to be a dynamic inventory script.

    Requires a ``--list`` string literal in executable code (not comments).
    Ansible uses ``--list`` to enumerate inventory from a script.

    Args:
        source: Python source code text.

    Returns:
        True when the source contains ``--list`` handling.
    """
    tree = _parse_inventory_script_source(source)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() == _LIST_FLAG:
            return True
    return False


def _has_meta_reference(source: str) -> bool:
    """Return True if source references ``_meta`` in executable code.

    Args:
        source: Python source code text.

    Returns:
        True when ``_meta`` appears as a name or string literal in code.
    """
    tree = _parse_inventory_script_source(source)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == _META_KEY:
            return True
        if isinstance(node, ast.Constant) and node.value == _META_KEY:
            return True
    return False


@dataclass
class InventoryScriptMissingMetaGraphRule(GraphRule):
    """Flag inventory scripts that may be missing ``_meta.hostvars``.

    Scans for Python files in ``inventory/``/``inventories/`` directories
    adjacent to playbooks.  Files that look like dynamic inventory scripts
    but do not reference ``_meta`` in their source are flagged.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        enabled: Whether the rule is enabled.
        name: Rule name.
        version: Rule version.
        severity: Severity level.
        tags: Rule tags.
        scope: Structural scope.
    """

    rule_id: str = "M029"
    description: str = "Inventory scripts must include _meta.hostvars in JSON output"
    enabled: bool = True
    name: str = "InventoryScriptMissingMeta"
    version: str = "v0.0.1"
    severity: Severity = Severity.MEDIUM
    tags: tuple[str, ...] = (Tag.AAP, Tag.PORTABILITY)
    scope: str = RuleScope.PLAYBOOK
    _checked_dirs: set[str] = field(default_factory=set)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match playbook nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True when the node is a playbook.
        """
        node = graph.get_node(node_id)
        if node is None:
            return False
        return node.node_type == NodeType.PLAYBOOK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Scan for inventory scripts missing ``_meta`` near the playbook.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            GraphRuleResult; verdict True when scripts missing _meta are found.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None

        playbook_dir = os.path.dirname(os.path.abspath(node.file_path))
        if playbook_dir in self._checked_dirs:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )
        self._checked_dirs.add(playbook_dir)

        scripts = _find_inventory_scripts(playbook_dir)
        if not scripts:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        missing_meta: list[str] = []
        for script_path in scripts:
            source = _read_script_source(script_path)
            if source is None:
                continue
            if _looks_like_inventory_script(source) and not _has_meta_reference(source):
                missing_meta.append(os.path.relpath(script_path, playbook_dir))

        if not missing_meta:
            return GraphRuleResult(
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start),
            )

        scripts_str = ", ".join(missing_meta)
        detail: YAMLDict = {
            "message": (
                f"Inventory script(s) may be missing _meta.hostvars (required in Ansible 2.23+): {scripts_str}"
            ),
        }
        return GraphRuleResult(
            verdict=True,
            detail=detail,
            node_id=node_id,
            file=(node.file_path, node.line_start),
        )
