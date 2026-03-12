"""Unit tests for the gitleaks scanner wrapper and async gRPC servicer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from apme_engine.validators.gitleaks.scanner import (
    RULE_PREFIX,
    _build_rule_id,
    _convert_findings,
    _is_vault_encrypted,
    _value_is_jinja,
    run_gitleaks,
)


class TestVaultDetection:
    def test_vault_header_detected(self):
        assert _is_vault_encrypted("  $ANSIBLE_VAULT;1.1;AES256\ndeadbeef")

    def test_plain_content_not_vault(self):
        assert not _is_vault_encrypted("password: s3cret")

    def test_empty_string(self):
        assert not _is_vault_encrypted("")


class TestJinjaFiltering:
    def test_jinja_expression(self):
        assert _value_is_jinja("{{ vault_password }}")

    def test_quoted_jinja(self):
        assert _value_is_jinja('\'{{ lookup("env", "SECRET") }}\'')

    def test_literal_value(self):
        assert not _value_is_jinja("hardcoded_secret_123")

    def test_mixed_not_full_jinja(self):
        assert not _value_is_jinja("prefix-{{ var }}-suffix")


class TestRuleIdMapping:
    def test_unmapped_rule(self):
        assert _build_rule_id("aws-access-key-id") == f"{RULE_PREFIX}:aws-access-key-id"

    def test_mapped_rule(self):
        with patch.dict("apme_engine.validators.gitleaks.scanner.RULE_ID_MAP", {"generic-api-key": "SEC001"}):
            assert _build_rule_id("generic-api-key") == "SEC001"


class TestConvertFindings:
    def test_basic_finding(self, tmp_path):
        secret_file = tmp_path / "vars.yml"
        secret_file.write_text("password: s3cret123\n")

        findings = [
            {
                "RuleID": "generic-api-key",
                "Description": "Generic API Key",
                "File": str(secret_file),
                "StartLine": 1,
                "EndLine": 1,
                "Match": "password: s3cret123",
            }
        ]
        violations = _convert_findings(findings, tmp_path)
        assert len(violations) == 1
        assert violations[0]["rule_id"] == f"{RULE_PREFIX}:generic-api-key"
        assert violations[0]["level"] == "error"
        assert violations[0]["file"] == "vars.yml"
        assert violations[0]["line"] == 1

    def test_jinja_value_filtered(self, tmp_path):
        jinja_file = tmp_path / "vars.yml"
        jinja_file.write_text("password: '{{ vault_pw }}'\n")

        findings = [
            {
                "RuleID": "generic-api-key",
                "Description": "Generic API Key",
                "File": str(jinja_file),
                "StartLine": 1,
                "EndLine": 1,
                "Match": "{{ vault_pw }}",
            }
        ]
        violations = _convert_findings(findings, tmp_path)
        assert len(violations) == 0

    def test_vault_encrypted_filtered(self, tmp_path):
        vault_file = tmp_path / "secrets.yml"
        vault_file.write_text("$ANSIBLE_VAULT;1.1;AES256\ndeadbeef\n")

        findings = [
            {
                "RuleID": "generic-api-key",
                "Description": "API Key",
                "File": str(vault_file),
                "StartLine": 2,
                "EndLine": 2,
                "Match": "deadbeef",
            }
        ]
        violations = _convert_findings(findings, tmp_path)
        assert len(violations) == 0

    def test_multiline_range(self, tmp_path):
        f = tmp_path / "key.pem"
        f.write_text("-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n")

        findings = [
            {
                "RuleID": "private-key",
                "Description": "Private Key",
                "File": str(f),
                "StartLine": 1,
                "EndLine": 3,
                "Match": "-----BEGIN RSA PRIVATE KEY-----",
            }
        ]
        violations = _convert_findings(findings, tmp_path)
        assert len(violations) == 1
        assert violations[0]["line"] == [1, 3]


class TestRunGitleaks:
    def test_binary_not_found(self, tmp_path):
        with patch("apme_engine.validators.gitleaks.scanner.GITLEAKS_BIN", "/nonexistent/gitleaks"):
            result = run_gitleaks(tmp_path)
        assert result == []

    def test_successful_scan_no_findings(self, tmp_path):
        clean = tmp_path / "clean.yml"
        clean.write_text("---\n- name: Clean play\n  hosts: all\n  tasks: []\n")

        report = tmp_path / "report.json"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with (
            patch("apme_engine.validators.gitleaks.scanner.subprocess.run", return_value=mock_proc),
            patch("apme_engine.validators.gitleaks.scanner.tempfile.NamedTemporaryFile") as mock_tmp,
        ):
            mock_tmp.return_value.__enter__ = lambda s: s
            mock_tmp.return_value.__exit__ = lambda s, *a: None
            mock_tmp.return_value.name = str(report)
            report.write_text("[]")
            result = run_gitleaks(tmp_path)

        assert result == []

    def test_successful_scan_with_findings(self, tmp_path):
        secret_file = tmp_path / "vars.yml"
        secret_file.write_text("api_key: AKIAIOSFODNN7EXAMPLE\n")

        finding_data = json.dumps(
            [
                {
                    "RuleID": "aws-access-key-id",
                    "Description": "AWS Access Key ID",
                    "File": str(secret_file),
                    "StartLine": 1,
                    "EndLine": 1,
                    "Match": "AKIAIOSFODNN7EXAMPLE",
                }
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        report_file = tmp_path / "report.json"

        with (
            patch("apme_engine.validators.gitleaks.scanner.subprocess.run", return_value=mock_proc),
            patch("apme_engine.validators.gitleaks.scanner.tempfile.NamedTemporaryFile") as mock_tmp,
        ):
            mock_tmp.return_value.__enter__ = lambda s: s
            mock_tmp.return_value.__exit__ = lambda s, *a: None
            mock_tmp.return_value.name = str(report_file)
            report_file.write_text(finding_data)
            result = run_gitleaks(tmp_path)

        assert len(result) == 1
        assert result[0]["rule_id"] == f"{RULE_PREFIX}:aws-access-key-id"
        assert result[0]["file"] == "vars.yml"

    def test_timeout_handled(self, tmp_path):
        import subprocess as sp

        with patch(
            "apme_engine.validators.gitleaks.scanner.subprocess.run", side_effect=sp.TimeoutExpired("gitleaks", 120)
        ):
            result = run_gitleaks(tmp_path)
        assert result == []


class TestGitleaksServicer:
    """Test the async gRPC servicer layer."""

    async def test_validate_no_files(self):
        from apme.v1 import validate_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        request = validate_pb2.ValidateRequest(files=[], request_id="gl-1")
        resp = await servicer.Validate(request, None)
        assert len(resp.violations) == 0
        assert resp.request_id == "gl-1"

    async def test_validate_with_files(self):
        from apme.v1 import common_pb2, validate_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()

        fake_violations = [
            {
                "rule_id": "SEC:aws-access-key-id",
                "level": "error",
                "message": "AWS Key",
                "file": "vars.yml",
                "line": 1,
                "path": "",
            }
        ]

        request = validate_pb2.ValidateRequest(
            request_id="gl-2", files=[common_pb2.File(path="vars.yml", content=b"api_key: AKIAIOSFODNN7EXAMPLE\n")]
        )

        with patch("apme_engine.daemon.gitleaks_validator_server.run_gitleaks", return_value=fake_violations):
            resp = await servicer.Validate(request, None)

        assert len(resp.violations) == 1
        assert resp.violations[0].rule_id == "SEC:aws-access-key-id"
        assert resp.request_id == "gl-2"

    async def test_health_binary_present(self):
        from apme.v1 import common_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"8.18.0", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            resp = await servicer.Health(common_pb2.HealthRequest(), None)
        assert "ok" in resp.status
        assert "8.18.0" in resp.status

    async def test_health_binary_missing(self):
        from apme.v1 import common_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError):
            resp = await servicer.Health(common_pb2.HealthRequest(), None)
        assert "not found" in resp.status
