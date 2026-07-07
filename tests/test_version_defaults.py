"""Tests for version_defaults: per-rule ansible-core version applicability (ADR-057)."""

from __future__ import annotations

import re
from pathlib import Path

from packaging.specifiers import SpecifierSet

from apme_engine.version_defaults import (
    VERSION_DEFAULTS,
    get_version_spec_str,
    get_version_specifier,
    is_applicable,
)

_VALIDATORS_DIR = Path(__file__).resolve().parent.parent / "src" / "apme_engine" / "validators"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_KV_RE = re.compile(r'^(\w+):\s*"?([^"\n]+)"?\s*$', re.MULTILINE)


class TestVersionDefaults:
    """Tests for the VERSION_DEFAULTS table and accessor functions."""

    def test_all_m_rules_have_version(self) -> None:
        """Every M-rule in the table has a valid SpecifierSet.

        Returns:
            None: Assert-only test.
        """
        for rule_id, spec in VERSION_DEFAULTS.items():
            assert rule_id.startswith("M"), f"expected M-prefix, got {rule_id}"
            assert isinstance(spec, SpecifierSet), f"{rule_id} value must be SpecifierSet"
            assert str(spec), f"{rule_id} specifier must not be empty"

    def test_get_version_specifier_known_rule(self) -> None:
        """Known M-rule returns its SpecifierSet.

        Returns:
            None: Assert-only test.
        """
        spec = get_version_specifier("M014")
        assert spec is not None
        assert isinstance(spec, SpecifierSet)
        assert str(spec) == ">=2.24"

    def test_get_version_specifier_unknown_rule(self) -> None:
        """Unknown rule returns None.

        Returns:
            None: Assert-only test.
        """
        assert get_version_specifier("L021") is None
        assert get_version_specifier("NONEXISTENT") is None

    def test_get_version_spec_str_known_rule(self) -> None:
        """Known M-rule returns PEP 440 specifier string.

        Returns:
            None: Assert-only test.
        """
        assert get_version_spec_str("M008") == ">=2.19"
        assert get_version_spec_str("M014") == ">=2.24"
        assert get_version_spec_str("M018") == ">=2.21"

    def test_get_version_spec_str_unknown_rule(self) -> None:
        """Unknown rule returns empty string.

        Returns:
            None: Assert-only test.
        """
        assert get_version_spec_str("L021") == ""
        assert get_version_spec_str("R101") == ""

    def test_is_applicable_matching_version(self) -> None:
        """Rule is applicable when target version satisfies the specifier.

        Returns:
            None: Assert-only test.
        """
        assert is_applicable("M008", "2.19") is True
        assert is_applicable("M008", "2.20") is True
        assert is_applicable("M014", "2.24") is True

    def test_is_applicable_non_matching_version(self) -> None:
        """Rule is not applicable when target version does not satisfy the specifier.

        Returns:
            None: Assert-only test.
        """
        assert is_applicable("M008", "2.18") is False
        assert is_applicable("M014", "2.19") is False
        assert is_applicable("M014", "2.23") is False

    def test_is_applicable_version_agnostic_rule(self) -> None:
        """Rules without version metadata are always applicable.

        Returns:
            None: Assert-only test.
        """
        assert is_applicable("L021", "2.19") is True
        assert is_applicable("R101", "2.20") is True

    def test_version_groups(self) -> None:
        """Verify representative rules from each version group.

        Returns:
            None: Assert-only test.
        """
        assert get_version_spec_str("M005") == ">=2.19"
        assert get_version_spec_str("M010") == ">=2.18"
        assert get_version_spec_str("M023") == ">=2.22"
        assert get_version_spec_str("M022") == ">=2.23"
        assert get_version_spec_str("M024") == ">=2.24"
        assert get_version_spec_str("M001") == ">=2.9"

    def test_is_applicable_empty_version(self) -> None:
        """Empty ansible_core_version returns True (treat as unconstrained).

        Returns:
            None: Assert-only test.
        """
        assert is_applicable("M014", "") is True

    def test_is_applicable_malformed_version(self) -> None:
        """Malformed version string returns True (fail-open).

        Returns:
            None: Assert-only test.
        """
        assert is_applicable("M014", "not-a-version") is True


class TestFrontmatterConsistency:
    """Validate that .md frontmatter and VERSION_DEFAULTS stay in sync."""

    @staticmethod
    def _collect_frontmatter_versions() -> dict[str, str]:
        """Scan all validator .md files for ansible_core_version frontmatter.

        Returns:
            Dict of rule_id -> version specifier string from frontmatter.
        """
        result: dict[str, str] = {}
        for md_path in sorted(_VALIDATORS_DIR.rglob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            fm_match = _FRONTMATTER_RE.match(text)
            if not fm_match:
                continue
            kvs = dict(_KV_RE.findall(fm_match.group(1)))
            rule_id = kvs.get("rule_id", "")
            version = kvs.get("ansible_core_version", "")
            if rule_id and version:
                result[rule_id] = version
        return result

    def test_frontmatter_matches_version_defaults(self) -> None:
        """Every .md ansible_core_version must match VERSION_DEFAULTS.

        Returns:
            None: Assert-only test.
        """
        fm_versions = self._collect_frontmatter_versions()
        assert fm_versions, "should find at least one .md with ansible_core_version"

        mismatches: list[str] = []
        for rule_id, fm_value in sorted(fm_versions.items()):
            table_value = get_version_spec_str(rule_id)
            if not table_value:
                mismatches.append(f"{rule_id}: frontmatter has {fm_value!r} but VERSION_DEFAULTS has no entry")
            elif fm_value != table_value:
                mismatches.append(f"{rule_id}: frontmatter={fm_value!r} != VERSION_DEFAULTS={table_value!r}")

        assert not mismatches, "frontmatter/VERSION_DEFAULTS drift:\n" + "\n".join(mismatches)

    def test_version_defaults_has_frontmatter(self) -> None:
        """Every VERSION_DEFAULTS entry for an implemented rule has frontmatter.

        Returns:
            None: Assert-only test.
        """
        fm_versions = self._collect_frontmatter_versions()
        implemented_rules = set(fm_versions.keys())

        missing: list[str] = []
        for rule_id in sorted(VERSION_DEFAULTS):
            if rule_id not in implemented_rules:
                missing.append(f"{rule_id}: in VERSION_DEFAULTS but no .md frontmatter found")

        assert not missing, "VERSION_DEFAULTS entries without .md frontmatter:\n" + "\n".join(missing)
