"""CycloneDX 1.5 SBOM serializer for gateway manifest data.

Converts persisted ScanManifest, ScanCollection, and ScanPythonPackage
rows into a CycloneDX 1.5 JSON dict.  All CycloneDX dataclasses, PURL
generation, serialization, and validation logic live in this module so
the engine remains format-agnostic.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from apme_gateway.db.models import ScanCollection, ScanManifest, ScanPythonPackage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PURL helpers
# ---------------------------------------------------------------------------

_GALAXY_URL: str = "https://galaxy.ansible.com"


def _normalize_pypi_name(name: str) -> str:
    """Normalize a Python package name per PEP 503.

    Args:
        name: Raw Python package name.

    Returns:
        PEP 503 normalized package name.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _make_pypi_purl(name: str, version: str) -> str:
    """Generate a PURL for a Python (PyPI) package.

    Args:
        name: Python package name (will be PEP 503 normalized).
        version: Package version string.

    Returns:
        PURL string in format ``pkg:pypi/{normalized_name}@{version}``.
    """
    normalized = _normalize_pypi_name(name)
    safe_name = quote(normalized, safe="")
    safe_version = quote(version, safe="")
    return f"pkg:pypi/{safe_name}@{safe_version}"


def _make_collection_purl(fqcn: str, version: str) -> str:
    """Generate a PURL for an Ansible Galaxy collection.

    Args:
        fqcn: Fully-qualified collection name (e.g. ``cisco.ios``).
        version: Collection version string.

    Returns:
        PURL string in format ``pkg:generic/{fqcn}@{version}?repository_url=...``.
    """
    safe_fqcn = quote(fqcn, safe=".")
    safe_version = quote(version, safe="")
    return f"pkg:generic/{safe_fqcn}@{safe_version}?repository_url={_GALAXY_URL}"


# ---------------------------------------------------------------------------
# CycloneDX dataclasses
# ---------------------------------------------------------------------------


class ComponentType(str, Enum):
    """CycloneDX component type classification.

    Attributes:
        APPLICATION: A software application.
        FRAMEWORK: A software framework.
        LIBRARY: A software library.
    """

    APPLICATION = "application"
    FRAMEWORK = "framework"
    LIBRARY = "library"


@dataclass
class OrganizationalEntity:
    """CycloneDX organizational entity (supplier, manufacturer).

    Attributes:
        name: Organization name.
        urls: Associated URLs.
    """

    name: str = "unknown"
    urls: list[str] = field(default_factory=list)


@dataclass
class Property:
    """CycloneDX property key-value pair.

    Attributes:
        name: Property name (namespace:key format).
        value: Property value.
    """

    name: str = ""
    value: str = ""


@dataclass
class LicenseChoice:
    """CycloneDX license choice.

    Attributes:
        license_id: SPDX license identifier (e.g. ``Apache-2.0``).
        license_name: Free-text license name when no SPDX ID is available.
    """

    license_id: str = ""
    license_name: str = ""


@dataclass
class Component:
    """CycloneDX component representing a software dependency.

    Attributes:
        type: Component classification (library, application, etc.).
        name: Component name.
        version: Component version string.
        purl: Package URL uniquely identifying the component.
        bom_ref: BOM reference identifier (typically same as purl).
        supplier: Organization that supplied the component.
        author: Component author name.
        description: Human-readable component description.
        licenses: License metadata for the component.
        properties: Additional key-value properties.
    """

    type: ComponentType
    name: str
    version: str
    purl: str
    bom_ref: str
    supplier: OrganizationalEntity = field(default_factory=OrganizationalEntity)
    author: str = "unknown"
    description: str = ""
    licenses: list[LicenseChoice] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)


@dataclass
class Dependency:
    """CycloneDX dependency relationship.

    Attributes:
        ref: PURL or bom-ref of the component.
        depends_on: List of PURLs/bom-refs this component depends on.
    """

    ref: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class BomMetadata:
    """CycloneDX BOM metadata section.

    Attributes:
        timestamp: ISO 8601 creation timestamp.
        tools_name: Name of the tool that generated the BOM.
        tools_version: Version of the generating tool.
    """

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tools_name: str = "apme"
    tools_version: str = "0.1.0"


@dataclass
class Bom:
    """CycloneDX 1.5 Bill of Materials root object.

    Attributes:
        bom_format: BOM format identifier (always CycloneDX).
        spec_version: CycloneDX specification version.
        serial_number: Unique BOM identifier as URN UUID.
        version: BOM document version number.
        metadata: BOM metadata section.
        components: List of software components.
        dependencies: List of dependency relationships.
    """

    bom_format: str = "CycloneDX"
    spec_version: str = "1.5"
    serial_number: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    version: int = 1
    metadata: BomMetadata = field(default_factory=BomMetadata)
    components: list[Component] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Serializer: dataclass -> dict
# ---------------------------------------------------------------------------


def _strip_empty(obj: Any, preserve_empty_strings: bool = False) -> Any:
    """Recursively remove None, empty strings, and empty lists from dicts.

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


def _org_entity_to_dict(org: OrganizationalEntity) -> dict[str, Any]:
    d: dict[str, Any] = {"name": org.name}
    if org.urls:
        d["url"] = org.urls
    return d


def _license_to_dict(lc: LicenseChoice) -> dict[str, Any]:
    if lc.license_id:
        return {"license": {"id": lc.license_id}}
    if lc.license_name:
        return {"license": {"name": lc.license_name}}
    return {}


def _property_to_dict(prop: Property, include_empty: bool = False) -> dict[str, Any]:
    if not include_empty and prop.value == "":
        return {}
    return {"name": prop.name, "value": prop.value}


def _component_to_dict(comp: Component, include_empty: bool = False) -> dict[str, Any]:
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
    if comp.licenses:
        licenses = [ld for lc in comp.licenses if (ld := _license_to_dict(lc))]
        if licenses:
            d["licenses"] = licenses
    if comp.properties:
        props = [pd for p in comp.properties if (pd := _property_to_dict(p, include_empty=include_empty))]
        if props:
            d["properties"] = props
    return d


def _dependency_to_dict(dep: Dependency) -> dict[str, Any]:
    return {"ref": dep.ref, "dependsOn": list(dep.depends_on)}


def _metadata_to_dict(meta: BomMetadata) -> dict[str, Any]:
    return {
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
    if bom.dependencies:
        result["dependencies"] = [_dependency_to_dict(dep) for dep in bom.dependencies]

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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    """A single validation finding for a component.

    Attributes:
        component_name: Name of the component (or identifier if name is empty).
        field: The field that triggered the finding.
        severity: Finding severity, either ``error`` or ``warning``.
        message: Human-readable description of the issue.
        suggestion: Actionable fix suggestion.
    """

    component_name: str
    field: str
    severity: str
    message: str
    suggestion: str


@dataclass
class ValidationResult:
    """Aggregated validation result for a BOM.

    Attributes:
        errors: List of all validation findings (errors and warnings).
    """

    errors: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return True when no error-level findings exist.

        Returns:
            True if no error-severity findings, False otherwise.
        """
        return not any(e.severity == "error" for e in self.errors)


_REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("name", "Provide a component name"),
    ("version", "Specify the component version string"),
    ("purl", "Generate a valid Package URL"),
    ("bom_ref", "Set bom_ref to a unique identifier"),
]


def validate_component(component: Component) -> list[ValidationError]:
    """Validate a single component against NTIA minimum element requirements.

    Args:
        component: The component to validate.

    Returns:
        List of ValidationError findings (may be empty if fully valid).
    """
    errors: list[ValidationError] = []
    comp_label = component.name or component.purl or component.bom_ref or "<unknown>"
    for field_name, suggestion in _REQUIRED_FIELDS:
        value = getattr(component, field_name, "")
        if not value:
            errors.append(
                ValidationError(
                    component_name=comp_label,
                    field=field_name,
                    severity="error",
                    message=f"Required field '{field_name}' is missing or empty",
                    suggestion=suggestion,
                )
            )
    if component.supplier.name == "unknown":
        errors.append(
            ValidationError(
                component_name=comp_label,
                field="supplier",
                severity="warning",
                message="Supplier name is 'unknown'",
                suggestion="Set supplier to the organization that provides this component",
            )
        )
    if component.author == "unknown":
        errors.append(
            ValidationError(
                component_name=comp_label,
                field="author",
                severity="warning",
                message="Author is 'unknown'",
                suggestion="Set author to the person or organization that authored this component",
            )
        )
    return errors


def validate_bom(bom: Bom) -> ValidationResult:
    """Validate all components in a BOM and check for duplicate bom_refs.

    Args:
        bom: The BOM to validate.

    Returns:
        ValidationResult with aggregated findings from all components.
    """
    all_errors: list[ValidationError] = []
    for component in bom.components:
        all_errors.extend(validate_component(component))
    bom_refs = [c.bom_ref for c in bom.components if c.bom_ref]
    ref_counts = Counter(bom_refs)
    for ref, count in ref_counts.items():
        if count > 1:
            dup_names = [c.name or c.purl or "<unknown>" for c in bom.components if c.bom_ref == ref]
            all_errors.append(
                ValidationError(
                    component_name=", ".join(dup_names),
                    field="bom_ref",
                    severity="error",
                    message=f"Duplicate bom_ref '{ref}' found on {count} components",
                    suggestion="Ensure each component has a unique bom_ref value",
                )
            )
    return ValidationResult(errors=all_errors)


# ---------------------------------------------------------------------------
# Public API: DB models -> CycloneDX dict
# ---------------------------------------------------------------------------


def _make_license_choice(license_str: str) -> list[LicenseChoice]:
    """Parse a license string into LicenseChoice list.

    Args:
        license_str: SPDX license identifier string.

    Returns:
        List with one LicenseChoice, or empty list if input is empty.
    """
    if not license_str:
        return []
    return [LicenseChoice(license_id=license_str)]


def _collection_to_component(c: ScanCollection) -> Component:
    """Convert a ScanCollection DB row to a CycloneDX Component.

    Args:
        c: ScanCollection ORM row.

    Returns:
        CycloneDX Component dataclass.
    """
    purl = _make_collection_purl(c.fqcn, c.version)
    return Component(
        type=ComponentType.LIBRARY,
        name=c.fqcn,
        version=c.version,
        purl=purl,
        bom_ref=purl,
        supplier=OrganizationalEntity(name=c.supplier) if c.supplier else OrganizationalEntity(),
        author=c.supplier or "unknown",
        licenses=_make_license_choice(c.license),
        properties=[Property(name="apme:source", value=c.source)],
    )


def _package_to_component(p: ScanPythonPackage) -> Component:
    """Convert a ScanPythonPackage DB row to a CycloneDX Component.

    Args:
        p: ScanPythonPackage ORM row.

    Returns:
        CycloneDX Component dataclass.
    """
    purl = _make_pypi_purl(p.name, p.version)
    return Component(
        type=ComponentType.LIBRARY,
        name=p.name,
        version=p.version,
        purl=purl,
        bom_ref=purl,
        supplier=OrganizationalEntity(name=p.supplier) if p.supplier else OrganizationalEntity(),
        author=p.supplier or "unknown",
        licenses=_make_license_choice(p.license),
        properties=[Property(name="apme:source", value="pypi")],
    )


def manifest_to_cyclonedx(
    manifest: ScanManifest,
    collections: list[ScanCollection],
    packages: list[ScanPythonPackage],
    tools_version: str,
) -> dict[str, Any]:
    """Serialize persisted manifest data into CycloneDX 1.5 JSON dict.

    Args:
        manifest: ScanManifest ORM row.
        collections: ScanCollection ORM rows for this scan.
        packages: ScanPythonPackage ORM rows for this scan.
        tools_version: APME version string for BOM metadata.

    Returns:
        CycloneDX 1.5 JSON-compatible dict.
    """
    bom = Bom(metadata=BomMetadata(tools_version=tools_version))

    # Add ansible-core as the first component
    if manifest.ansible_core_version:
        core_purl = _make_pypi_purl("ansible-core", manifest.ansible_core_version)
        bom.components.append(
            Component(
                type=ComponentType.FRAMEWORK,
                name="ansible-core",
                version=manifest.ansible_core_version,
                purl=core_purl,
                bom_ref=core_purl,
                supplier=OrganizationalEntity(name="Red Hat"),
                author="Red Hat",
                properties=[Property(name="apme:source", value="pypi")],
            )
        )

    for c in collections:
        bom.components.append(_collection_to_component(c))

    for p in packages:
        bom.components.append(_package_to_component(p))

    # Each component gets a dependency entry (no resolved dep graph yet)
    for comp in bom.components:
        bom.dependencies.append(Dependency(ref=comp.bom_ref))

    return bom_to_dict(bom)
