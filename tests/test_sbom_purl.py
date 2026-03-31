"""Unit tests for PURL generation in the SBOM module."""

from __future__ import annotations

from apme_gateway.api.sbom import _make_collection_purl, _make_pypi_purl, _normalize_pypi_name


class TestNormalizePypiName:
    """Tests for PEP 503 PyPI name normalization."""

    def test_dots(self) -> None:
        """Dots are replaced with hyphens."""
        assert _normalize_pypi_name("ruamel.yaml") == "ruamel-yaml"

    def test_underscores(self) -> None:
        """Underscores are replaced with hyphens."""
        assert _normalize_pypi_name("my_package") == "my-package"

    def test_mixed(self) -> None:
        """Mixed separators and case are normalized."""
        assert _normalize_pypi_name("My_Package.Name") == "my-package-name"

    def test_consecutive(self) -> None:
        """Consecutive separators collapse to single hyphen."""
        assert _normalize_pypi_name("a..b__c") == "a-b-c"

    def test_already_normal(self) -> None:
        """Already normalized names pass through unchanged."""
        assert _normalize_pypi_name("requests") == "requests"


class TestMakePypiPurl:
    """Tests for PyPI PURL generation."""

    def test_basic(self) -> None:
        """PyPI PURL uses normalized name."""
        assert _make_pypi_purl("ruamel.yaml", "0.18.0") == "pkg:pypi/ruamel-yaml@0.18.0"

    def test_uppercase(self) -> None:
        """PyPI PURL lowercases package name."""
        assert _make_pypi_purl("PyYAML", "6.0") == "pkg:pypi/pyyaml@6.0"

    def test_special_version(self) -> None:
        """Special characters in version are percent-encoded."""
        result = _make_pypi_purl("test", "1.0+local")
        assert "1.0%2Blocal" in result


class TestMakeCollectionPurl:
    """Tests for Ansible collection PURL generation."""

    def test_basic(self) -> None:
        """Collection PURL uses dot-joined fqcn with repository_url."""
        result = _make_collection_purl("cisco.ios", "2.0.0")
        assert result == "pkg:generic/cisco.ios@2.0.0?repository_url=https://galaxy.ansible.com"

    def test_dot_notation(self) -> None:
        """Collection PURL preserves dot notation in fqcn."""
        result = _make_collection_purl("ansible.builtin", "1.0.0")
        assert "ansible.builtin" in result
