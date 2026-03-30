"""CycloneDX 1.5 data model dataclasses.

Provides BOM, Component, Dependency, BomMetadata, and supporting types
for SBOM generation. All types are stdlib-only with no external dependencies.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

APME_PROPERTY_NAMESPACE: str = "apme"
"""Namespace prefix for APME-specific component properties."""


class ComponentType(str, Enum):
    """CycloneDX component type classification.

    Attributes:
        APPLICATION: A software application.
        FRAMEWORK: A software framework.
        LIBRARY: A software library.
        CONTAINER: A container image.
        FIRMWARE: Firmware.
        FILE: A single file.
    """

    APPLICATION = "application"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    CONTAINER = "container"
    FIRMWARE = "firmware"
    FILE = "file"


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
    """CycloneDX license choice for component license metadata.

    Represents a license either by SPDX identifier or free-text name.
    At least one field should be populated; both may be empty if license
    information is unavailable.

    Attributes:
        license_id: SPDX license identifier (e.g. "Apache-2.0", "MIT").
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


def _make_timestamp() -> str:
    """Generate an ISO 8601 timestamp with UTC timezone.

    Returns:
        ISO 8601 formatted timestamp string.
    """
    return datetime.now(timezone.utc).isoformat()


def _make_serial_number() -> str:
    """Generate a URN UUID serial number for BOM identification.

    Returns:
        String in format urn:uuid:<uuid4>.
    """
    return f"urn:uuid:{uuid.uuid4()}"


@dataclass
class BomMetadata:
    """CycloneDX BOM metadata section.

    Attributes:
        timestamp: ISO 8601 creation timestamp.
        authors: List of BOM authors.
        tools_name: Name of the tool that generated the BOM.
        tools_version: Version of the generating tool.
    """

    timestamp: str = field(default_factory=_make_timestamp)
    authors: list[OrganizationalEntity] = field(default_factory=list)
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
    serial_number: str = field(default_factory=_make_serial_number)
    version: int = 1
    metadata: BomMetadata = field(default_factory=BomMetadata)
    components: list[Component] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


def mark_name_inferred(component: Component) -> None:
    """Mark a component's name as inferred from its directory path.

    Adds a property indicating the name was not from authoritative metadata
    but derived from the filesystem directory name.

    Args:
        component: The component to annotate.
    """
    component.properties.append(
        Property(name=f"{APME_PROPERTY_NAMESPACE}:name-source", value="inferred-from-directory")
    )
