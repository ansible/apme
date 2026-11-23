"""Unit and integration tests for the Python Dependency Auditor (ADR-051)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.validators.dep_audit.auditor import (
    RULE_ID_CVE,
    _convert_findings,
    _find_site_packages,
    pip_audit_available,
    run_pip_audit,
)

_HAS_UV = shutil.which("uv") is not None


class TestFindSitePackages:
    """Tests for site-packages discovery in a venv."""

    def test_finds_site_packages(self, tmp_path: Path) -> None:
        """Discovers site-packages under lib/pythonX.Y/.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sp = tmp_path / "lib" / "python3.12" / "site-packages"
        sp.mkdir(parents=True)
        assert _find_site_packages(tmp_path) == sp

    def test_returns_none_no_lib(self, tmp_path: Path) -> None:
        """Returns None when lib/ doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _find_site_packages(tmp_path) is None

    def test_returns_none_no_site_packages(self, tmp_path: Path) -> None:
        """Returns None when lib/ exists but no site-packages.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "lib" / "python3.12").mkdir(parents=True)
        assert _find_site_packages(tmp_path) is None


class TestConvertFindings:
    """Tests for converting pip-audit JSON to ViolationDicts."""

    def test_basic_vulnerability(self) -> None:
        """Single vulnerability converts to ViolationDict with R200."""
        data = {
            "dependencies": [
                {
                    "name": "jmespath",
                    "version": "1.0.0",
                    "vulns": [
                        {
                            "id": "PYSEC-2025-12345",
                            "description": "Remote code execution",
                            "fix_versions": ["1.0.1"],
                            "aliases": ["CVE-2025-12345"],
                        }
                    ],
                }
            ]
        }
        violations = _convert_findings(data)
        assert len(violations) == 1
        v = violations[0]
        assert v["rule_id"] == RULE_ID_CVE
        assert "jmespath==1.0.0" in str(v["message"])
        assert "CVE-2025-12345" in str(v["message"])
        assert v["dep_package"] == "jmespath"
        assert v["dep_installed_version"] == "1.0.0"
        assert v["dep_fix_versions"] == "1.0.1"
        assert v["severity"] == "medium"

    def test_no_vulns_empty(self) -> None:
        """Package with no vulns produces no violations."""
        data = {"dependencies": [{"name": "requests", "version": "2.31.0", "vulns": []}]}
        assert _convert_findings(data) == []

    def test_invalid_data(self) -> None:
        """Non-dict input returns empty list."""
        assert _convert_findings("not a dict") == []
        assert _convert_findings(None) == []

    def test_multiple_vulns_same_package(self) -> None:
        """Multiple vulnerabilities in one package produce multiple violations."""
        data = {
            "dependencies": [
                {
                    "name": "paramiko",
                    "version": "3.4.0",
                    "vulns": [
                        {"id": "VULN-1", "description": "vuln one", "fix_versions": ["3.4.1"]},
                        {"id": "VULN-2", "description": "vuln two", "fix_versions": ["3.5.0"]},
                    ],
                }
            ]
        }
        violations = _convert_findings(data)
        assert len(violations) == 2
        assert all(v["rule_id"] == RULE_ID_CVE for v in violations)


class TestPipAuditAvailable:
    """Tests for pip-audit binary detection."""

    def test_available(self) -> None:
        """Returns True and version when pip-audit succeeds."""
        mock_result = MagicMock(returncode=0, stdout="pip-audit 2.7.0")
        with patch("apme_engine.validators.dep_audit.auditor.subprocess.run", return_value=mock_result):
            available, info = pip_audit_available()
        assert available is True
        assert "2.7.0" in info

    def test_not_found(self) -> None:
        """Returns False when pip-audit is not on PATH."""
        with patch("apme_engine.validators.dep_audit.auditor.subprocess.run", side_effect=FileNotFoundError):
            available, info = pip_audit_available()
        assert available is False
        assert "not found" in info


class TestRunPipAudit:
    """Tests for the pip-audit subprocess runner."""

    def test_no_site_packages(self, tmp_path: Path) -> None:
        """Returns empty list when venv has no site-packages.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert run_pip_audit(tmp_path) == []

    def test_subprocess_not_found(self, tmp_path: Path) -> None:
        """Returns empty list when pip-audit binary is missing.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sp = tmp_path / "lib" / "python3.12" / "site-packages"
        sp.mkdir(parents=True)
        with patch("apme_engine.validators.dep_audit.auditor.subprocess.run", side_effect=FileNotFoundError):
            assert run_pip_audit(tmp_path) == []

    def test_successful_audit(self, tmp_path: Path) -> None:
        """Parses pip-audit JSON output into violations.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sp = tmp_path / "lib" / "python3.12" / "site-packages"
        sp.mkdir(parents=True)
        audit_output = json.dumps(
            {
                "dependencies": [
                    {
                        "name": "jmespath",
                        "version": "1.0.0",
                        "vulns": [
                            {
                                "id": "PYSEC-2025-99",
                                "description": "RCE via deserialization",
                                "fix_versions": ["1.0.1"],
                                "aliases": ["CVE-2025-99"],
                            }
                        ],
                    }
                ]
            }
        )
        mock_result = MagicMock(returncode=1, stdout=audit_output, stderr="")
        with patch("apme_engine.validators.dep_audit.auditor.subprocess.run", return_value=mock_result):
            violations = run_pip_audit(tmp_path)
        assert len(violations) == 1
        assert violations[0]["rule_id"] == RULE_ID_CVE

    def test_clean_audit(self, tmp_path: Path) -> None:
        """Returns empty list when pip-audit finds no vulns.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        sp = tmp_path / "lib" / "python3.12" / "site-packages"
        sp.mkdir(parents=True)
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("apme_engine.validators.dep_audit.auditor.subprocess.run", return_value=mock_result):
            assert run_pip_audit(tmp_path) == []


class TestDepAuditServicer:
    """Tests for the dep_audit gRPC servicer."""

    async def test_validate_no_venv_path(self) -> None:
        """Returns empty violations when no venv_path is provided."""
        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        servicer = DepAuditValidatorServicer()
        request = ValidateRequest(request_id="test-1", venv_path="")
        ctx = MagicMock()
        resp = await servicer.Validate(request, ctx)
        assert len(resp.violations) == 0
        assert resp.request_id == "test-1"

    async def test_validate_with_findings(self) -> None:
        """Returns violations from pip-audit subprocess."""
        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        violations = [
            {
                "rule_id": "R200",
                "severity": "high",
                "message": "pkg==1.0 CVE-2025-1",
                "file": "",
                "line": 0,
                "path": "",
                "scope": "playbook",
                "source": "dep_audit",
            }
        ]

        servicer = DepAuditValidatorServicer()
        request = ValidateRequest(request_id="test-2", venv_path="/tmp/fakevenv")
        ctx = MagicMock()

        with patch("apme_engine.daemon.dep_audit_server._run_audit", return_value=violations):
            resp = await servicer.Validate(request, ctx)

        assert len(resp.violations) == 1
        assert resp.violations[0].rule_id == "R200"
        assert resp.HasField("diagnostics")  # type: ignore[attr-defined]
        assert resp.diagnostics.validator_name == "dep_audit"

    async def test_health_available(self) -> None:
        """Health returns ok when pip-audit is available."""
        from apme.v1.common_pb2 import HealthRequest
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        servicer = DepAuditValidatorServicer()
        ctx = MagicMock()
        with patch("apme_engine.daemon.dep_audit_server.pip_audit_available", return_value=(True, "2.7.0")):
            resp = await servicer.Health(HealthRequest(), ctx)
        assert "ok" in resp.status

    async def test_health_unavailable(self) -> None:
        """Health reports unavailable when pip-audit is missing."""
        from apme.v1.common_pb2 import HealthRequest
        from apme_engine.daemon.dep_audit_server import DepAuditValidatorServicer

        servicer = DepAuditValidatorServicer()
        ctx = MagicMock()
        with patch(
            "apme_engine.daemon.dep_audit_server.pip_audit_available",
            return_value=(False, "pip-audit binary not found"),
        ):
            resp = await servicer.Health(HealthRequest(), ctx)
        assert "not available" in resp.status


@pytest.mark.skipif(not _HAS_UV, reason="uv not available")
class TestDepAuditIntegration:
    """Integration test: real venv with a known-vulnerable package."""

    def test_detects_cve_in_real_venv(self, tmp_path: Path) -> None:
        """Install jinja2==3.1.2 (CVE-2024-22195) and verify pip-audit flags it.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        available, info = pip_audit_available()
        if not available:
            pytest.skip(f"pip-audit not available: {info}")

        venv_dir = tmp_path / "venv"
        python_target = shutil.which("python3.12") or sys.executable
        subprocess.run(  # noqa: S603, S607
            ["uv", "venv", "--python", python_target, str(venv_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603, S607
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_dir / "bin" / "python"),
                "jinja2==3.1.2",
            ],
            check=True,
            capture_output=True,
        )

        violations = run_pip_audit(venv_dir)

        assert len(violations) >= 1, "pip-audit should find at least one CVE in jinja2==3.1.2"
        assert all(v["rule_id"] == RULE_ID_CVE for v in violations)
        jinja_hits = [v for v in violations if str(v.get("dep_package", "")).lower() == "jinja2"]
        assert len(jinja_hits) >= 1, "Expected at least one Jinja2 CVE finding"
        assert jinja_hits[0]["dep_installed_version"] == "3.1.2"
        assert jinja_hits[0]["source"] == "dep_audit"
