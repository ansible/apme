"""Unit tests for generate_rule_catalog heuristics.

Covers ``_is_incidental_reference`` (test-detection) and
``_parse_frontmatter`` (YAML frontmatter parsing) in
``tools/generate_rule_catalog.py``.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = str(REPO_ROOT / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

_mod: types.ModuleType = importlib.import_module("generate_rule_catalog")
_is_incidental_reference = _mod._is_incidental_reference
_parse_frontmatter = _mod._parse_frontmatter
_normalize_validator = _mod._normalize_validator
_rule_ids_from_module_name = _mod._rule_ids_from_module_name


# ---------------------------------------------------------------------------
# _is_incidental_reference
# ---------------------------------------------------------------------------


class TestIsIncidentalReference:
    """Tests for the incidental-reference detection heuristic."""

    def test_filename_contains_rule_id(self) -> None:
        """File named after the rule is never incidental.

        Tests:
            Filename ``test_L039_rules.py`` contains ``L039``.
        """
        text = 'rule_id = "L039"  # just a string in fixture data'
        assert not _is_incidental_reference("L039", text, "test_L039_rules.py")

    def test_class_name_contains_rule_id(self) -> None:
        """File with a test class embedding the rule ID is genuine.

        Tests:
            ``class TestL039Rule`` matches the context pattern.
        """
        text = 'class TestL039Rule:\n    rule_id = "L039"\n'
        assert not _is_incidental_reference("L039", text, "test_misc.py")

    def test_def_test_contains_rule_id(self) -> None:
        """File with a test function embedding the rule ID is genuine.

        Tests:
            ``def test_l039_fires`` matches the context pattern.
        """
        text = 'def test_l039_fires():\n    assert "L039" in results\n'
        assert not _is_incidental_reference("L039", text, "test_other.py")

    def test_import_contains_rule_id(self) -> None:
        """File importing something with the rule ID is genuine.

        Tests:
            ``import ...L039`` matches the context pattern.
        """
        text = 'from apme_engine.validators.native.rules.L039_undefined import Rule\n"L039"\n'
        assert not _is_incidental_reference("L039", text, "test_validators.py")

    def test_rule_id_assignment(self) -> None:
        """File with ``rule_id = "L039"`` is genuine.

        Tests:
            Direct rule_id assignment matches the assignment pattern.
        """
        text = 'rule_id = "L039"\nsome_other_code()\n'
        assert not _is_incidental_reference("L039", text, "test_generic.py")

    def test_rule_id_colon_assignment(self) -> None:
        """File with ``rule_id: "L039"`` (dict-style) is genuine.

        Tests:
            Colon-separated assignment matches the assignment pattern.
        """
        text = '{"rule_id": "L039", "severity": "low"}\n'
        assert not _is_incidental_reference("L039", text, "test_generic.py")

    def test_incidental_fixture_data(self) -> None:
        """Rule ID appearing only in fixture data is incidental.

        Tests:
            No class/def/import context for the rule ID.
        """
        text = 'SAMPLE_DATA = ["L039", "L040", "M001"]\ndef test_count():\n    pass\n'
        assert _is_incidental_reference("L039", text, "test_summary.py")

    def test_incidental_string_literal(self) -> None:
        """Rule ID in a data list without import/class/def context is incidental.

        Tests:
            ``"L039"`` appears in sample data, no structural context.
        """
        text = 'SAMPLE_IDS = ["L039", "M001"]\ndef test_something_else():\n    pass\n'
        assert _is_incidental_reference("L039", text, "test_unrelated.py")

    def test_case_insensitive_filename_match(self) -> None:
        """Filename check is case-insensitive.

        Tests:
            ``test_l039.py`` matches rule ID ``L039``.
        """
        text = '"L039" in some list'
        assert not _is_incidental_reference("L039", text, "test_l039.py")


class TestRuleIdsFromModuleName:
    """Tests for grouped rule module name expansion."""

    def test_m001_m004_range(self) -> None:
        """M001_M004_introspect expands to four rule IDs."""
        assert _rule_ids_from_module_name("M001_M004_introspect") == {
            "M001",
            "M002",
            "M003",
            "M004",
        }

    def test_non_range_module(self) -> None:
        """Unrelated module names return empty set."""
        assert _rule_ids_from_module_name("L039_undefined_variable_graph") == set()


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    """Tests for YAML frontmatter extraction from markdown files."""

    def test_simple_frontmatter(self, tmp_path: Path) -> None:
        """Parses simple key-value frontmatter.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "rule.md"
        md.write_text("---\nrule_id: L001\ndescription: Test rule\n---\n# Rule\n")
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "L001"
        assert result["description"] == "Test rule"

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        """Returns empty dict when no frontmatter is present.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "plain.md"
        md.write_text("# Just a heading\n\nSome content.\n")
        result = _parse_frontmatter(md)
        assert result == {}

    def test_quoted_values(self, tmp_path: Path) -> None:
        """Handles quoted YAML values in frontmatter.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "quoted.md"
        md.write_text('---\nrule_id: "M030"\ndescription: "A quoted description"\n---\n')
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "M030"
        assert result["description"] == "A quoted description"

    def test_status_field(self, tmp_path: Path) -> None:
        """Parses status and status_reason fields.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "stub.md"
        md.write_text("---\nrule_id: L999\nstatus: stub\nstatus_reason: Covered by ansible-lint\n---\n")
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "L999"
        assert result["status"] == "stub"
        assert result["status_reason"] == "Covered by ansible-lint"

    def test_validator_field(self, tmp_path: Path) -> None:
        """Parses the validator field from frontmatter.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "opa.md"
        md.write_text("---\nrule_id: P005\nvalidator: opa\ndescription: OPA rule\n---\n")
        result = _parse_frontmatter(md)
        assert result["validator"] == "opa"

    def test_multiline_description_yaml(self, tmp_path: Path) -> None:
        """Handles YAML folded scalar (``>``) in frontmatter via yaml.safe_load.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "folded.md"
        md.write_text("---\nrule_id: L050\ndescription: >\n  A long\n  description\n---\n")
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "L050"
        assert "long" in result["description"]

    def test_none_values_excluded(self, tmp_path: Path) -> None:
        """Keys with None values are excluded from the result.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "nonevals.md"
        md.write_text("---\nrule_id: L060\nstatus:\n---\n")
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "L060"
        assert "status" not in result

    def test_import_error_fallback_warns_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ImportError for PyYAML emits a stderr warning before regex fallback.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
            capsys: Pytest capture fixture for stdout/stderr.
        """
        import builtins

        md = tmp_path / "rule.md"
        md.write_text("---\nrule_id: L001\ndescription: Test rule\n---\n")

        real_import = builtins.__import__

        def fake_import(
            name: str,
            globals: Mapping[str, object] | None = None,
            locals: Mapping[str, object] | None = None,
            fromlist: Sequence[str] = (),
            level: int = 0,
        ) -> object:
            if name == "yaml":
                raise ImportError("PyYAML not installed")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "L001"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "PyYAML" in captured.err


class TestNormalizeValidator:
    """Tests for validator name normalization from frontmatter."""

    def test_known_native(self) -> None:
        """Lowercase native maps to Native."""
        assert _normalize_validator("native") == "Native"

    def test_known_ansible(self) -> None:
        """Lowercase ansible maps to Ansible."""
        assert _normalize_validator("ansible") == "Ansible"

    def test_unknown_string_capitalized(self) -> None:
        """Unknown validator strings are title-cased."""
        assert _normalize_validator("custom") == "Custom"

    def test_empty_defaults_to_native(self) -> None:
        """Empty string defaults to Native."""
        assert _normalize_validator("") == "Native"


class TestGetTestCache:
    """Tests for rule_id -> test file cache construction."""

    def test_import_graph_rule_module_counts_as_tested(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Importing a graph rule module links the rule ID to that test file.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_r402_loader.py"
        test_file.write_text(
            "from apme_engine.validators.native.rules.R402_list_all_used_variables_graph import "
            "ListAllUsedVariablesGraphRule\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "TESTS_DIR", tests_dir)
        monkeypatch.setattr(_mod, "_TEST_CACHE", None)
        cache = _mod._get_test_cache()
        assert "test_r402_loader.py" in cache.get("R402", [])

    def test_grouped_module_import_expands_rule_range(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Grouped modules like M001_M004_introspect credit each rule in the range.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_ansible_cache.py"
        test_file.write_text(
            "from apme_engine.validators.ansible.rules import M001_M004_introspect\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "TESTS_DIR", tests_dir)
        monkeypatch.setattr(_mod, "_TEST_CACHE", None)
        cache = _mod._get_test_cache()
        for rid in ("M001", "M002", "M003", "M004"):
            assert "test_ansible_cache.py" in cache.get(rid, [])

    def test_incidental_string_literal_excluded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """String literals mentioning a rule ID without imports are incidental.

        Args:
            tmp_path: Pytest temporary directory fixture.
            monkeypatch: Pytest monkeypatch fixture.
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_misc.py"
        test_file.write_text(
            'SAMPLE_IDS = ["R402", "L039"]\ndef test_something_else():\n    pass\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "TESTS_DIR", tests_dir)
        monkeypatch.setattr(_mod, "_TEST_CACHE", None)
        cache = _mod._get_test_cache()
        assert "R402" not in cache


class TestParseFrontmatterYamlFallback:
    """Tests for YAML-error fallback in frontmatter parsing."""

    def test_yaml_error_falls_back_to_regex(self, tmp_path: Path) -> None:
        """Invalid YAML still yields rule_id via regex fallback.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        md = tmp_path / "broken.md"
        md.write_text("---\nrule_id: R402\nstatus: [unclosed\ndescription: ok\n---\n", encoding="utf-8")
        result = _parse_frontmatter(md)
        assert result["rule_id"] == "R402"
