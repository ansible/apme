"""Unit tests for CycloneDX 1.5 SBOM serializer (gateway)."""

from __future__ import annotations

import json
import re

from apme_gateway.api.sbom import (
    Bom,
    BomMetadata,
    Component,
    ComponentType,
    Dependency,
    LicenseChoice,
    OrganizationalEntity,
    Property,
    bom_to_dict,
    bom_to_json,
    validate_bom,
    validate_component,
)

FIXED_TIMESTAMP = "2026-01-15T12:00:00+00:00"
FIXED_SERIAL = "urn:uuid:00000000-0000-0000-0000-000000000001"


def _make_minimal_bom() -> Bom:
    meta = BomMetadata(timestamp=FIXED_TIMESTAMP)
    return Bom(serial_number=FIXED_SERIAL, metadata=meta)


def _make_component(**overrides: object) -> Component:
    defaults = {
        "type": ComponentType.LIBRARY,
        "name": "my-lib",
        "version": "1.0.0",
        "purl": "pkg:pypi/my-lib@1.0.0",
        "bom_ref": "pkg:pypi/my-lib@1.0.0",
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return Component(**defaults)  # type: ignore[arg-type]


def _assert_no_nulls(obj: object, path: str = "$") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert v is not None, f"None value found at {path}.{k}"
            _assert_no_nulls(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            assert item is not None, f"None value found at {path}[{i}]"
            _assert_no_nulls(item, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# BOM structure tests
# ---------------------------------------------------------------------------


class TestMinimalBom:
    """Test serialization of a default/minimal BOM."""

    def test_minimal_bom_to_dict(self) -> None:
        """Minimal Bom serializes with required CycloneDX fields."""
        bom = _make_minimal_bom()
        d = bom_to_dict(bom)
        assert d["bomFormat"] == "CycloneDX"
        assert d["specVersion"] == "1.5"
        assert d["serialNumber"].startswith("urn:uuid:")
        assert d["version"] == 1
        assert "timestamp" in d["metadata"]
        assert "tools" in d["metadata"]

    def test_serial_number_format(self) -> None:
        """Generated serial number matches URN UUID format."""
        bom = Bom()
        d = bom_to_dict(bom)
        pattern = r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, d["serialNumber"])


class TestRoundtrip:
    """Test JSON roundtrip fidelity."""

    def test_bom_to_json_roundtrip(self) -> None:
        """bom_to_json produces same structure as json.dumps(bom_to_dict())."""
        bom = _make_minimal_bom()
        d = bom_to_dict(bom)
        j = bom_to_json(bom)
        assert json.loads(j) == d


class TestNullStripping:
    """Test that no None values appear in output."""

    def test_no_nulls_in_output(self) -> None:
        """Serialized output contains no None values."""
        bom = _make_minimal_bom()
        bom.components.append(_make_component(description=""))
        d = bom_to_dict(bom)
        _assert_no_nulls(d)


class TestEmptyStripping:
    """Test that empty collections and strings are omitted."""

    def test_empty_lists_omitted(self) -> None:
        """Empty list fields are excluded from output."""
        comp = _make_component(licenses=[], properties=[])
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        comp_dict = d["components"][0]
        assert "licenses" not in comp_dict
        assert "properties" not in comp_dict

    def test_empty_strings_omitted(self) -> None:
        """Empty string fields are excluded from output."""
        comp = _make_component(description="")
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        comp_dict = d["components"][0]
        assert "description" not in comp_dict


class TestKeyMappings:
    """Test CycloneDX key naming conventions."""

    def test_component_bom_ref_key(self) -> None:
        """Component bom_ref serializes as 'bom-ref' with hyphen."""
        comp = _make_component()
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        comp_dict = d["components"][0]
        assert "bom-ref" in comp_dict
        assert "bom_ref" not in comp_dict


class TestLicenseSerialization:
    """Test license choice serialization logic."""

    def test_license_id_preferred(self) -> None:
        """License ID is used when both id and name are present."""
        lc = LicenseChoice(license_id="MIT", license_name="MIT License")
        comp = _make_component(licenses=[lc])
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        lic = d["components"][0]["licenses"][0]
        assert lic == {"license": {"id": "MIT"}}

    def test_license_name_fallback(self) -> None:
        """License name is used when ID is empty."""
        lc = LicenseChoice(license_id="", license_name="Custom License")
        comp = _make_component(licenses=[lc])
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        lic = d["components"][0]["licenses"][0]
        assert lic == {"license": {"name": "Custom License"}}

    def test_license_empty_omitted(self) -> None:
        """Empty license entries result in omitted licenses field."""
        lc = LicenseChoice(license_id="", license_name="")
        comp = _make_component(licenses=[lc])
        bom = _make_minimal_bom()
        bom.components.append(comp)
        d = bom_to_dict(bom)
        comp_dict = d["components"][0]
        assert "licenses" not in comp_dict


class TestToolsFormat:
    """Test CycloneDX 1.5 tools format."""

    def test_tools_modern_format(self) -> None:
        """Tools metadata uses CycloneDX 1.5 components format."""
        bom = _make_minimal_bom()
        d = bom_to_dict(bom)
        tools = d["metadata"]["tools"]
        assert tools == {"components": [{"type": "application", "name": "apme", "version": "0.1.0"}]}


class TestDependencies:
    """Test dependency serialization."""

    def test_dependency_serialization(self) -> None:
        """Dependency serializes with ref and dependsOn fields."""
        dep = Dependency(ref="pkg:pypi/requests@2.31.0", depends_on=["pkg:pypi/urllib3@2.0.0"])
        bom = _make_minimal_bom()
        bom.dependencies.append(dep)
        d = bom_to_dict(bom)
        dep_dict = d["dependencies"][0]
        assert dep_dict["ref"] == "pkg:pypi/requests@2.31.0"
        assert dep_dict["dependsOn"] == ["pkg:pypi/urllib3@2.0.0"]

    def test_dependency_empty_depends_on(self) -> None:
        """Empty dependsOn array is preserved in output."""
        dep = Dependency(ref="pkg:pypi/standalone@1.0.0", depends_on=[])
        bom = _make_minimal_bom()
        bom.dependencies.append(dep)
        d = bom_to_dict(bom)
        dep_dict = d["dependencies"][0]
        assert "dependsOn" in dep_dict
        assert dep_dict["dependsOn"] == []


class TestPopulatedBom:
    """Test a fully populated BOM."""

    def test_populated_bom(self) -> None:
        """Fully populated BOM with all fields serializes correctly."""
        bom = _make_minimal_bom()
        bom.components.append(
            _make_component(
                name="requests",
                version="2.31.0",
                purl="pkg:pypi/requests@2.31.0",
                bom_ref="pkg:pypi/requests@2.31.0",
                description="HTTP library",
                licenses=[LicenseChoice(license_id="Apache-2.0")],
                properties=[Property(name="apme:source", value="pypi")],
                supplier=OrganizationalEntity(name="PSF", urls=["https://python.org"]),
                author="Kenneth Reitz",
            )
        )
        bom.dependencies.append(Dependency(ref="pkg:pypi/requests@2.31.0", depends_on=["pkg:pypi/urllib3@2.0.0"]))
        d = bom_to_dict(bom)

        assert len(d["components"]) == 1
        assert len(d["dependencies"]) == 1

        comp = d["components"][0]
        assert comp["name"] == "requests"
        assert comp["bom-ref"] == "pkg:pypi/requests@2.31.0"
        assert comp["description"] == "HTTP library"
        assert comp["author"] == "Kenneth Reitz"
        assert comp["supplier"] == {"name": "PSF", "url": ["https://python.org"]}
        assert comp["licenses"] == [{"license": {"id": "Apache-2.0"}}]
        _assert_no_nulls(d)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Test BOM and component validation."""

    def test_valid_component(self) -> None:
        """Valid component produces only warnings (unknown supplier/author)."""
        comp = _make_component()
        errors = validate_component(comp)
        assert all(e.severity == "warning" for e in errors)

    def test_missing_required_fields(self) -> None:
        """Missing required fields produce error-level findings."""
        comp = Component(
            type=ComponentType.LIBRARY,
            name="",
            version="",
            purl="",
            bom_ref="",
        )
        errors = validate_component(comp)
        error_fields = {e.field for e in errors if e.severity == "error"}
        assert "name" in error_fields
        assert "version" in error_fields
        assert "purl" in error_fields
        assert "bom_ref" in error_fields

    def test_validate_bom_duplicates(self) -> None:
        """Duplicate bom_ref values produce error-level findings."""
        bom = _make_minimal_bom()
        bom.components.append(_make_component(bom_ref="dup-ref"))
        bom.components.append(_make_component(name="other", bom_ref="dup-ref"))
        result = validate_bom(bom)
        dup_errors = [e for e in result.errors if e.field == "bom_ref" and "Duplicate" in e.message]
        assert len(dup_errors) == 1

    def test_valid_bom_is_valid(self) -> None:
        """BOM with valid components reports is_valid=True."""
        bom = _make_minimal_bom()
        bom.components.append(_make_component())
        result = validate_bom(bom)
        assert result.is_valid
