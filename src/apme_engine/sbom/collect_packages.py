"""Python package inventory collector for SBOM generation.

Enumerates installed Python packages from a virtual environment's
site-packages directory, extracting name, version, license, and author
metadata from dist-info METADATA files. Infrastructure packages (pip,
setuptools, etc.) and Ansible collection wrapper packages are filtered out.
"""

from __future__ import annotations

import email.parser
import logging
from pathlib import Path

from apme_engine.sbom.models import (
    Component,
    ComponentType,
    LicenseChoice,
)
from apme_engine.sbom.purl import make_pypi_purl, normalize_pypi_name
from apme_engine.venv_manager.session import _venv_site_packages

logger = logging.getLogger(__name__)

_INFRASTRUCTURE_PACKAGES: frozenset[str] = frozenset({
    "pip",
    "setuptools",
    "wheel",
    "pkg-resources",
    "-distutils-hack",  # _distutils_hack normalized
    "distlib",
    "filelock",
    "platformdirs",
})
"""Package names (PEP 503 normalized) that are venv infrastructure, not user deps."""

_COLLECTION_WRAPPER_PREFIX: str = "ansible-collection-"
"""Prefix for PyPI packages that are thin wrappers around Ansible collections."""

_KNOWN_SPDX_IDS: frozenset[str] = frozenset({
    "MIT",
    "Apache-2.0",
    "GPL-2.0-only",
    "GPL-3.0-only",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MPL-2.0",
    "LGPL-2.1-only",
    "LGPL-3.0-only",
    "Unlicense",
    "PSF-2.0",
})
"""Common SPDX license identifiers recognized for LicenseChoice.license_id."""


def _is_excluded_package(name: str) -> bool:
    """Check whether a package should be excluded from inventory.

    Excludes venv infrastructure packages and Ansible collection wrapper
    packages (``ansible-collection-*``).

    Args:
        name: Raw package name from dist-info.

    Returns:
        True if the package should be filtered out.
    """
    normalized = normalize_pypi_name(name)
    if normalized in _INFRASTRUCTURE_PACKAGES:
        return True
    if normalized.startswith(_COLLECTION_WRAPPER_PREFIX):
        return True
    return False


def _make_license(license_str: str) -> LicenseChoice:
    """Convert a license string to a LicenseChoice.

    If the string matches a known SPDX identifier, it is stored as
    ``license_id``; otherwise as ``license_name`` (free-text).

    Args:
        license_str: License string from package METADATA.

    Returns:
        A populated LicenseChoice.
    """
    if not license_str:
        return LicenseChoice()
    if license_str in _KNOWN_SPDX_IDS:
        return LicenseChoice(license_id=license_str)
    return LicenseChoice(license_name=license_str)


def _read_dist_info_metadata(dist_info_dir: Path) -> dict[str, str]:
    """Read and parse a dist-info METADATA file.

    Uses :class:`email.parser.HeaderParser` per PEP 566 to extract
    package metadata fields.

    Args:
        dist_info_dir: Path to the ``*.dist-info`` directory.

    Returns:
        Dictionary with keys: name, version, summary, author, license.
        Returns empty dict if METADATA cannot be read.
    """
    metadata_path = dist_info_dir / "METADATA"
    if not metadata_path.is_file():
        logger.warning("No METADATA file in %s", dist_info_dir)
        return {}

    try:
        text = metadata_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("Cannot read %s", metadata_path, exc_info=True)
        return {}

    parser = email.parser.HeaderParser()
    msg = parser.parsestr(text)

    result: dict[str, str] = {}

    name = msg.get("Name", "")
    if name:
        result["name"] = name

    version = msg.get("Version", "")
    if version:
        result["version"] = version

    summary = msg.get("Summary", "")
    if summary:
        result["summary"] = summary

    # Author with fallback to Author-email
    author = msg.get("Author", "")
    if not author:
        author = msg.get("Author-email", "")
    if author:
        result["author"] = author

    # License-Expression preferred over License
    license_str = msg.get("License-Expression", "")
    if not license_str:
        license_str = msg.get("License", "")
    if license_str:
        result["license"] = license_str

    return result


def collect_packages(venv_root: Path) -> list[Component]:
    """Collect all user-installed Python packages from a virtual environment.

    Scans the venv's site-packages for ``*.dist-info`` directories, reads
    METADATA files, filters out infrastructure and collection-wrapper
    packages, and returns CycloneDX Component objects.

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        List of Component objects for discovered packages.
    """
    site_packages = _venv_site_packages(venv_root)
    components: list[Component] = []
    filtered_count = 0

    for dist_dir in sorted(site_packages.glob("*.dist-info")):
        if not dist_dir.is_dir():
            continue

        meta = _read_dist_info_metadata(dist_dir)
        if not meta:
            logger.warning("Skipping %s: could not read metadata", dist_dir.name)
            continue

        pkg_name = meta.get("name", "")
        if not pkg_name:
            logger.warning("Skipping %s: no Name in METADATA", dist_dir.name)
            continue

        if _is_excluded_package(pkg_name):
            filtered_count += 1
            continue

        version = meta.get("version", "0.0.0")
        purl = make_pypi_purl(pkg_name, version)

        licenses: list[LicenseChoice] = []
        license_str = meta.get("license", "")
        if license_str:
            licenses.append(_make_license(license_str))

        component = Component(
            type=ComponentType.LIBRARY,
            name=pkg_name,
            version=version,
            purl=purl,
            bom_ref=purl,
            author=meta.get("author", "unknown"),
            description=meta.get("summary", ""),
            licenses=licenses,
        )
        components.append(component)

    logger.debug(
        "Collected %d packages, filtered %d infrastructure/wrapper packages",
        len(components),
        filtered_count,
    )
    return components
