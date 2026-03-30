"""Unit tests for PURL generation functions."""

from __future__ import annotations

from apme_engine.sbom.purl import (
    make_collection_purl,
    make_pypi_purl,
    make_role_purl,
    normalize_pypi_name,
)


class TestNormalizePypiName:
    """Tests for PEP 503 PyPI name normalization."""

    def test_normalize_pypi_name_dots(self) -> None:
        """Dots are replaced with hyphens."""
        assert normalize_pypi_name("ruamel.yaml") == "ruamel-yaml"

    def test_normalize_pypi_name_underscores(self) -> None:
        """Underscores are replaced with hyphens."""
        assert normalize_pypi_name("my_package") == "my-package"

    def test_normalize_pypi_name_mixed(self) -> None:
        """Mixed separators and case are normalized."""
        assert normalize_pypi_name("My_Package.Name") == "my-package-name"

    def test_normalize_pypi_name_consecutive(self) -> None:
        """Consecutive separators collapse to single hyphen."""
        assert normalize_pypi_name("a..b__c") == "a-b-c"

    def test_normalize_pypi_name_already_normal(self) -> None:
        """Already normalized names pass through unchanged."""
        assert normalize_pypi_name("requests") == "requests"


class TestMakePypiPurl:
    """Tests for PyPI PURL generation."""

    def test_make_pypi_purl(self) -> None:
        """PyPI PURL uses normalized name."""
        assert make_pypi_purl("ruamel.yaml", "0.18.0") == "pkg:pypi/ruamel-yaml@0.18.0"

    def test_make_pypi_purl_uppercase(self) -> None:
        """PyPI PURL lowercases package name."""
        assert make_pypi_purl("PyYAML", "6.0") == "pkg:pypi/pyyaml@6.0"


class TestMakeCollectionPurl:
    """Tests for Ansible collection PURL generation."""

    def test_make_collection_purl(self) -> None:
        """Collection PURL uses dot-joined namespace.name with repository_url."""
        result = make_collection_purl("cisco", "ios", "2.0.0")
        assert result == "pkg:generic/cisco.ios@2.0.0?repository_url=https://galaxy.ansible.com"

    def test_make_collection_purl_dot_joined(self) -> None:
        """Collection PURL uses dot not slash between namespace and name."""
        result = make_collection_purl("ansible", "builtin", "1.0.0")
        assert "ansible.builtin" in result
        assert "ansible/builtin" not in result


class TestMakeRolePurl:
    """Tests for Ansible role PURL generation."""

    def test_make_role_purl(self) -> None:
        """Role PURL uses pkg:generic with repository_url."""
        result = make_role_purl("my_role", "1.0.0")
        assert result == "pkg:generic/my_role@1.0.0?repository_url=https://galaxy.ansible.com"

    def test_make_role_purl_unversioned(self) -> None:
        """Unversioned role produces valid PURL with @unversioned."""
        result = make_role_purl("bare_role", "unversioned")
        assert result == "pkg:generic/bare_role@unversioned?repository_url=https://galaxy.ansible.com"


class TestPurlSpecialCharacters:
    """Tests for PURL special character handling."""

    def test_purl_special_characters(self) -> None:
        """Special characters in name are percent-encoded."""
        result = make_pypi_purl("my package", "1.0")
        # Space should be percent-encoded in the PURL
        assert "%20" in result or "my-package" in result
        # After normalization, spaces become hyphens via PEP 503
        # but the version should be encoded if it has specials
        result_with_special_version = make_pypi_purl("test", "1.0+local")
        assert "1.0%2Blocal" in result_with_special_version
