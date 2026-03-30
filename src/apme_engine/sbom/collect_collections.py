"""Ansible collection inventory collector with dependency mapping.

Walks ``ansible_collections/`` in a venv's site-packages to discover
all installed collections with full metadata (MANIFEST.json preferred,
galaxy.yml fallback, directory-inference last resort). Maps collection-
to-collection dependencies from galaxy.yml.

Satisfies INV-01 (collection discovery) and INV-04 (dependency mapping).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from apme_engine.sbom._yaml_subset import parse_yaml_subset
from apme_engine.sbom.models import (
    Component,
    ComponentType,
    Dependency,
    LicenseChoice,
    OrganizationalEntity,
    mark_name_inferred,
)
from apme_engine.sbom.purl import make_collection_purl
from apme_engine.venv_manager.session import _venv_site_packages

logger = logging.getLogger(__name__)

# Well-known SPDX license IDs for fast lookup.
_KNOWN_SPDX: frozenset[str] = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "Unlicense",
        "CC0-1.0",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_manifest_json(collection_dir: Path) -> dict[str, object]:
    """Read MANIFEST.json and return normalised collection_info dict.

    Args:
        collection_dir: Path to the collection directory.

    Returns:
        Dictionary with collection metadata (namespace, name, version, etc.).
    """
    text = (collection_dir / "MANIFEST.json").read_text(encoding="utf-8")
    data = json.loads(text)
    info = data["collection_info"]
    return {
        "namespace": info["namespace"],
        "name": info["name"],
        "version": info.get("version", "unversioned"),
        "authors": info.get("authors", []),
        "description": info.get("description", ""),
        "license": info.get("license", []),
    }


def _read_galaxy_yml(collection_dir: Path) -> dict[str, object]:
    """Read galaxy.yml via the stdlib YAML subset parser.

    Args:
        collection_dir: Path to the collection directory.

    Returns:
        Dictionary with collection metadata (namespace, name, version, etc.).

    Raises:
        FileNotFoundError: If galaxy.yml is missing.
        ValueError: If parse result is empty or missing namespace/name.
    """
    path = collection_dir / "galaxy.yml"
    if not path.is_file():
        raise FileNotFoundError(f"No galaxy.yml in {collection_dir}")
    text = path.read_text(encoding="utf-8")
    parsed = parse_yaml_subset(text)
    if not parsed:
        raise ValueError(f"Empty parse result for {path}")
    ns = parsed.get("namespace", "")
    name = parsed.get("name", "")
    if not ns or not name:
        raise ValueError(f"galaxy.yml missing namespace/name in {collection_dir}")
    # Authors may be a list (from ``- item`` syntax) or absent
    authors_raw = parsed.get("authors", [])
    if isinstance(authors_raw, str):
        authors_raw = [authors_raw] if authors_raw else []
    elif not isinstance(authors_raw, list):
        authors_raw = []
    # License may be a string or list
    license_raw = parsed.get("license", [])
    if isinstance(license_raw, str):
        license_raw = [license_raw] if license_raw else []
    elif not isinstance(license_raw, list):
        license_raw = []
    return {
        "namespace": str(ns),
        "name": str(name),
        "version": str(parsed.get("version", "unversioned")),
        "authors": authors_raw,
        "description": str(parsed.get("description", "")),
        "license": license_raw,
    }


def _infer_from_path(collection_dir: Path) -> dict[str, object]:
    """Infer collection metadata from directory path.

    The directory structure is ``ansible_collections/{namespace}/{name}/``.

    Args:
        collection_dir: Path to the collection directory.

    Returns:
        Dictionary with inferred collection metadata.
    """
    name = collection_dir.name
    namespace = collection_dir.parent.name
    return {
        "namespace": namespace,
        "name": name,
        "version": "unversioned",
        "authors": [],
        "description": "",
        "license": [],
        "_inferred": True,
    }


def _iter_collection_dirs(site_packages: Path) -> list[Path]:
    """Walk ``ansible_collections/{namespace}/{name}/`` directories.

    Skips namespace dirs starting with ``_``.

    Args:
        site_packages: Path to the site-packages directory.

    Returns:
        List of collection directory paths.
    """
    ac_root = site_packages / "ansible_collections"
    if not ac_root.is_dir():
        return []

    dirs: list[Path] = []
    for ns_dir in sorted(ac_root.iterdir()):
        if not ns_dir.is_dir() or ns_dir.name.startswith("_"):
            continue
        for col_dir in sorted(ns_dir.iterdir()):
            if not col_dir.is_dir():
                continue
            dirs.append(col_dir)
    return dirs


def _extract_license(metadata: dict[str, object]) -> list[LicenseChoice]:
    """Convert license field to LicenseChoice objects.

    Well-known SPDX IDs get ``license_id``; everything else gets
    ``license_name``.

    Args:
        metadata: Collection metadata dictionary.

    Returns:
        List of LicenseChoice objects.
    """
    raw = metadata.get("license", [])
    if isinstance(raw, str):
        raw_list: list[object] = [raw] if raw else []
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raw_list = []
    choices: list[LicenseChoice] = []
    for entry in raw_list:
        entry = str(entry).strip()
        if not entry:
            continue
        if entry in _KNOWN_SPDX:
            choices.append(LicenseChoice(license_id=entry))
        else:
            choices.append(LicenseChoice(license_name=entry))
    return choices


def _read_galaxy_deps(collection_dir: Path) -> dict[str, str]:
    """Read dependency map from galaxy.yml.

    Args:
        collection_dir: Path to the collection directory.

    Returns:
        Mapping of FQCN to version spec, or empty dict on any failure.
    """
    path = collection_dir / "galaxy.yml"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        parsed = parse_yaml_subset(text)
        deps = parsed.get("dependencies", {})
        if isinstance(deps, dict):
            return {str(k): str(v) for k, v in deps.items()}
    except Exception:
        logger.debug("Failed to read dependencies from %s", path, exc_info=True)
    return {}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def collect_collections(
    venv_root: Path,
) -> tuple[list[Component], list[Dependency]]:
    """Discover all Ansible collections in a venv and map dependencies.

    Walks ``ansible_collections/`` under site-packages. For each collection
    directory, tries metadata sources in order:
    1. MANIFEST.json (authoritative)
    2. galaxy.yml (fallback)
    3. Directory name inference (last resort)

    Dependencies are read from galaxy.yml and resolved against the set
    of discovered collections. Unresolved dependencies are logged as
    warnings but not included in output.

    Args:
        venv_root: Root path of the virtual environment.

    Returns:
        Tuple of (components, dependencies).
    """
    site = _venv_site_packages(venv_root)
    col_dirs = _iter_collection_dirs(site)

    if not col_dirs:
        return [], []

    components: list[Component] = []
    # Map fqcn -> (purl, collection_dir) for dependency resolution
    purl_map: dict[str, str] = {}
    dir_map: dict[str, Path] = {}

    for col_dir in col_dirs:
        metadata: dict[str, object] | None = None
        inferred = False

        # Try MANIFEST.json first
        try:
            metadata = _read_manifest_json(col_dir)
        except Exception:
            logger.debug("MANIFEST.json failed for %s, trying galaxy.yml", col_dir)

        # Fall back to galaxy.yml
        if metadata is None:
            try:
                metadata = _read_galaxy_yml(col_dir)
            except Exception:
                logger.debug("galaxy.yml failed for %s, inferring from path", col_dir)

        # Last resort: directory inference
        if metadata is None:
            metadata = _infer_from_path(col_dir)
            inferred = True

        ns = str(metadata["namespace"])
        name = str(metadata["name"])
        version = str(metadata["version"])
        fqcn = f"{ns}.{name}"

        purl = make_collection_purl(ns, name, version)

        # Authors
        authors = metadata.get("authors", [])
        if isinstance(authors, list) and authors:
            author_str = ", ".join(str(a) for a in authors)
            supplier_name = str(authors[0])
        else:
            author_str = "unknown"
            supplier_name = "unknown"

        component = Component(
            type=ComponentType.LIBRARY,
            name=fqcn,
            version=version,
            purl=purl,
            bom_ref=purl,
            supplier=OrganizationalEntity(name=supplier_name),
            author=author_str,
            description=str(metadata.get("description", "")),
            licenses=_extract_license(metadata),
        )

        if inferred or metadata.get("_inferred"):
            mark_name_inferred(component)

        components.append(component)
        purl_map[fqcn] = purl
        dir_map[fqcn] = col_dir

    # Build dependency graph
    dependencies: list[Dependency] = []
    for fqcn, col_dir in dir_map.items():
        declared_deps = _read_galaxy_deps(col_dir)
        if not declared_deps:
            continue

        resolved_purls: list[str] = []
        for dep_fqcn in declared_deps:
            if dep_fqcn in purl_map:
                resolved_purls.append(purl_map[dep_fqcn])
            else:
                logger.warning(
                    "Unresolved dependency: %s requires %s (not installed)",
                    fqcn,
                    dep_fqcn,
                )

        if resolved_purls:
            dependencies.append(Dependency(ref=purl_map[fqcn], depends_on=resolved_purls))

    return components, dependencies
