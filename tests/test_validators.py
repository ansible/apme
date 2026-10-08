"""Tests for validator abstraction (ScanContext, OpaValidator)."""

import shutil
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.opa_client import OpaInfrastructureError
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
        """OpaValidator.run maps run_opa output through unchanged.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(opa_bundle_path))
        with patch("apme_engine.validators.opa.run_opa", return_value=[]) as mock_opa:
            result = v.run(ctx)
        assert mock_opa.call_args[0][0] == sample_hierarchy_payload
        assert mock_opa.call_args[0][1] == str(opa_bundle_path)
        assert result == []

    def test_opa_validator_run_returns_violations(
        self, sample_hierarchy_payload: dict[str, object], tmp_path: Path
    ) -> None:
        """OpaValidator.run returns violations from run_opa with contents intact.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            tmp_path: Pytest temporary directory fixture.

        """
        (tmp_path / "bundle").mkdir()
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(tmp_path / "bundle"))
        violations = [{"rule_id": "r1", "severity": "high", "message": "msg", "file": "f", "line": 1, "path": "p"}]
        with patch("apme_engine.validators.opa.run_opa", return_value=violations):
            result = v.run(ctx)
        # Assert mapped outputs (violation contents), not just call counts.
        assert len(result) == 1
        assert result[0]["rule_id"] == "r1"
        assert result[0]["severity"] == "high"
        assert result[0]["message"] == "msg"
        assert result[0]["file"] == "f"
        assert result[0]["line"] == 1
        assert result[0]["path"] == "p"

    def test_opa_validator_run_propagates_infra_error(
        self, sample_hierarchy_payload: dict[str, object], tmp_path: Path
    ) -> None:
        """run_opa infrastructure failures propagate as OpaInfrastructureError.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            tmp_path: Pytest temporary directory fixture.

        """
        (tmp_path / "bundle").mkdir()
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(tmp_path / "bundle"))
        with (
            patch(
                "apme_engine.validators.opa.run_opa",
                side_effect=OpaInfrastructureError("OPA binary is not available"),
            ),
            pytest.raises(OpaInfrastructureError, match="not available"),
        ):
            v.run(ctx)

    def test_opa_validator_run_propagates_missing_bundle(
        self, sample_hierarchy_payload: dict[str, object], tmp_path: Path
    ) -> None:
        """Missing bundle path surfaces FileNotFoundError for daemon R902 mapping.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            tmp_path: Pytest temporary directory fixture.

        """
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, sample_hierarchy_payload))
        v = OpaValidator(str(tmp_path / "does-not-exist"))
        with (
            patch(
                "apme_engine.validators.opa.run_opa",
                side_effect=FileNotFoundError("OPA bundle path is not a directory"),
            ),
            pytest.raises(FileNotFoundError, match="not a directory"),
        ):
            v.run(ctx)

    def test_opa_daemon_maps_run_opa_raise_to_infra_error(self, sample_hierarchy_payload: dict[str, object]) -> None:
        """OPA daemon maps run_opa raises to an R902 infra violation, not empty.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
        """
        import asyncio
        import json

        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        request = ValidateRequest(
            request_id="req-opa-raise",
            hierarchy_payload=json.dumps(sample_hierarchy_payload).encode(),
        )
        servicer = OpaValidatorServicer()
        with patch(
            "apme_engine.daemon.opa_validator_server._run_opa",
            side_effect=OpaInfrastructureError("boom"),
        ):
            response = asyncio.run(servicer.Validate(request, None))  # type: ignore[arg-type]
        assert len(response.violations) == 1  # type: ignore[attr-defined]
        assert response.violations[0].rule_id == "R902"  # type: ignore[attr-defined]
        assert response.request_id == "req-opa-raise"  # type: ignore[attr-defined]

    def test_opa_daemon_maps_malformed_payload_to_infra_error(self) -> None:
        """Malformed hierarchy_payload JSON maps to R902 instead of raising."""
        import asyncio

        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        request = ValidateRequest(request_id="req-bad-json", hierarchy_payload=b"{not-json")
        servicer = OpaValidatorServicer()
        response = asyncio.run(servicer.Validate(request, None))  # type: ignore[arg-type]
        assert len(response.violations) == 1  # type: ignore[attr-defined]
        assert response.violations[0].rule_id == "R902"  # type: ignore[attr-defined]

    def test_native_run_without_mocks_clean_debug_task(self, tmp_path: Path) -> None:
        """Native graph scan of a violation-free debug task returns exactly [].

        The fixture is intentionally clean (fully-qualified debug module,
        plain-text msg, no sensitive vars) so all 90+ default GraphRules pass.
        Assert exact emptiness — not a truthiness loop — so any new rule that
        fires on this task fails loudly here instead of passing vacuously.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        import json

        from apme_engine.daemon.native_validator_server import _run_graph
        from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

        graph = ContentGraph()
        node = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=1,
            line_end=3,
            name="x",
            module="ansible.builtin.debug",
            yaml_lines="- name: x\n  ansible.builtin.debug:\n    msg: hi\n",
        )
        graph.add_node(node)
        raw = json.dumps(graph.to_dict()).encode()
        _ = tmp_path  # tmp fixture ensures isolated cwd-independent execution
        result = _run_graph(raw)
        assert result.violations == []

    def test_ansible_run_without_mocks_tmp_fixture(self, tmp_path: Path) -> None:
        """AnsibleValidator without mocks reports missing venv binary contents.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible import AnsibleValidator

        venv_root = tmp_path / "venv"
        venv_root.mkdir()
        project = tmp_path / "proj"
        project.mkdir()
        (project / "site.yml").write_text("- hosts: all\n  tasks: []\n")
        ctx = ScanContext(hierarchy_payload=cast(YAMLDict, {"hierarchy": []}), root_dir=str(project))
        validator = AnsibleValidator(venv_root=venv_root)
        result = validator.run_with_timing(ctx)
        assert len(result.violations) >= 1
        first = result.violations[0]
        assert first.get("rule_id") == "L057"
        assert "ansible-playbook not found" in str(first.get("message"))
        assert first.get("severity") == "error"
        assert first.get("line") == 1

    def test_violation_dict_to_proto_maps_malformed_line(self) -> None:
        """Malformed line values do not raise; contents map to proto defaults."""
        from apme_engine.daemon.violation_convert import violation_dict_to_proto
        from apme_engine.engine.models import ViolationDict

        malformed = cast(
            ViolationDict,
            {
                "rule_id": "L001",
                "severity": "warning",
                "message": "bad line",
                "file": "site.yml",
                "line": "not-a-number",
                "path": "site.yml/plays[0]",
            },
        )
        proto = violation_dict_to_proto(malformed)
        assert proto.rule_id == "L001"
        assert proto.file == "site.yml"
        assert proto.message == "bad line"
        # Malformed line falls back to unset (0) rather than raising.
        assert proto.line == 0
        assert not proto.HasField("line_range")


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_opa_integration_real_binary(opa_bundle_path: Path, sample_hierarchy_payload: dict[str, object]) -> None:
    """Real OPA binary evaluation returns mapped violations (skipped if missing).

    Args:
        opa_bundle_path: Fixture providing path to OPA bundle.
        sample_hierarchy_payload: Fixture providing sample hierarchy data.
    """
    from apme_engine.opa_client import run_opa

    if shutil.which("opa") is None:
        pytest.skip("opa binary not on PATH")
    violations = run_opa(
        cast(YAMLDict, sample_hierarchy_payload),
        str(opa_bundle_path),
    )
    assert isinstance(violations, list)
    for v in violations:
        assert v.get("rule_id")
        assert "message" in v
