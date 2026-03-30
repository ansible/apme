"""CycloneDX component and BOM validation with multi-error collection.

Validates components and BOMs against NTIA minimum element requirements,
collecting all findings (errors and warnings) without rejecting components.
Validation is advisory only -- invalid components remain in the BOM.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from apme_engine.sbom.models import Bom, Component


@dataclass
class ValidationError:
    """A single validation finding for a component.

    Attributes:
        component_name: Name of the component (or identifier if name is empty).
        field: The field that triggered the finding.
        severity: Finding severity, either "error" or "warning".
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
    """Aggregated validation result for a BOM or component set.

    Attributes:
        errors: List of all validation findings (errors and warnings).
    """

    errors: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return True when no error-level findings exist.

        Warning-level findings do not affect validity.

        Returns:
            True if no error-severity findings, False otherwise.
        """
        return not any(e.severity == "error" for e in self.errors)


_REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("name", "Provide a component name (e.g., collection FQCN or package name)"),
    ("version", "Specify the component version string"),
    ("purl", "Generate a valid Package URL for this component"),
    ("bom_ref", "Set bom_ref to a unique identifier (typically the PURL)"),
]
"""Required fields and their fix suggestions."""


def validate_component(component: Component) -> list[ValidationError]:
    """Validate a single component against NTIA minimum element requirements.

    Checks required fields (name, version, purl, bom_ref) as errors and
    recommended fields (supplier, author) as warnings. Collects ALL findings
    before returning -- does not fail on first error.

    Args:
        component: The component to validate.

    Returns:
        List of ValidationError findings (may be empty if fully valid).
    """
    errors: list[ValidationError] = []
    comp_label = component.name or component.purl or component.bom_ref or "<unknown>"

    # Check required fields
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

    # Check recommended fields
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

    Validates each component individually and checks that all bom_ref values
    are unique across the BOM. All findings are collected -- no components
    are excluded from the BOM.

    Args:
        bom: The BOM to validate.

    Returns:
        ValidationResult with aggregated findings from all components.
    """
    all_errors: list[ValidationError] = []

    # Validate each component
    for component in bom.components:
        all_errors.extend(validate_component(component))

    # Check for duplicate bom_ref values
    bom_refs = [c.bom_ref for c in bom.components if c.bom_ref]
    ref_counts = Counter(bom_refs)
    for ref, count in ref_counts.items():
        if count > 1:
            # Find components with this duplicate ref
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
