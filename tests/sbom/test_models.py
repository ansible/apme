"""Unit tests for CycloneDX data model dataclasses."""

from __future__ import annotations

import glob
import re
import uuid
from datetime import datetime, timezone

import pytest

from apme_engine.sbom.models import (
    Bom,
    BomMetadata,
    Component,
    ComponentType,
    Dependency,
    OrganizationalEntity,
    Property,
    mark_name_inferred,
)


class TestComponentFields:
    """Tests for Component dataclass fields and defaults."""

    def test_component_has_required_fields(self, sample_component: Component) -> None:
        """Component dataclass has type, name, version, purl, bom_ref fields."""
        assert hasattr(sample_component, "type")
        assert hasattr(sample_component, "name")
        assert hasattr(sample_component, "version")
        assert hasattr(sample_component, "purl")
        assert hasattr(sample_component, "bom_ref")

    def test_component_defaults(self) -> None:
        """Supplier defaults to unknown, author defaults to unknown."""
        comp = Component(
            type=ComponentType.LIBRARY,
            name="test",
            version="1.0.0",
            purl="pkg:pypi/test@1.0.0",
            bom_ref="pkg:pypi/test@1.0.0",
        )
        assert comp.supplier.name == "unknown"
        assert comp.author == "unknown"

    def test_component_type_is_library(self) -> None:
        """ComponentType.LIBRARY exists and equals 'library'."""
        assert ComponentType.LIBRARY == "library"
        assert ComponentType.LIBRARY.value == "library"


class TestBom:
    """Tests for Bom dataclass."""

    def test_bom_has_serial_number(self) -> None:
        """Bom.serial_number starts with urn:uuid: and contains valid UUID."""
        bom = Bom()
        assert bom.serial_number.startswith("urn:uuid:")
        uuid_str = bom.serial_number.removeprefix("urn:uuid:")
        # Should not raise
        uuid.UUID(uuid_str)

    def test_bom_components_default_empty(self) -> None:
        """Bom.components defaults to empty list."""
        bom = Bom()
        assert bom.components == []
        assert isinstance(bom.components, list)

    def test_bom_spec_version(self) -> None:
        """Bom.spec_version is 1.5 and bom_format is CycloneDX."""
        bom = Bom()
        assert bom.spec_version == "1.5"
        assert bom.bom_format == "CycloneDX"


class TestBomMetadata:
    """Tests for BomMetadata dataclass."""

    def test_bom_metadata_has_timestamp(self) -> None:
        """BomMetadata.timestamp is ISO 8601 with timezone."""
        meta = BomMetadata()
        ts = meta.timestamp
        # Should parse as ISO 8601
        parsed = datetime.fromisoformat(ts)
        # Must have timezone info
        assert parsed.tzinfo is not None

    def test_bom_metadata_has_tool(self) -> None:
        """BomMetadata.tools_name is apme."""
        meta = BomMetadata()
        assert meta.tools_name == "apme"


class TestDependency:
    """Tests for Dependency dataclass."""

    def test_dependency_structure(self) -> None:
        """Dependency has ref (str) and depends_on (list[str])."""
        dep = Dependency(ref="pkg:pypi/test@1.0.0")
        assert dep.ref == "pkg:pypi/test@1.0.0"
        assert dep.depends_on == []
        assert isinstance(dep.depends_on, list)


class TestProperty:
    """Tests for Property dataclass."""

    def test_property_structure(self) -> None:
        """Property has name (str) and value (str)."""
        prop = Property(name="apme:source", value="galaxy")
        assert prop.name == "apme:source"
        assert prop.value == "galaxy"


class TestMarkNameInferred:
    """Tests for mark_name_inferred function."""

    def test_mark_name_inferred(self) -> None:
        """mark_name_inferred adds Property with name=apme:name-source, value=inferred-from-directory."""
        comp = Component(
            type=ComponentType.LIBRARY,
            name="my_role",
            version="unversioned",
            purl="pkg:generic/my_role@unversioned",
            bom_ref="pkg:generic/my_role@unversioned",
        )
        mark_name_inferred(comp)
        assert len(comp.properties) == 1
        assert comp.properties[0].name == "apme:name-source"
        assert comp.properties[0].value == "inferred-from-directory"


class TestNoExternalImports:
    """Tests that sbom module uses only stdlib."""

    def test_no_external_imports(self) -> None:
        """sbom module has zero non-stdlib imports."""
        import pathlib

        sbom_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "apme_engine" / "sbom"
        stdlib_modules = {
            "collections", "dataclasses", "enum", "uuid", "re", "datetime",
            "typing", "logging", "urllib", "__future__", "importlib",
        }

        for py_file in sbom_dir.glob("*.py"):
            content = py_file.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("import ") or line.startswith("from "):
                    # Skip relative imports (from . or from apme_engine)
                    if "from ." in line or "from apme_engine" in line:
                        continue
                    if line.startswith("from __future__"):
                        continue
                    # Extract module name
                    if line.startswith("from "):
                        module = line.split()[1].split(".")[0]
                    else:
                        module = line.split()[1].split(".")[0]
                    assert module in stdlib_modules, (
                        f"External import found in {py_file.name}: {line}"
                    )
