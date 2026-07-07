"""Tests for version_defaults: per-rule ansible-core version applicability (ADR-057)."""

from packaging.specifiers import SpecifierSet

from apme_engine.version_defaults import (
    VERSION_DEFAULTS,
    get_version_spec_str,
    get_version_specifier,
    is_applicable,
)


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
        assert get_version_spec_str("M013") == ">=2.20"
        assert get_version_spec_str("M023") == ">=2.22"
        assert get_version_spec_str("M022") == ">=2.23"
        assert get_version_spec_str("M024") == ">=2.24"
        assert get_version_spec_str("M001") == ">=2.9"
