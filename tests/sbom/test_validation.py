"""Tests for CycloneDX component and BOM validation."""

from __future__ import annotations

from apme_engine.sbom.models import (
    Bom,
    Component,
    ComponentType,
    OrganizationalEntity,
)
from apme_engine.sbom.validation import (
    ValidationError,
    ValidationResult,
    validate_bom,
    validate_component,
)


def _make_valid_component(
    *,
    name: str = "cisco.ios",
    version: str = "2.0.0",
    purl: str = "pkg:generic/cisco.ios@2.0.0",
    bom_ref: str = "pkg:generic/cisco.ios@2.0.0",
    supplier_name: str = "Cisco",
    author: str = "Cisco Systems",
) -> Component:
    """Create a valid component with all required and recommended fields.

    Args:
        name: Component name.
        version: Component version.
        purl: Package URL.
        bom_ref: BOM reference identifier.
        supplier_name: Supplier organization name.
        author: Author name.

    Returns:
        Component instance with all fields populated.
    """
    return Component(
        type=ComponentType.LIBRARY,
        name=name,
        version=version,
        purl=purl,
        bom_ref=bom_ref,
        supplier=OrganizationalEntity(name=supplier_name),
        author=author,
    )


class TestValidateComponent:
    """Tests for validate_component function."""

    def test_valid_component_no_errors(self) -> None:
        """A fully populated component returns empty error list."""
        component = _make_valid_component()
        errors = validate_component(component)
        assert errors == []

    def test_missing_name_is_error(self) -> None:
        """Component with name='' produces error-level finding."""
        component = _make_valid_component(name="")
        errors = validate_component(component)
        name_errors = [e for e in errors if e.field == "name" and e.severity == "error"]
        assert len(name_errors) == 1

    def test_missing_version_is_error(self) -> None:
        """Component with version='' produces error-level finding."""
        component = _make_valid_component(version="")
        errors = validate_component(component)
        version_errors = [e for e in errors if e.field == "version" and e.severity == "error"]
        assert len(version_errors) == 1

    def test_missing_purl_is_error(self) -> None:
        """Component with purl='' produces error-level finding."""
        component = _make_valid_component(purl="")
        errors = validate_component(component)
        purl_errors = [e for e in errors if e.field == "purl" and e.severity == "error"]
        assert len(purl_errors) == 1

    def test_missing_bom_ref_is_error(self) -> None:
        """Component with bom_ref='' produces error-level finding."""
        component = _make_valid_component(bom_ref="")
        errors = validate_component(component)
        bom_ref_errors = [e for e in errors if e.field == "bom_ref" and e.severity == "error"]
        assert len(bom_ref_errors) == 1

    def test_missing_multiple_fields_collects_all(self) -> None:
        """Component missing multiple fields returns all errors."""
        component = _make_valid_component(name="", version="", purl="")
        errors = validate_component(component)
        error_findings = [e for e in errors if e.severity == "error"]
        assert len(error_findings) == 3
        fields = {e.field for e in error_findings}
        assert fields == {"name", "version", "purl"}

    def test_unknown_supplier_is_warning(self) -> None:
        """Component with supplier.name='unknown' produces warning."""
        component = _make_valid_component(supplier_name="unknown")
        errors = validate_component(component)
        supplier_warnings = [e for e in errors if e.field == "supplier" and e.severity == "warning"]
        assert len(supplier_warnings) == 1

    def test_unknown_author_is_warning(self) -> None:
        """Component with author='unknown' produces warning."""
        component = _make_valid_component(author="unknown")
        errors = validate_component(component)
        author_warnings = [e for e in errors if e.field == "author" and e.severity == "warning"]
        assert len(author_warnings) == 1

    def test_warnings_dont_fail_validation(self) -> None:
        """Component with only warnings still validates as True."""
        component = _make_valid_component(supplier_name="unknown", author="unknown")
        errors = validate_component(component)
        result = ValidationResult(errors=errors)
        assert result.is_valid is True
        assert len(errors) == 2

    def test_error_fields_populated(self) -> None:
        """Each ValidationError has all required fields populated."""
        component = _make_valid_component(name="test-comp", version="")
        errors = validate_component(component)
        assert len(errors) >= 1
        for error in errors:
            assert error.component_name != ""
            assert error.field != ""
            assert error.severity in ("error", "warning")
            assert error.message != ""
            assert error.suggestion != ""


class TestValidateBom:
    """Tests for validate_bom function."""

    def test_validate_bom_checks_all_components(self) -> None:
        """BOM with multiple components validates each one."""
        components = [
            _make_valid_component(name="", bom_ref="ref1", purl="purl1"),
            _make_valid_component(name="", bom_ref="ref2", purl="purl2"),
            _make_valid_component(name="", bom_ref="ref3", purl="purl3"),
        ]
        bom = Bom(components=components)
        result = validate_bom(bom)
        name_errors = [e for e in result.errors if e.field == "name"]
        assert len(name_errors) == 3

    def test_validate_bom_duplicate_bom_ref(self) -> None:
        """BOM with duplicate bom_ref produces error."""
        components = [
            _make_valid_component(name="comp1", bom_ref="dup-ref", purl="purl1"),
            _make_valid_component(name="comp2", bom_ref="dup-ref", purl="purl2"),
        ]
        bom = Bom(components=components)
        result = validate_bom(bom)
        dup_errors = [e for e in result.errors if e.field == "bom_ref" and "duplicate" in e.message.lower()]
        assert len(dup_errors) >= 1

    def test_validate_bom_valid(self) -> None:
        """BOM with unique bom_refs and valid components validates successfully."""
        components = [
            _make_valid_component(name="comp1", bom_ref="ref1", purl="purl1"),
            _make_valid_component(name="comp2", bom_ref="ref2", purl="purl2"),
        ]
        bom = Bom(components=components)
        result = validate_bom(bom)
        assert result.is_valid is True


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_is_valid_property(self) -> None:
        """ValidationResult.is_valid returns True when no errors exist."""
        # No errors at all
        result_empty = ValidationResult()
        assert result_empty.is_valid is True

        # Only warnings
        result_warnings = ValidationResult(
            errors=[
                ValidationError(
                    component_name="test",
                    field="supplier",
                    severity="warning",
                    message="test warning",
                    suggestion="fix it",
                )
            ]
        )
        assert result_warnings.is_valid is True

        # Has error
        result_errors = ValidationResult(
            errors=[
                ValidationError(
                    component_name="test",
                    field="name",
                    severity="error",
                    message="missing name",
                    suggestion="provide a name",
                )
            ]
        )
        assert result_errors.is_valid is False
