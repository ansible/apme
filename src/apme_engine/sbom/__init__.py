"""CycloneDX 1.5 SBOM data model, PURL generation, and inventory collectors.

This module provides stdlib-only dataclasses for CycloneDX BOM structures,
PURL generation functions for Ansible collections, roles, and Python packages,
and collector functions for building component inventories.
"""

from __future__ import annotations

from apme_engine.sbom.collect_collections import collect_collections
from apme_engine.sbom.collect_packages import collect_packages
from apme_engine.sbom.collect_roles import collect_roles
from apme_engine.sbom.models import (
    APME_PROPERTY_NAMESPACE,
    Bom,
    BomMetadata,
    Component,
    ComponentType,
    Dependency,
    LicenseChoice,
    OrganizationalEntity,
    Property,
    mark_name_inferred,
)
from apme_engine.sbom._yaml_subset import parse_yaml_subset
from apme_engine.sbom.serializer import bom_to_dict, bom_to_json
from apme_engine.sbom.purl import (
    make_collection_purl,
    make_pypi_purl,
    make_role_purl,
    normalize_pypi_name,
)
from apme_engine.sbom.validation import (
    ValidationError,
    ValidationResult,
    validate_bom,
    validate_component,
)

__all__ = [
    "APME_PROPERTY_NAMESPACE",
    "bom_to_dict",
    "bom_to_json",
    "collect_collections",
    "collect_packages",
    "collect_roles",
    "Bom",
    "BomMetadata",
    "Component",
    "ComponentType",
    "Dependency",
    "LicenseChoice",
    "OrganizationalEntity",
    "Property",
    "ValidationError",
    "ValidationResult",
    "make_collection_purl",
    "make_pypi_purl",
    "make_role_purl",
    "mark_name_inferred",
    "normalize_pypi_name",
    "parse_yaml_subset",
    "validate_bom",
    "validate_component",
]
