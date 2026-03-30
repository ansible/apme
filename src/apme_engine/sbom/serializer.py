"""CycloneDX 1.5 JSON serializer.

Converts Bom dataclass tree into CycloneDX 1.5 compliant JSON dicts
using an explicit dict builder approach with recursive null/empty stripping.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from apme_engine.sbom.models import (
    Bom,
    BomMetadata,
    Component,
    Dependency,
    LicenseChoice,
    OrganizationalEntity,
    Property,
)

logger = logging.getLogger(__name__)


def bom_to_dict(bom: Bom, include_empty: bool = False) -> dict[str, Any]:
    """Convert a Bom dataclass to a CycloneDX 1.5 JSON-compatible dict.

    Args:
        bom: The BOM dataclass to serialize.
        include_empty: If True, include properties with empty values.

    Returns:
        A dict ready for JSON serialization in CycloneDX 1.5 format.
    """
    result: dict[str, Any] = {
        "bomFormat": bom.bom_format,
        "specVersion": bom.spec_version,
        "serialNumber": bom.serial_number,
        "version": bom.version,
        "metadata": _metadata_to_dict(bom.metadata),
    }

    if bom.components:
        components = []
        for comp in bom.components:
            comp_dict = _component_to_dict(comp, include_empty=include_empty)
            comp_dict = _strip_empty(comp_dict, preserve_empty_strings=include_empty)
            if comp_dict:
                components.append(comp_dict)
        if components:
            result["components"] = components

    # Dependencies are handled separately to preserve empty dependsOn lists
    if bom.dependencies:
        result["dependencies"] = [_dependency_to_dict(dep) for dep in bom.dependencies]

    # Strip empty values from everything except dependencies and components
    # (components are already stripped individually above with correct flags)
    deps_backup = result.pop("dependencies", None)
    comps_backup = result.pop("components", None)
    result = _strip_empty(result) or {}
    if comps_backup is not None:
        result["components"] = comps_backup
    if deps_backup is not None:
        result["dependencies"] = deps_backup

    return result


def bom_to_json(bom: Bom, indent: int = 2, include_empty: bool = False) -> str:
    """Convert a Bom dataclass to a CycloneDX 1.5 JSON string.

    Args:
        bom: The BOM dataclass to serialize.
        indent: JSON indentation level.
        include_empty: If True, include properties with empty values.

    Returns:
        A JSON string in CycloneDX 1.5 format.
    """
    d = bom_to_dict(bom, include_empty=include_empty)
    return json.dumps(d, indent=indent, sort_keys=False)


def _strip_empty(obj: Any, preserve_empty_strings: bool = False) -> Any:
    """Recursively remove None, empty strings, and empty lists from dicts.

    Returns None if a dict becomes empty after stripping. Does not strip
    0 or False values. Does not strip None items from lists (only filters
    empty dicts from lists).

    Args:
        obj: The object to strip.
        preserve_empty_strings: If True, keep empty string values.

    Returns:
        The stripped object, or None if it became empty.
    """
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if v is None:
                continue
            if not preserve_empty_strings and isinstance(v, str) and v == "":
                continue
            if isinstance(v, list) and len(v) == 0:
                continue
            stripped = _strip_empty(v, preserve_empty_strings=preserve_empty_strings)
            if stripped is None:
                continue
            if isinstance(stripped, dict) and len(stripped) == 0:
                continue
            cleaned[k] = stripped
        return cleaned if cleaned else None
    if isinstance(obj, list):
        result = []
        for item in obj:
            if item is None:
                continue
            stripped = _strip_empty(item, preserve_empty_strings=preserve_empty_strings)
            if stripped is None:
                continue
            if isinstance(stripped, dict) and len(stripped) == 0:
                continue
            result.append(stripped)
        return result
    return obj


def _component_to_dict(comp: Component, include_empty: bool = False) -> dict[str, Any]:
    """Map a Component dataclass to a CycloneDX camelCase dict.

    Args:
        comp: The component to serialize.
        include_empty: If True, include properties with empty values.

    Returns:
        A dict with CycloneDX field names.
    """
    d: dict[str, Any] = {
        "type": comp.type.value if hasattr(comp.type, "value") else str(comp.type),
        "name": comp.name,
        "version": comp.version,
        "purl": comp.purl,
        "bom-ref": comp.bom_ref,
        "supplier": _org_entity_to_dict(comp.supplier),
        "author": comp.author,
        "description": comp.description,
    }

    # Licenses
    if comp.licenses:
        licenses = []
        for lc in comp.licenses:
            lic_dict = _license_to_dict(lc)
            if lic_dict:
                licenses.append(lic_dict)
        if licenses:
            d["licenses"] = licenses

    # Properties
    if comp.properties:
        props = []
        for prop in comp.properties:
            prop_dict = _property_to_dict(prop, include_empty=include_empty)
            if prop_dict:
                props.append(prop_dict)
        if props:
            d["properties"] = props

    return d


def _org_entity_to_dict(org: OrganizationalEntity) -> dict[str, Any]:
    """Map an OrganizationalEntity to a CycloneDX dict.

    Note: CycloneDX uses singular 'url' key, not 'urls'.

    Args:
        org: The organizational entity to serialize.

    Returns:
        A dict with name and url fields.
    """
    d: dict[str, Any] = {"name": org.name}
    if org.urls:
        d["url"] = org.urls
    return d


def _license_to_dict(lc: LicenseChoice) -> dict[str, Any]:
    """Map a LicenseChoice to a CycloneDX license dict.

    Prefers license_id over license_name. Never emits both.
    Returns empty dict if neither is set.

    Args:
        lc: The license choice to serialize.

    Returns:
        A dict in CycloneDX license format, or empty dict.
    """
    if lc.license_id:
        return {"license": {"id": lc.license_id}}
    if lc.license_name:
        return {"license": {"name": lc.license_name}}
    return {}


def _property_to_dict(prop: Property, include_empty: bool = False) -> dict[str, Any]:
    """Map a Property to a CycloneDX property dict.

    Args:
        prop: The property to serialize.
        include_empty: If True, include properties with empty values.

    Returns:
        A dict with name and value, or empty dict if value is empty
        and include_empty is False.
    """
    if not include_empty and prop.value == "":
        return {}
    return {"name": prop.name, "value": prop.value}


def _dependency_to_dict(dep: Dependency) -> dict[str, Any]:
    """Map a Dependency to a CycloneDX dependency dict.

    Note: dependsOn is always emitted, even when empty, per CycloneDX
    convention for dependency entries.

    Args:
        dep: The dependency to serialize.

    Returns:
        A dict with ref and dependsOn fields.
    """
    return {
        "ref": dep.ref,
        "dependsOn": list(dep.depends_on),
    }


def _metadata_to_dict(meta: BomMetadata) -> dict[str, Any]:
    """Map BomMetadata to a CycloneDX metadata dict.

    Uses CycloneDX 1.5 modern tools format with components array.

    Args:
        meta: The metadata to serialize.

    Returns:
        A dict with timestamp, tools, and optionally authors.
    """
    d: dict[str, Any] = {
        "timestamp": meta.timestamp,
        "tools": {
            "components": [
                {
                    "type": "application",
                    "name": meta.tools_name,
                    "version": meta.tools_version,
                }
            ]
        },
    }
    if meta.authors:
        d["authors"] = [_org_entity_to_dict(a) for a in meta.authors]
    return d
