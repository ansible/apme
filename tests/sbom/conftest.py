"""Shared fixtures for SBOM tests."""

from __future__ import annotations

import pytest

from apme_engine.sbom.models import (
    Bom,
    BomMetadata,
    Component,
    ComponentType,
    OrganizationalEntity,
    Property,
)


@pytest.fixture()
def sample_component() -> Component:
    """Create a sample Component for testing.

    Returns:
        A Component with all fields populated.
    """
    return Component(
        type=ComponentType.LIBRARY,
        name="cisco.ios",
        version="2.0.0",
        purl="pkg:generic/cisco.ios@2.0.0?repository_url=https://galaxy.ansible.com",
        bom_ref="pkg:generic/cisco.ios@2.0.0?repository_url=https://galaxy.ansible.com",
    )


@pytest.fixture()
def sample_bom(sample_component: Component) -> Bom:
    """Create a sample Bom for testing.

    Args:
        sample_component: A pre-built Component fixture.

    Returns:
        A Bom with one component.
    """
    return Bom(components=[sample_component])
