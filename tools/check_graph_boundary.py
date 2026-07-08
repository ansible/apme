#!/usr/bin/env python3
"""Verify import boundaries between apme_engine.graph and apme_engine.engine (ADR-059).

Rules:
  1. graph/ must NOT import from engine/ (the dependency is one-way: engine → graph)
  2. graph/ must NOT import from validators/ (rules now live in graph/rules/)

Allowed exceptions:
  - graph/content_graph.py may lazy-import engine.yaml_utils (for apply_transform)
  - TYPE_CHECKING blocks are ignored (type-only references are fine)

Exit codes:
  0  All boundaries respected
  1  Boundary violation(s) found
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
_GRAPH_PKG = _SRC / "apme_engine" / "graph"

_FORBIDDEN_PACKAGES = (
    "apme_engine.engine",
    "apme_engine.validators",
    "apme_engine.daemon",
)

_ALLOWED_LAZY_IMPORTS: dict[str, set[str]] = {
    "content_graph.py": {"apme_engine.engine.yaml_utils"},
}

_ALLOWED_RULE_IMPORTS: dict[str, set[str]] = {
    "L030_non_builtin_use_graph.py": {"apme_engine.engine.finder"},
}


def _check_file(path: Path) -> list[str]:
    """Return boundary violations for a single Python file."""
    relative = path.relative_to(_GRAPH_PKG)
    rel_str = str(relative)
    filename = relative.name
    allowed = _ALLOWED_LAZY_IMPORTS.get(relative.parts[0] if relative.parts else "", set())
    allowed = allowed | _ALLOWED_RULE_IMPORTS.get(filename, set())

    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []

    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        pass
                continue

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if _is_inside_type_checking(node, tree):
                continue

            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif node.module:
                modules = [node.module]

            for mod in modules:
                if any(mod == p or mod.startswith(p + ".") for p in _FORBIDDEN_PACKAGES):
                    if mod in allowed:
                        continue
                    violations.append(f"  {rel_str}:{node.lineno}: graph/ must not import '{mod}'")

    return violations


def _is_inside_type_checking(node: ast.AST, tree: ast.Module) -> bool:
    """Check if an import node is inside an ``if TYPE_CHECKING:`` block."""
    for top_node in ast.walk(tree):
        if isinstance(top_node, ast.If):
            test = top_node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                for child in ast.walk(top_node):
                    if child is node:
                        return True
    return False


def main() -> int:
    """Run boundary checks on all graph/ Python files."""
    all_violations: list[str] = []

    for py_file in sorted(_GRAPH_PKG.rglob("*.py")):
        all_violations.extend(_check_file(py_file))

    if all_violations:
        print("ADR-059 import boundary violations:")
        for v in all_violations:
            print(v)
        print(f"\n{len(all_violations)} violation(s) found.")
        return 1

    print("ADR-059 import boundaries: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
