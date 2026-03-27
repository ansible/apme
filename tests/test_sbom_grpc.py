"""Tests for GenerateSbom gRPC RPC on PrimaryServicer.

Covers: _build_component_details helper, full SBOM path, summary-only path,
and summary-only NOT_FOUND error case.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apme.v1.primary_pb2 import SbomComponentDetail, SbomResponse, ScanChunk, ScanOptions
from apme_engine.daemon.primary_server import PrimaryServicer
from apme_engine.sbom.models import (
    APME_PROPERTY_NAMESPACE,
    Component,
    ComponentType,
    Dependency,
    LicenseChoice,
    Property,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_component(
    name: str,
    version: str = "1.0.0",
    *,
    license_id: str = "",
    license_name: str = "",
    name_inferred: bool = False,
) -> Component:
    """Create a test Component with optional license and inferred-name property."""
    licenses = []
    if license_id or license_name:
        licenses.append(LicenseChoice(license_id=license_id, license_name=license_name))
    properties = []
    if name_inferred:
        properties.append(
            Property(
                name=f"{APME_PROPERTY_NAMESPACE}:name-source",
                value="inferred-from-directory",
            )
        )
    return Component(
        type=ComponentType.LIBRARY,
        name=name,
        version=version,
        purl=f"pkg:generic/{name}@{version}",
        bom_ref=f"pkg:generic/{name}@{version}",
        licenses=licenses,
        properties=properties,
    )


# ── _build_component_details tests ───────────────────────────────────


class TestBuildComponentDetails:
    """Tests for PrimaryServicer._build_component_details."""

    def test_basic_component(self):
        comp = _make_component("my-collection", "2.0.0", license_id="MIT")
        details = PrimaryServicer._build_component_details([comp], "collection")
        assert len(details) == 1
        d = details[0]
        assert d.type == "collection"
        assert d.name == "my-collection"
        assert d.version == "2.0.0"
        assert d.license == "MIT"
        assert d.name_inferred is False
        assert d.version_missing is False

    def test_name_inferred(self):
        comp = _make_component("bare-role", "1.0.0", name_inferred=True)
        details = PrimaryServicer._build_component_details([comp], "role")
        assert details[0].name_inferred is True

    def test_missing_version(self):
        comp = _make_component("no-version", "")
        details = PrimaryServicer._build_component_details([comp], "package")
        assert details[0].version_missing is True
        assert details[0].version == ""

    def test_license_name_fallback(self):
        comp = _make_component("pkg", "1.0.0", license_name="Custom License")
        details = PrimaryServicer._build_component_details([comp], "package")
        assert details[0].license == "Custom License"

    def test_license_id_preferred_over_name(self):
        comp = _make_component("pkg", "1.0.0", license_id="Apache-2.0", license_name="Apache")
        details = PrimaryServicer._build_component_details([comp], "package")
        assert details[0].license == "Apache-2.0"

    def test_no_license(self):
        comp = _make_component("pkg", "1.0.0")
        details = PrimaryServicer._build_component_details([comp], "package")
        assert details[0].license == ""

    def test_empty_list(self):
        details = PrimaryServicer._build_component_details([], "collection")
        assert details == []

    def test_multiple_components(self):
        comps = [
            _make_component("a", "1.0"),
            _make_component("b", "2.0", license_id="MIT"),
            _make_component("c", "", name_inferred=True),
        ]
        details = PrimaryServicer._build_component_details(comps, "role")
        assert len(details) == 3
        assert [d.name for d in details] == ["a", "b", "c"]


# ── GenerateSbom full path tests ─────────────────────────────────────


def _mock_accumulate_chunks(files=None, opts=None):
    """Return an async function that mimics _accumulate_chunks."""
    async def _acc(request_stream):
        return (
            files or [],
            str(uuid.uuid4()),
            "project",
            opts or ScanOptions(session_id="test-session"),
            None,
        )
    return _acc


@pytest.fixture()
def servicer():
    return PrimaryServicer()


class TestGenerateSbomFullPath:
    """Full SBOM generation: mock venv, collectors, and verify response."""

    def test_full_path_returns_complete_sbom(self, servicer):
        coll_comp = _make_component("ansible.netcommon", "5.0.0", license_id="GPL-3.0-or-later")
        pkg_comp = _make_component("requests", "2.31.0", license_id="Apache-2.0")
        role_comp = _make_component("my_role", "", name_inferred=True)

        mock_venv_session = MagicMock()
        mock_venv_session.venv_root = Path("/fake/venv")
        mock_venv_session.failed_collections = []

        async def _run():
            with (
                patch.object(servicer, "_accumulate_chunks", _mock_accumulate_chunks()),
                patch(
                    "apme_engine.daemon.primary_server._write_chunked_fs",
                    return_value=Path("/fake/tmpdir"),
                ),
                patch.object(
                    servicer,
                    "_get_venv_manager",
                    return_value=MagicMock(
                        get=MagicMock(return_value=mock_venv_session),
                        acquire=MagicMock(return_value=mock_venv_session),
                    ),
                ),
                patch(
                    "apme_engine.sbom.collect_collections",
                    return_value=([coll_comp], [Dependency(ref=coll_comp.purl)]),
                ),
                patch(
                    "apme_engine.sbom.collect_packages",
                    return_value=[pkg_comp],
                ),
                patch(
                    "apme_engine.sbom.collect_roles",
                    return_value=[role_comp],
                ),
            ):
                context = MagicMock()
                return await servicer.GenerateSbom(AsyncMock(), context)

        response = asyncio.run(_run())

        assert isinstance(response, SbomResponse)
        assert response.collection_count == 1
        assert response.package_count == 1
        assert response.role_count == 1
        assert response.total_count == 3
        assert len(response.components) == 3
        assert response.sbom_json  # non-empty bytes

        # Verify component details
        coll_detail = response.components[0]
        assert coll_detail.type == "collection"
        assert coll_detail.name == "ansible.netcommon"
        assert coll_detail.license == "GPL-3.0-or-later"

        role_detail = response.components[2]
        assert role_detail.type == "role"
        assert role_detail.name_inferred is True
        assert role_detail.version_missing is True


class TestGenerateSbomSummaryOnly:
    """Summary-only mode tests."""

    def test_summary_only_with_existing_venv(self, servicer):
        """Summary-only with existing venv should succeed."""
        coll_comp = _make_component("ansible.utils", "3.0.0")

        mock_venv_root = MagicMock(spec=Path)
        mock_venv_root.is_dir.return_value = True
        mock_venv_session = MagicMock()
        mock_venv_session.venv_root = mock_venv_root

        opts = ScanOptions(session_id="test-session", summary_only=True)

        async def _run():
            with (
                patch.object(servicer, "_accumulate_chunks", _mock_accumulate_chunks(opts=opts)),
                patch.object(
                    servicer,
                    "_get_venv_manager",
                    return_value=MagicMock(get=MagicMock(return_value=mock_venv_session)),
                ),
                patch(
                    "apme_engine.sbom.collect_collections",
                    return_value=([coll_comp], []),
                ),
                patch(
                    "apme_engine.sbom.collect_packages",
                    return_value=[],
                ),
                patch(
                    "apme_engine.sbom.collect_roles",
                    return_value=[],
                ),
                patch("tempfile.mkdtemp", return_value="/fake/empty"),
            ):
                context = MagicMock()
                return await servicer.GenerateSbom(AsyncMock(), context)

        response = asyncio.run(_run())

        assert response.collection_count == 1
        assert response.package_count == 0
        assert response.role_count == 0
        assert response.total_count == 1

    def test_summary_only_no_venv_returns_not_found(self, servicer):
        """Summary-only without existing venv should abort with NOT_FOUND."""
        opts = ScanOptions(session_id="test-session", summary_only=True)

        async def _run():
            with (
                patch.object(servicer, "_accumulate_chunks", _mock_accumulate_chunks(opts=opts)),
                patch.object(
                    servicer,
                    "_get_venv_manager",
                    return_value=MagicMock(get=MagicMock(return_value=None)),
                ),
            ):
                context = AsyncMock()
                await servicer.GenerateSbom(AsyncMock(), context)
                return context

        context = asyncio.run(_run())

        context.abort.assert_awaited_once()
        call_args = context.abort.call_args
        assert call_args[0][0].name == "NOT_FOUND" or "NOT_FOUND" in str(call_args)
