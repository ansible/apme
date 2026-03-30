"""Tests for Ansible collection inventory collector with dependency mapping.

Covers collection discovery from venv site-packages, metadata extraction
from MANIFEST.json / galaxy.yml / directory inference, license extraction,
and dependency relationship mapping.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from apme_engine.sbom.collect_collections import collect_collections
from apme_engine.sbom.models import ComponentType

# ---------------------------------------------------------------------------
# Helpers to build mock venv structures
# ---------------------------------------------------------------------------


def _make_venv_skeleton(tmp_path: Path) -> Path:
    """Create a minimal venv skeleton with site-packages dir.

    Args:
        tmp_path: Temporary directory path for test fixtures.

    Returns:
        Path to the venv root directory.
    """
    venv_root = tmp_path / "venv"
    site = venv_root / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    return venv_root


def _site_packages(venv_root: Path) -> Path:
    """Get the site-packages directory path from a venv root.

    Args:
        venv_root: Path to the venv root directory.

    Returns:
        Path to the site-packages directory.
    """
    return venv_root / "lib" / "python3.12" / "site-packages"


def _add_collection_with_manifest(
    venv_root: Path,
    namespace: str,
    name: str,
    version: str = "2.0.0",
    *,
    authors: list[str] | None = None,
    description: str = "A test collection",
    license_list: list[str] | None = None,
    deps: dict[str, str] | None = None,
) -> Path:
    """Add a collection dir with MANIFEST.json (and optionally galaxy.yml for deps).

    Args:
        venv_root: Path to the venv root directory.
        namespace: Collection namespace.
        name: Collection name.
        version: Collection version.
        authors: List of author names.
        description: Collection description.
        license_list: List of license identifiers.
        deps: Dictionary of dependency names to version specs.

    Returns:
        Path to the created collection directory.
    """
    site = _site_packages(venv_root)
    col_dir = site / "ansible_collections" / namespace / name
    col_dir.mkdir(parents=True)

    manifest = {
        "collection_info": {
            "namespace": namespace,
            "name": name,
            "version": version,
            "authors": authors or ["Test Author"],
            "description": description,
            "license": license_list or ["GPL-3.0-only"],
        }
    }
    (col_dir / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    if deps:
        lines = ["---", f"namespace: {namespace}", f"name: {name}", f"version: {version}", "dependencies:"]
        for dep_name, dep_ver in deps.items():
            lines.append(f"  {dep_name}: '{dep_ver}'")
        (col_dir / "galaxy.yml").write_text("\n".join(lines), encoding="utf-8")

    return col_dir


def _add_collection_with_galaxy_only(
    venv_root: Path,
    namespace: str,
    name: str,
    version: str = "1.0.0",
    *,
    description: str = "Galaxy-only collection",
    authors: list[str] | None = None,
    license_str: str = "MIT",
) -> Path:
    """Add a collection dir with galaxy.yml but no MANIFEST.json.

    Args:
        venv_root: Path to the venv root directory.
        namespace: Collection namespace.
        name: Collection name.
        version: Collection version.
        description: Collection description.
        authors: List of author names.
        license_str: License identifier string.

    Returns:
        Path to the created collection directory.
    """
    site = _site_packages(venv_root)
    col_dir = site / "ansible_collections" / namespace / name
    col_dir.mkdir(parents=True)

    lines = [
        "---",
        f"namespace: {namespace}",
        f"name: {name}",
        f"version: {version}",
        f"description: {description}",
        f"license: {license_str}",
    ]
    if authors:
        lines.append("authors:")
        for a in authors:
            lines.append(f"  - {a}")
    (col_dir / "galaxy.yml").write_text("\n".join(lines), encoding="utf-8")
    return col_dir


def _add_collection_bare(venv_root: Path, namespace: str, name: str) -> Path:
    """Add a collection dir with no metadata files at all.

    Args:
        venv_root: Path to the venv root directory.
        namespace: Collection namespace.
        name: Collection name.

    Returns:
        Path to the created collection directory.
    """
    site = _site_packages(venv_root)
    col_dir = site / "ansible_collections" / namespace / name
    col_dir.mkdir(parents=True)
    return col_dir


# ---------------------------------------------------------------------------
# Tests: basic discovery
# ---------------------------------------------------------------------------


class TestEmptyVenv:
    """Empty venv returns empty lists."""

    def test_no_ansible_collections_dir(self, tmp_path: Path) -> None:
        """Verify empty lists returned when ansible_collections dir doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        components, deps = collect_collections(venv_root)
        assert components == []
        assert deps == []

    def test_empty_ansible_collections_dir(self, tmp_path: Path) -> None:
        """Verify empty lists returned when ansible_collections dir is empty.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        (_site_packages(venv_root) / "ansible_collections").mkdir()
        components, deps = collect_collections(venv_root)
        assert components == []
        assert deps == []


class TestManifestJsonDiscovery:
    """Collections discovered via MANIFEST.json."""

    def test_single_collection(self, tmp_path: Path) -> None:
        """Verify single collection discovered from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0")
        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        c = components[0]
        assert c.name == "cisco.ios"
        assert c.version == "2.0.0"
        assert c.type == ComponentType.LIBRARY
        assert "cisco.ios" in c.purl
        assert c.bom_ref == c.purl

    def test_multiple_collections(self, tmp_path: Path) -> None:
        """Verify multiple collections are all discovered.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0")
        _add_collection_with_manifest(venv_root, "ansible", "netcommon", "3.0.0")
        components, _ = collect_collections(venv_root)
        assert len(components) == 2
        names = {c.name for c in components}
        assert names == {"cisco.ios", "ansible.netcommon"}

    def test_author_populated(self, tmp_path: Path) -> None:
        """Verify authors field is populated from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0", authors=["Alice", "Bob"])
        components, _ = collect_collections(venv_root)
        assert components[0].author == "Alice, Bob"

    def test_supplier_from_first_author(self, tmp_path: Path) -> None:
        """Verify supplier is set to first author name.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0", authors=["Alice", "Bob"])
        components, _ = collect_collections(venv_root)
        assert components[0].supplier.name == "Alice"

    def test_description_populated(self, tmp_path: Path) -> None:
        """Verify description field is populated from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0", description="Cisco IOS modules")
        components, _ = collect_collections(venv_root)
        assert components[0].description == "Cisco IOS modules"

    def test_license_populated(self, tmp_path: Path) -> None:
        """Verify license list is populated from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0", license_list=["Apache-2.0", "MIT"])
        components, _ = collect_collections(venv_root)
        licenses = components[0].licenses
        assert len(licenses) == 2
        assert any(lc.license_id == "Apache-2.0" for lc in licenses)
        assert any(lc.license_id == "MIT" for lc in licenses)


class TestManifestPreference:
    """MANIFEST.json is preferred over galaxy.yml when both exist."""

    def test_manifest_wins_over_galaxy(self, tmp_path: Path) -> None:
        """Verify MANIFEST.json takes precedence over galaxy.yml when both exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        col_dir = _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0", description="From MANIFEST")
        # Also add galaxy.yml with different version
        galaxy = "---\nnamespace: cisco\nname: ios\nversion: 1.0.0\ndescription: From galaxy"
        (col_dir / "galaxy.yml").write_text(galaxy, encoding="utf-8")

        components, _ = collect_collections(venv_root)
        assert components[0].version == "2.0.0"
        assert components[0].description == "From MANIFEST"


class TestGalaxyYmlDiscovery:
    """Collections discovered via galaxy.yml when MANIFEST.json absent."""

    def test_galaxy_yml_fallback(self, tmp_path: Path) -> None:
        """Verify galaxy.yml is used when MANIFEST.json is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_galaxy_only(venv_root, "community", "general", "5.0.0")
        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        assert components[0].name == "community.general"
        assert components[0].version == "5.0.0"


class TestDirectoryInference:
    """Collections with no metadata files get name/version inferred."""

    def test_bare_directory_inferred(self, tmp_path: Path) -> None:
        """Verify bare directory without metadata gets name and version inferred.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_bare(venv_root, "custom", "mymod")
        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        c = components[0]
        assert c.name == "custom.mymod"
        assert c.version == "unversioned"

    def test_bare_directory_marked_inferred(self, tmp_path: Path) -> None:
        """Verify inferred collections have apme:name-source property.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_bare(venv_root, "custom", "mymod")
        components, _ = collect_collections(venv_root)
        props = {p.name: p.value for p in components[0].properties}
        assert props.get("apme:name-source") == "inferred-from-directory"


class TestSkipUnderscoreDirs:
    """Namespace dirs starting with _ are skipped."""

    def test_underscore_namespace_skipped(self, tmp_path: Path) -> None:
        """Verify namespaces starting with underscore are skipped.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "_internal", "utils", "1.0.0")
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0")
        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        assert components[0].name == "cisco.ios"


class TestMalformedMetadata:
    """Malformed metadata falls through gracefully."""

    def test_malformed_manifest_falls_to_galaxy(self, tmp_path: Path) -> None:
        """Verify malformed MANIFEST.json falls back to galaxy.yml.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        site = _site_packages(venv_root)
        col_dir = site / "ansible_collections" / "broken" / "coll"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text("NOT JSON", encoding="utf-8")
        galaxy = "---\nnamespace: broken\nname: coll\nversion: 1.0.0"
        (col_dir / "galaxy.yml").write_text(galaxy, encoding="utf-8")

        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        assert components[0].name == "broken.coll"
        assert components[0].version == "1.0.0"

    def test_malformed_both_falls_to_inference(self, tmp_path: Path) -> None:
        """Verify malformed metadata files fall back to directory inference.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        site = _site_packages(venv_root)
        col_dir = site / "ansible_collections" / "broken" / "coll"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text("NOT JSON", encoding="utf-8")
        (col_dir / "galaxy.yml").write_text("", encoding="utf-8")

        components, _ = collect_collections(venv_root)
        assert len(components) == 1
        assert components[0].name == "broken.coll"
        assert components[0].version == "unversioned"


# ---------------------------------------------------------------------------
# Tests: dependency mapping
# ---------------------------------------------------------------------------


class TestDependencyMapping:
    """Collection-to-collection dependencies from galaxy.yml."""

    def test_resolved_dependency(self, tmp_path: Path) -> None:
        """Verify resolved dependencies are included in dependency list.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "ansible", "netcommon", "3.0.0")
        _add_collection_with_manifest(
            venv_root,
            "cisco",
            "ios",
            "2.0.0",
            deps={"ansible.netcommon": ">=2.0.0"},
        )
        _, deps = collect_collections(venv_root)
        assert len(deps) >= 1
        cisco_dep = [d for d in deps if "cisco.ios" in d.ref]
        assert len(cisco_dep) == 1
        assert any("ansible.netcommon" in purl for purl in cisco_dep[0].depends_on)

    def test_unresolved_dependency_not_in_output(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Verify unresolved dependencies are excluded and warning is logged.

        Args:
            tmp_path: Pytest temporary directory fixture.
            caplog: Pytest log capture fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(
            venv_root,
            "cisco",
            "ios",
            "2.0.0",
            deps={"ansible.netcommon": ">=2.0.0"},
        )
        with caplog.at_level(logging.WARNING):
            _, deps = collect_collections(venv_root)
        # No dependency objects for unresolved
        cisco_dep = [d for d in deps if "cisco.ios" in d.ref]
        # Either no dep entry or depends_on is empty
        for d in cisco_dep:
            assert "ansible.netcommon" not in " ".join(d.depends_on)
        # Warning was logged
        assert any("ansible.netcommon" in r.message for r in caplog.records)

    def test_no_deps_no_dependency_objects(self, tmp_path: Path) -> None:
        """Verify collections without dependencies produce empty dependency list.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "cisco", "ios", "2.0.0")
        _, deps = collect_collections(venv_root)
        assert deps == []


# ---------------------------------------------------------------------------
# Tests: license extraction
# ---------------------------------------------------------------------------


class TestLicenseExtraction:
    """License metadata extraction from MANIFEST.json."""

    def test_well_known_spdx_id(self, tmp_path: Path) -> None:
        """Verify well-known SPDX license IDs are extracted correctly.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "test", "coll", license_list=["MIT"])
        components, _ = collect_collections(venv_root)
        lc = components[0].licenses[0]
        assert lc.license_id == "MIT"

    def test_unknown_license_uses_name(self, tmp_path: Path) -> None:
        """Verify unknown licenses are stored as free-text names.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        venv_root = _make_venv_skeleton(tmp_path)
        _add_collection_with_manifest(venv_root, "test", "coll", license_list=["Custom-License-v2"])
        components, _ = collect_collections(venv_root)
        lc = components[0].licenses[0]
        assert lc.license_name == "Custom-License-v2"
        assert lc.license_id == ""
