"""Tests for Pydantic schemas — validation and serialization."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

if not importlib.util.find_spec("sqlalchemy"):
    pytest.skip("gateway extras not installed", allow_module_level=True)

from apme_gateway.models.schemas import (
    HealthOut,
    PaginatedResponse,
    ScanCreate,
    ScanDetail,
    ScanIngest,
    ScanSummary,
    ServiceHealth,
    ViolationIngest,
    ViolationOut,
)


class TestScanCreate:
    """Tests for the ScanCreate schema."""

    def test_minimal(self) -> None:
        """Only project_name is required."""
        sc = ScanCreate(project_name="test")
        assert sc.project_name == "test"
        assert sc.files == {}
        assert sc.ansible_core_version == ""
        assert sc.collection_specs == []

    def test_full(self) -> None:
        """All fields are accepted."""
        sc = ScanCreate(
            project_name="full",
            files={"site.yml": "---"},
            ansible_core_version="2.18",
            collection_specs=["community.general"],
        )
        assert sc.files == {"site.yml": "---"}
        assert sc.ansible_core_version == "2.18"


class TestScanIngest:
    """Tests for the ScanIngest schema."""

    def test_defaults(self) -> None:
        """Default values are sensible for a simple ingest."""
        si = ScanIngest(project_name="p")
        assert si.scan_type == "scan"
        assert si.status == "completed"
        assert si.violations == []
        assert si.fixed_count == 0
        assert si.request_id is None

    def test_fix_type(self) -> None:
        """Fix-type ingest with fixed_count."""
        si = ScanIngest(project_name="p", scan_type="fix", fixed_count=10)
        assert si.scan_type == "fix"
        assert si.fixed_count == 10

    def test_with_violations(self) -> None:
        """Violations are properly deserialized."""
        si = ScanIngest(
            project_name="p",
            violations=[
                ViolationIngest(rule_id="L002", level="error", message="bad", file="a.yml"),
            ],
        )
        assert len(si.violations) == 1
        assert si.violations[0].rule_id == "L002"


class TestScanSummary:
    """Tests for the ScanSummary response schema."""

    def test_serialization(self) -> None:
        """ScanSummary serializes all expected fields."""
        now = datetime.now(timezone.utc)
        ss = ScanSummary(
            id="abc",
            project_name="test",
            created_at=now,
            status="completed",
            total_violations=5,
            error_count=2,
            warning_count=3,
            scan_type="fix",
            fixed_count=4,
        )
        data = ss.model_dump()
        assert data["scan_type"] == "fix"
        assert data["fixed_count"] == 4
        assert data["total_violations"] == 5


class TestScanDetail:
    """Tests for the ScanDetail response schema."""

    def test_includes_violations(self) -> None:
        """ScanDetail includes a violations list."""
        sd = ScanDetail(
            id="x",
            project_name="p",
            created_at=datetime.now(timezone.utc),
            status="completed",
            total_violations=1,
            violations=[
                ViolationOut(id="v1", rule_id="L002", level="error", message="bad", file="a.yml"),
            ],
        )
        assert len(sd.violations) == 1


class TestHealthSchemas:
    """Tests for health-related schemas."""

    def test_health_out_ok(self) -> None:
        """HealthOut with all ok services."""
        h = HealthOut(
            status="ok",
            services=[
                ServiceHealth(name="primary", status="ok", latency_ms=1.0),
            ],
        )
        assert h.status == "ok"
        assert len(h.services) == 1

    def test_paginated_response(self) -> None:
        """PaginatedResponse wraps items correctly."""
        pr = PaginatedResponse[ScanSummary](
            items=[],
            total=0,
            page=1,
            page_size=20,
        )
        assert pr.items == []
        assert pr.total == 0
