"""Schema validation integration tests for CycloneDX 1.5 serializer output.

Validates that bom_to_dict output conforms to the official CycloneDX 1.5
JSON Schema, proving spec compliance of the explicit dict builder.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft7Validator
from referencing import Registry, Resource

from apme_engine.sbom.models import (
    Bom,
    BomMetadata,
    Component,
    ComponentType,
    Dependency,
    LicenseChoice,
    OrganizationalEntity,
    Property,
)
from apme_engine.sbom.serializer import bom_to_dict

SCHEMA_DIR = Path(__file__).parent / "schemas"


@pytest.fixture  # type: ignore[untyped-decorator]
def cdx_validator() -> Draft7Validator:
    """Create a Draft7Validator with CycloneDX 1.5 schema and SPDX ref resolution.

    Returns:
        Draft7Validator configured with CycloneDX 1.5 and SPDX schemas.
    """
    bom_schema = json.loads((SCHEMA_DIR / "bom-1.5.schema.json").read_text(encoding="utf-8"))
    spdx_schema = json.loads((SCHEMA_DIR / "spdx.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(
        "spdx.schema.json",
        Resource.from_contents(spdx_schema),
    )
    return Draft7Validator(bom_schema, registry=registry)


@pytest.mark.integration
class TestSchemaValidation:
    """CycloneDX 1.5 schema validation integration tests."""

    def test_minimal_bom_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """Default Bom() serialized via bom_to_dict passes schema validation.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(timestamp="2026-03-27T00:00:00+00:00"),
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_single_component_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """Bom with one Component passes schema validation.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(timestamp="2026-03-27T00:00:00+00:00"),
            components=[
                Component(
                    type=ComponentType.LIBRARY,
                    name="ansible.netcommon",
                    version="7.1.0",
                    purl="pkg:generic/ansible/netcommon@7.1.0",
                    bom_ref="pkg:generic/ansible/netcommon@7.1.0",
                ),
            ],
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_component_with_licenses_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """Component with LicenseChoice(license_id='MIT') passes validation.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(timestamp="2026-03-27T00:00:00+00:00"),
            components=[
                Component(
                    type=ComponentType.LIBRARY,
                    name="my-collection",
                    version="1.0.0",
                    purl="pkg:generic/my/collection@1.0.0",
                    bom_ref="pkg:generic/my/collection@1.0.0",
                    licenses=[LicenseChoice(license_id="MIT")],
                ),
            ],
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_component_with_properties_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """Component with Property entries passes validation.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(timestamp="2026-03-27T00:00:00+00:00"),
            components=[
                Component(
                    type=ComponentType.LIBRARY,
                    name="my-role",
                    version="2.0.0",
                    purl="pkg:generic/my/role@2.0.0",
                    bom_ref="pkg:generic/my/role@2.0.0",
                    properties=[
                        Property(name="apme:content-type", value="role"),
                        Property(name="apme:namespace", value="my"),
                    ],
                ),
            ],
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_dependencies_pass_schema(self, cdx_validator: Draft7Validator) -> None:
        """Bom with Dependency entries (including empty dependsOn) passes validation.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(timestamp="2026-03-27T00:00:00+00:00"),
            components=[
                Component(
                    type=ComponentType.LIBRARY,
                    name="parent",
                    version="1.0.0",
                    purl="pkg:generic/ns/parent@1.0.0",
                    bom_ref="pkg:generic/ns/parent@1.0.0",
                ),
                Component(
                    type=ComponentType.LIBRARY,
                    name="child",
                    version="2.0.0",
                    purl="pkg:generic/ns/child@2.0.0",
                    bom_ref="pkg:generic/ns/child@2.0.0",
                ),
            ],
            dependencies=[
                Dependency(
                    ref="pkg:generic/ns/parent@1.0.0",
                    depends_on=["pkg:generic/ns/child@2.0.0"],
                ),
                Dependency(
                    ref="pkg:generic/ns/child@2.0.0",
                    depends_on=[],
                ),
            ],
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_populated_bom_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """Fully populated BOM with multiple components, deps, licenses, properties passes.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(
                timestamp="2026-03-27T00:00:00+00:00",
                authors=[OrganizationalEntity(name="Test Org")],
            ),
            components=[
                Component(
                    type=ComponentType.LIBRARY,
                    name="ansible.netcommon",
                    version="7.1.0",
                    purl="pkg:generic/ansible/netcommon@7.1.0",
                    bom_ref="pkg:generic/ansible/netcommon@7.1.0",
                    supplier=OrganizationalEntity(name="Ansible"),
                    author="Ansible Team",
                    description="Ansible network common collection",
                    licenses=[LicenseChoice(license_id="GPL-3.0-or-later")],
                    properties=[
                        Property(name="apme:content-type", value="collection"),
                    ],
                ),
                Component(
                    type=ComponentType.LIBRARY,
                    name="requests",
                    version="2.31.0",
                    purl="pkg:pypi/requests@2.31.0",
                    bom_ref="pkg:pypi/requests@2.31.0",
                    licenses=[LicenseChoice(license_id="Apache-2.0")],
                    properties=[
                        Property(name="apme:content-type", value="python-package"),
                    ],
                ),
                Component(
                    type=ComponentType.LIBRARY,
                    name="my_role",
                    version="0.0.0",
                    purl="pkg:generic/ns/my_role@0.0.0",
                    bom_ref="pkg:generic/ns/my_role@0.0.0",
                    properties=[
                        Property(name="apme:content-type", value="role"),
                        Property(name="apme:name-source", value="inferred-from-directory"),
                    ],
                ),
            ],
            dependencies=[
                Dependency(
                    ref="pkg:generic/ansible/netcommon@7.1.0",
                    depends_on=["pkg:pypi/requests@2.31.0"],
                ),
                Dependency(
                    ref="pkg:pypi/requests@2.31.0",
                    depends_on=[],
                ),
                Dependency(
                    ref="pkg:generic/ns/my_role@0.0.0",
                    depends_on=[],
                ),
            ],
        )
        result = bom_to_dict(bom)
        cdx_validator.validate(result)

    def test_tools_metadata_passes_schema(self, cdx_validator: Draft7Validator) -> None:
        """The 1.5 tools.components format passes the oneOf discriminator in schema.

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        bom = Bom(
            serial_number="urn:uuid:00000000-0000-0000-0000-000000000000",
            metadata=BomMetadata(
                timestamp="2026-03-27T00:00:00+00:00",
                tools_name="apme",
                tools_version="0.1.0",
            ),
        )
        result = bom_to_dict(bom)
        # Verify tools structure is present and valid
        assert "tools" in result["metadata"]
        assert "components" in result["metadata"]["tools"]
        cdx_validator.validate(result)

    def test_invalid_bom_fails_schema(self, cdx_validator: Draft7Validator) -> None:
        """A dict missing bomFormat fails schema validation (negative test).

        Args:
            cdx_validator: CycloneDX JSON schema validator fixture.
        """
        invalid = {
            "specVersion": "1.5",
            "version": 1,
        }
        with pytest.raises(jsonschema.ValidationError):
            cdx_validator.validate(invalid)
