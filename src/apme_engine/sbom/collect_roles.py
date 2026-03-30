"""Ansible role inventory collector for SBOM generation.

Scans a target directory for Ansible roles in the top-level ``roles/``
subdirectory. Extracts metadata from ``meta/main.yml`` (galaxy_info)
when available, falling back to directory name inference for bare roles.
Roles embedded inside installed collections are NOT scanned.
"""

from __future__ import annotations

import logging
from pathlib import Path

from apme_engine.sbom._yaml_subset import parse_yaml_subset
from apme_engine.sbom.models import (
    APME_PROPERTY_NAMESPACE,
    Component,
    ComponentType,
    LicenseChoice,
    OrganizationalEntity,
    Property,
    mark_name_inferred,
)
from apme_engine.sbom.purl import make_role_purl

logger = logging.getLogger(__name__)

_KNOWN_SPDX_IDS: frozenset[str] = frozenset(
    {
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
    }
)
"""Common SPDX license identifiers recognized for LicenseChoice.license_id."""


def _is_role_dir(d: Path) -> bool:
    """Check whether a directory looks like an Ansible role.

    A directory is a role if it contains ``meta/main.yml`` or
    ``tasks/main.yml``.

    Args:
        d: Directory to check.

    Returns:
        True if the directory appears to be an Ansible role.
    """
    return (d / "meta" / "main.yml").is_file() or (d / "tasks" / "main.yml").is_file()


def _read_role_meta(role_dir: Path) -> dict[str, str]:
    """Read role metadata from meta/main.yml.

    Extracts galaxy_info fields: role_name, version, author, company,
    license, description, min_ansible_version.

    Args:
        role_dir: Path to the role directory.

    Returns:
        Dictionary of extracted metadata fields. Empty dict if
        meta/main.yml is missing or unparseable.
    """
    meta_path = role_dir / "meta" / "main.yml"
    if not meta_path.is_file():
        return {}

    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("Cannot read %s", meta_path, exc_info=True)
        return {}

    parsed = parse_yaml_subset(text)
    galaxy_info = parsed.get("galaxy_info")

    if not isinstance(galaxy_info, dict):
        logger.warning("No galaxy_info mapping in %s", meta_path)
        return {}

    result: dict[str, str] = {}
    for key in (
        "role_name",
        "version",
        "author",
        "company",
        "license",
        "description",
        "min_ansible_version",
    ):
        val = galaxy_info.get(key, "")
        if val:
            result[key] = str(val)

    return result


def _infer_from_dir(role_dir: Path) -> dict[str, str]:
    """Infer role metadata from the directory name.

    Used when meta/main.yml is absent or unparseable.

    Args:
        role_dir: Path to the role directory.

    Returns:
        Dictionary with role_name from dir name and version="unversioned".
    """
    return {
        "role_name": role_dir.name,
        "version": "unversioned",
    }


def collect_roles(target_dir: Path) -> list[Component]:
    """Collect all Ansible roles from a target directory.

    Scans only the top-level ``roles/`` subdirectory under target_dir.
    Roles embedded inside ``ansible_collections/`` are NOT scanned.

    Args:
        target_dir: Root directory of the Ansible project.

    Returns:
        List of Component objects for discovered roles.
    """
    roles_dir = target_dir / "roles"
    if not roles_dir.is_dir():
        return []

    components: list[Component] = []

    for entry in sorted(roles_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not _is_role_dir(entry):
            continue

        meta = _read_role_meta(entry)
        inferred = False

        if not meta:
            meta = _infer_from_dir(entry)
            inferred = True

        role_name = meta.get("role_name", entry.name)
        version = meta.get("version", "unversioned")
        purl = make_role_purl(role_name, version)

        licenses: list[LicenseChoice] = []
        license_str = meta.get("license", "")
        if license_str:
            if license_str in _KNOWN_SPDX_IDS:
                licenses.append(LicenseChoice(license_id=license_str))
            else:
                licenses.append(LicenseChoice(license_name=license_str))

        supplier = OrganizationalEntity()
        company = meta.get("company", "")
        if company:
            supplier = OrganizationalEntity(name=company)

        properties: list[Property] = []
        min_ver = meta.get("min_ansible_version", "")
        if min_ver:
            properties.append(
                Property(
                    name=f"{APME_PROPERTY_NAMESPACE}:min-ansible-version",
                    value=min_ver,
                )
            )

        component = Component(
            type=ComponentType.LIBRARY,
            name=role_name,
            version=version,
            purl=purl,
            bom_ref=purl,
            supplier=supplier,
            author=meta.get("author", "unknown"),
            description=meta.get("description", ""),
            licenses=licenses,
            properties=properties,
        )

        if inferred:
            mark_name_inferred(component)

        components.append(component)

    logger.debug("Collected %d roles from %s", len(components), roles_dir)
    return components
