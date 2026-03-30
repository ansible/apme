"""Tests for PythonFileAnalyzer (ADR-044 Phase 1)."""

from __future__ import annotations

import textwrap

from apme_engine.engine.python_analyzer import PythonFileAnalyzer


class TestPythonFileAnalyzer:
    """Tests for ``PythonFileAnalyzer``."""

    def test_basic_module(self) -> None:
        """Verify DOCUMENTATION, EXAMPLES, RETURN, and main are detected."""
        source = textwrap.dedent("""\
            from ansible.module_utils.basic import AnsibleModule

            DOCUMENTATION = r\"\"\"
            module: test_mod
            \"\"\"

            EXAMPLES = r\"\"\"
            - test_mod:
                name: foo
            \"\"\"

            RETURN = r\"\"\"
            result:
                description: The result
            \"\"\"

            def main():
                module = AnsibleModule(argument_spec={"name": {"type": "str"}})
                module.exit_json(changed=False)

            if __name__ == "__main__":
                main()
        """)
        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze_source(source, "test_mod.py")

        assert result.has_documentation
        assert result.has_examples
        assert result.has_return
        assert result.has_main
        assert result.function_count >= 1

    def test_module_utils_import(self) -> None:
        """Verify module_utils import paths are collected."""
        source = textwrap.dedent("""\
            from ansible_collections.testorg.myplugin.plugins.module_utils.helpers import validate
            from ansible.module_utils.basic import AnsibleModule

            def main():
                pass
        """)
        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze_source(source, "my_module.py")

        assert len(result.module_utils_imports) == 2
        assert any("module_utils.helpers" in imp for imp in result.module_utils_imports)
        assert any("module_utils.basic" in imp for imp in result.module_utils_imports)

    def test_filter_plugin(self) -> None:
        """Verify FilterModule-style sources count as one class, no top-level functions."""
        source = textwrap.dedent("""\
            class FilterModule:
                def filters(self):
                    return {"to_nice_json": self.to_nice_json}

                @staticmethod
                def to_nice_json(data):
                    import json
                    return json.dumps(data, indent=2)
        """)
        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze_source(source, "format_utils.py")

        assert result.class_count == 1
        assert result.function_count == 0

    def test_syntax_error_graceful(self) -> None:
        """Verify invalid Python yields zero functions without raising."""
        source = "def broken(:\n    pass"
        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze_source(source, "broken.py")

        assert result.file_path == "broken.py"
        assert result.function_count == 0

    def test_resolve_module_utils_path(self) -> None:
        """Verify FQCN module_utils paths resolve to a ``.py`` suffix."""
        analyzer = PythonFileAnalyzer()
        path = analyzer.resolve_module_utils_path(
            "ansible_collections.testorg.graph_patterns.plugins.module_utils.deploy_helpers"
        )
        assert path is not None
        assert path.endswith("deploy_helpers.py")
        assert "module_utils" in path

    def test_argument_spec_extraction(self) -> None:
        """Verify ``argument_spec`` dict keys are extracted."""
        source = textwrap.dedent("""\
            argument_spec = {
                "name": {"type": "str", "required": True},
                "version": {"type": "str"},
            }
        """)
        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze_source(source, "mod.py")
        assert "name" in result.argument_spec_keys
        assert "version" in result.argument_spec_keys

    def test_real_fixture_file(self) -> None:
        """Verify fixture module analysis when the file is present."""
        from pathlib import Path

        fixture = Path("tests/fixtures/graph-patterns/plugins/modules/deploy_artifact.py")
        if not fixture.exists():
            return

        analyzer = PythonFileAnalyzer()
        result = analyzer.analyze(str(fixture))

        assert result.has_documentation
        assert result.has_main
        assert len(result.module_utils_imports) >= 1
