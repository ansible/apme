"""PURL generation functions for Ansible content and Python packages.

Generates Package URL (PURL) identifiers for collections, roles, and PyPI
packages following the PURL specification. Uses PEP 503 normalization for
Python package names.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

logger = logging.getLogger(__name__)

_GALAXY_URL: str = "https://galaxy.ansible.com"
"""Default Galaxy repository URL used as PURL qualifier."""


def normalize_pypi_name(name: str) -> str:
    """Normalize a Python package name per PEP 503.

    Replaces runs of dots, underscores, and hyphens with a single hyphen,
    then lowercases the result.

    Args:
        name: Raw Python package name.

    Returns:
        PEP 503 normalized package name.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def make_pypi_purl(name: str, version: str) -> str:
    """Generate a PURL for a Python (PyPI) package.

    Args:
        name: Python package name (will be PEP 503 normalized).
        version: Package version string.

    Returns:
        PURL string in format pkg:pypi/{normalized_name}@{version}.
    """
    normalized = normalize_pypi_name(name)
    safe_name = quote(normalized, safe="")
    safe_version = quote(version, safe="")
    return f"pkg:pypi/{safe_name}@{safe_version}"


def make_collection_purl(namespace: str, name: str, version: str) -> str:
    """Generate a PURL for an Ansible Galaxy collection.

    Uses dot-joined namespace.name format with generic PURL type
    and Galaxy repository URL qualifier.

    Args:
        namespace: Collection namespace (e.g., cisco).
        name: Collection name (e.g., ios).
        version: Collection version string.

    Returns:
        PURL string in format pkg:generic/{namespace}.{name}@{version}?repository_url=...
    """
    safe_ns = quote(namespace, safe="")
    safe_name = quote(name, safe="")
    safe_version = quote(version, safe="")
    return f"pkg:generic/{safe_ns}.{safe_name}@{safe_version}?repository_url={_GALAXY_URL}"


def make_role_purl(name: str, version: str) -> str:
    """Generate a PURL for an Ansible Galaxy role.

    Args:
        name: Role name.
        version: Role version string (use 'unversioned' for unknown versions).

    Returns:
        PURL string in format pkg:generic/{name}@{version}?repository_url=...
    """
    safe_name = quote(name, safe="")
    safe_version = quote(version, safe="")
    return f"pkg:generic/{safe_name}@{safe_version}?repository_url={_GALAXY_URL}"
