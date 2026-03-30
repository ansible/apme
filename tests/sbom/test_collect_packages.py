"""Tests for Python package inventory collector."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from apme_engine.sbom.collect_packages import (
    _is_excluded_package,
    _make_license,
    _read_dist_info_metadata,
    collect_packages,
)
from apme_engine.sbom.models import ComponentType

# ---------------------------------------------------------------------------
# _is_excluded_package
# ---------------------------------------------------------------------------


class TestIsExcludedPackage:
    """Tests for infrastructure and wrapper package filtering."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "name",
        ["pip", "setuptools", "wheel", "pkg_resources", "_distutils_hack", "distlib", "filelock", "platformdirs"],
    )
    def test_infrastructure_packages_excluded(self, name: str) -> None:
        """Verify known infrastructure packages are excluded.

        Args:
            name: Package name to test.
        """
        assert _is_excluded_package(name) is True

    def test_infrastructure_case_insensitive(self) -> None:
        """Verify PEP 503 normalization handles case differences."""
        assert _is_excluded_package("Pip") is True

    def test_ansible_collection_wrapper_excluded(self) -> None:
        """Verify ansible-collection wrapper packages are excluded."""
        assert _is_excluded_package("ansible-collection-cisco-ios") is True

    def test_ansible_collection_wrapper_normalized(self) -> None:
        """Verify underscore-normalized collection wrappers are excluded."""
        assert _is_excluded_package("ansible_collection_cisco_ios") is True

    def test_normal_package_not_excluded(self) -> None:
        """Verify normal user packages are not excluded."""
        assert _is_excluded_package("requests") is False

    def test_ansible_core_not_excluded(self) -> None:
        """Verify ansible-core is not excluded."""
        assert _is_excluded_package("ansible-core") is False


# ---------------------------------------------------------------------------
# _make_license
# ---------------------------------------------------------------------------


class TestMakeLicense:
    """Tests for license string to LicenseChoice conversion."""

    def test_known_spdx_id(self) -> None:
        """Verify known SPDX ID is stored as license_id."""
        lc = _make_license("MIT")
        assert lc.license_id == "MIT"
        assert lc.license_name == ""

    def test_apache_spdx_id(self) -> None:
        """Verify Apache-2.0 is recognized as SPDX ID."""
        lc = _make_license("Apache-2.0")
        assert lc.license_id == "Apache-2.0"

    def test_unknown_license_as_name(self) -> None:
        """Verify unknown license string is stored as license_name."""
        lc = _make_license("My Custom License")
        assert lc.license_id == ""
        assert lc.license_name == "My Custom License"

    def test_empty_string(self) -> None:
        """Verify empty string produces empty LicenseChoice."""
        lc = _make_license("")
        assert lc.license_id == ""
        assert lc.license_name == ""


# ---------------------------------------------------------------------------
# _read_dist_info_metadata
# ---------------------------------------------------------------------------


class TestReadDistInfoMetadata:
    """Tests for reading dist-info METADATA files."""

    def test_full_metadata(self, tmp_path: Path) -> None:
        """Verify all METADATA fields are extracted correctly.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        dist = tmp_path / "requests-2.31.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            textwrap.dedent("""\
            Metadata-Version: 2.1
            Name: requests
            Version: 2.31.0
            Summary: HTTP for Humans
            Author: Kenneth Reitz
            License-Expression: Apache-2.0
        """)
        )
        meta = _read_dist_info_metadata(dist)
        assert meta["name"] == "requests"
        assert meta["version"] == "2.31.0"
        assert meta["summary"] == "HTTP for Humans"
        assert meta["author"] == "Kenneth Reitz"
        assert meta["license"] == "Apache-2.0"

    def test_license_fallback(self, tmp_path: Path) -> None:
        """Verify License header is used when License-Expression is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        dist = tmp_path / "foo-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            textwrap.dedent("""\
            Name: foo
            Version: 1.0
            License: BSD-3-Clause
        """)
        )
        meta = _read_dist_info_metadata(dist)
        assert meta["license"] == "BSD-3-Clause"

    def test_author_email_fallback(self, tmp_path: Path) -> None:
        """Verify Author-email is used when Author is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        dist = tmp_path / "bar-0.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            textwrap.dedent("""\
            Name: bar
            Version: 0.1
            Author-email: bar@example.com
        """)
        )
        meta = _read_dist_info_metadata(dist)
        assert meta["author"] == "bar@example.com"

    def test_missing_metadata_file(self, tmp_path: Path) -> None:
        """Verify missing METADATA file returns empty dict.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        dist = tmp_path / "bad-1.0.dist-info"
        dist.mkdir()
        # No METADATA file
        meta = _read_dist_info_metadata(dist)
        assert meta == {}


# ---------------------------------------------------------------------------
# collect_packages (integration)
# ---------------------------------------------------------------------------


def _make_venv(tmp_path: Path, packages: dict[str, str]) -> Path:
    """Create a fake venv with dist-info directories.

    Args:
        tmp_path: Temporary directory root.
        packages: Mapping of dist-info dir name to METADATA content.

    Returns:
        Path to the fake venv root.
    """
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    for dist_name, metadata_text in packages.items():
        dist_dir = site / dist_name
        dist_dir.mkdir()
        (dist_dir / "METADATA").write_text(metadata_text)
    return venv


class TestCollectPackages:
    """Integration tests for collect_packages."""

    def test_single_package(self, tmp_path: Path) -> None:
        """Verify single package is collected with full metadata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "requests-2.31.0.dist-info": textwrap.dedent("""\
                Name: requests
                Version: 2.31.0
                Summary: HTTP for Humans
                Author: Kenneth Reitz
                License-Expression: Apache-2.0
            """),
            },
        )
        components = collect_packages(venv)
        assert len(components) == 1
        c = components[0]
        assert c.name == "requests"
        assert c.version == "2.31.0"
        assert c.type == ComponentType.LIBRARY
        assert "pkg:pypi/requests@2.31.0" in c.purl
        assert c.description == "HTTP for Humans"
        assert c.author == "Kenneth Reitz"
        assert len(c.licenses) == 1
        assert c.licenses[0].license_id == "Apache-2.0"

    def test_infrastructure_filtered(self, tmp_path: Path) -> None:
        """Verify infrastructure packages are filtered from results.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "pip-23.0.dist-info": "Name: pip\nVersion: 23.0\n",
                "setuptools-68.0.dist-info": "Name: setuptools\nVersion: 68.0\n",
                "wheel-0.41.0.dist-info": "Name: wheel\nVersion: 0.41.0\n",
                "requests-2.31.0.dist-info": "Name: requests\nVersion: 2.31.0\n",
            },
        )
        components = collect_packages(venv)
        names = [c.name for c in components]
        assert "requests" in names
        assert "pip" not in names
        assert "setuptools" not in names
        assert "wheel" not in names

    def test_collection_wrapper_filtered(self, tmp_path: Path) -> None:
        """Verify ansible-collection wrapper packages are filtered.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "ansible_collection_cisco_ios-1.0.dist-info": "Name: ansible-collection-cisco-ios\nVersion: 1.0\n",
                "jinja2-3.1.2.dist-info": "Name: Jinja2\nVersion: 3.1.2\n",
            },
        )
        components = collect_packages(venv)
        names = [c.name for c in components]
        assert "jinja2" in names or "Jinja2" in names
        assert not any("ansible-collection" in n for n in names)

    def test_empty_venv(self, tmp_path: Path) -> None:
        """Verify empty venv returns no components.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(tmp_path, {})
        components = collect_packages(venv)
        assert components == []

    def test_malformed_metadata(self, tmp_path: Path) -> None:
        """Verify malformed METADATA does not crash collector.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "broken-1.0.dist-info": "This is not valid metadata at all\nJust random text\n",
            },
        )
        # Should still return component with best-effort data
        components = collect_packages(venv)
        # Either empty (no Name found) or partial -- at least no crash
        assert isinstance(components, list)

    def test_license_expression_preferred_over_license(self, tmp_path: Path) -> None:
        """Verify License-Expression takes precedence over License.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "foo-1.0.dist-info": textwrap.dedent("""\
                Name: foo
                Version: 1.0
                License: Some Old License
                License-Expression: MIT
            """),
            },
        )
        components = collect_packages(venv)
        assert len(components) == 1
        assert components[0].licenses[0].license_id == "MIT"

    def test_non_spdx_license_stored_as_name(self, tmp_path: Path) -> None:
        """Verify non-SPDX license is stored as license_name.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv = _make_venv(
            tmp_path,
            {
                "bar-2.0.dist-info": textwrap.dedent("""\
                Name: bar
                Version: 2.0
                License: Custom Proprietary License
            """),
            },
        )
        components = collect_packages(venv)
        assert components[0].licenses[0].license_name == "Custom Proprietary License"
        assert components[0].licenses[0].license_id == ""
