"""Python file analysis for ContentGraph (ADR-044).

Uses ``ast`` to extract structural information from Ansible modules,
module_utils, and filter/action/lookup plugins.  Creates ``py_imports``
edges between modules and module_utils, and captures basic code quality
signals (function count, class count, docstring presence).

Public API
----------
- ``PythonFileAnalyzer`` — analyzes a single Python file
- ``PythonAnalysisResult`` — structured output from analysis
"""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PythonAnalysisResult:
    """Structured output from analyzing a Python file.

    Attributes:
        file_path: Absolute or relative path to the Python file.
        imports: List of imported module paths (from ... import ...).
        module_utils_imports: Imports that resolve to module_utils.
        function_count: Number of top-level function definitions.
        class_count: Number of top-level class definitions.
        has_documentation: True if a DOCUMENTATION string constant is found.
        has_examples: True if an EXAMPLES string constant is found.
        has_return: True if a RETURN string constant is found.
        has_main: True if a ``main()`` function or ``if __name__`` guard exists.
        argument_spec_keys: Keys from the argument_spec dict if detectable.
    """

    file_path: str = ""
    imports: list[str] = field(default_factory=list)
    module_utils_imports: list[str] = field(default_factory=list)
    function_count: int = 0
    class_count: int = 0
    has_documentation: bool = False
    has_examples: bool = False
    has_return: bool = False
    has_main: bool = False
    argument_spec_keys: list[str] = field(default_factory=list)


class PythonFileAnalyzer:
    """Analyze an Ansible Python file (module, module_utils, plugin).

    Uses ``ast.parse`` — no imports executed, no dependencies required.
    """

    def __init__(self, collection_root: str = "") -> None:
        """Initialize analyzer.

        Args:
            collection_root: Root path of the collection, used to resolve
                relative module_utils imports to node IDs.
        """
        self._collection_root = collection_root

    def analyze(self, file_path: str) -> PythonAnalysisResult:
        """Analyze a Python file and return structured results.

        Args:
            file_path: Path to the Python file.

        Returns:
            PythonAnalysisResult with extracted information.
        """
        result = PythonAnalysisResult(file_path=file_path)

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            logger.debug("Cannot read Python file: %s", file_path)
            return result

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            logger.debug("Syntax error in Python file: %s", file_path)
            return result

        self._analyze_tree(tree, result)
        return result

    def analyze_source(self, source: str, file_path: str = "<string>") -> PythonAnalysisResult:
        """Analyze Python source code directly.

        Args:
            source: Python source code string.
            file_path: Virtual file path for the result.

        Returns:
            PythonAnalysisResult with extracted information.
        """
        result = PythonAnalysisResult(file_path=file_path)

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return result

        self._analyze_tree(tree, result)
        return result

    def _analyze_tree(self, tree: ast.Module, result: PythonAnalysisResult) -> None:
        """Walk the AST and populate the result.

        Args:
            tree: Parsed AST module node.
            result: PythonAnalysisResult to populate in-place.
        """
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(alias.name)
                    if "module_utils" in alias.name:
                        result.module_utils_imports.append(alias.name)

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                result.imports.append(module)
                if "module_utils" in module:
                    result.module_utils_imports.append(module)

            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                result.function_count += 1
                if node.name == "main":
                    result.has_main = True

            elif isinstance(node, ast.ClassDef):
                result.class_count += 1

            elif isinstance(node, ast.Assign):
                self._check_doc_constants(node, result)
                self._check_argument_spec(node, result)

            elif isinstance(node, ast.If) and _is_name_main_guard(node):
                result.has_main = True

    def _check_doc_constants(self, node: ast.Assign, result: PythonAnalysisResult) -> None:
        """Check if an assignment defines DOCUMENTATION, EXAMPLES, or RETURN.

        Args:
            node: AST Assign node to inspect.
            result: PythonAnalysisResult to update.
        """
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "DOCUMENTATION":
                result.has_documentation = True
            elif target.id == "EXAMPLES":
                result.has_examples = True
            elif target.id == "RETURN":
                result.has_return = True

    def _check_argument_spec(self, node: ast.Assign, result: PythonAnalysisResult) -> None:
        """Try to extract argument_spec keys from a dict literal.

        Args:
            node: AST Assign node to inspect.
            result: PythonAnalysisResult to update.
        """
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if "argument_spec" not in target.id.lower():
                continue
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        result.argument_spec_keys.append(key.value)

    def resolve_module_utils_path(self, import_path: str) -> str | None:
        """Resolve a module_utils import to a file path relative to collection root.

        Args:
            import_path: Python import path (e.g.
                ``ansible_collections.testorg.graph_patterns.plugins.module_utils.deploy_helpers``).

        Returns:
            Relative file path (e.g. ``plugins/module_utils/deploy_helpers.py``)
            or None if unresolvable.
        """
        marker = "plugins.module_utils."
        idx = import_path.find(marker)
        if idx < 0:
            marker = "module_utils."
            idx = import_path.find(marker)
            if idx < 0:
                return None

        suffix = import_path[idx:].replace(".", os.sep) + ".py"
        return suffix


def _is_name_main_guard(node: ast.If) -> bool:
    """Check if an If node is ``if __name__ == '__main__':``.

    Args:
        node: AST If node to check.

    Returns:
        True if the node is a ``__name__ == '__main__'`` guard.
    """
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.comparators) != 1:
        return False
    comp = test.comparators[0]
    return isinstance(comp, ast.Constant) and comp.value == "__main__"
