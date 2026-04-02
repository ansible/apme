"""Tests for validator abstraction (ScanContext, OpaValidator)."""

from pathlib import Path
from typing import cast

from apme_engine.engine.models import YAMLDict
from apme_engine.validators.base import ScanContext
from apme_engine.validators.opa import OpaValidator


class TestScanContext:
    """Tests for ScanContext."""

    def test_scan_context_defaults(self) -> None:
        """ScanContext defaults scandata to None and root_dir to empty."""
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, {"scan_id": "x"}))
        assert ctx.hierarchy_payload["scan_id"] == "x"
        assert ctx.scandata is None
        assert ctx.root_dir == ""

    def test_scan_context_with_scandata(self) -> None:
        """ScanContext stores scandata and root_dir."""
        mock = object()
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, {}), scandata=mock, root_dir="/tmp")
        assert ctx.scandata is mock
        assert ctx.root_dir == "/tmp"


class TestOpaValidator:
    """Tests for OpaValidator."""

    def test_opa_validator_run_calls_run_opa(
        self, opa_bundle_path: Path, sample_hierarchy_payload: dict[str, object]
    ) -> None:
        """OpaValidator.run calls run_opa with hierarchy payload and bundle path.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        from unittest.mock import patch

        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(opa_bundle_path))
        with patch("apme_engine.validators.opa.run_opa", return_value=[]) as mock_opa:
            result = v.run(ctx)
        mock_opa.assert_called_once()
        assert mock_opa.call_args[0][0] == sample_hierarchy_payload
        assert mock_opa.call_args[0][1] == str(opa_bundle_path)
        assert result == []

    def test_opa_validator_run_returns_violations(
        self, sample_hierarchy_payload: dict[str, object], tmp_path: Path
    ) -> None:
        """OpaValidator.run returns violations from run_opa.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            tmp_path: Pytest temporary directory fixture.

        """
        from unittest.mock import patch

        (tmp_path / "bundle").mkdir()
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(tmp_path / "bundle"))
        violations = [{"rule_id": "r1", "severity": "high", "message": "msg", "file": "f", "line": 1, "path": "p"}]
        with patch("apme_engine.validators.opa.run_opa", return_value=violations):
            result = v.run(ctx)
        assert result == violations
