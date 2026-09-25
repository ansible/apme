"""Unit tests for the gitleaks scanner wrapper and async gRPC servicer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from apme_engine.engine.models import ViolationDict
from apme_engine.validators.gitleaks.scanner import (
    RULE_PREFIX,
    _build_rule_id,
    _build_stdin_payload,
    _convert_findings,
    _is_vault_encrypted,
    _resolve_node_id,
    _value_is_jinja,
    run_gitleaks,
    run_gitleaks_nodes,
)


class TestVaultDetection:
    """Tests for Ansible Vault detection."""

    def test_vault_header_detected(self) -> None:
        """Content with $ANSIBLE_VAULT header is detected as vault-encrypted."""
        assert _is_vault_encrypted("  $ANSIBLE_VAULT;1.1;AES256\ndeadbeef")

    def test_plain_content_not_vault(self) -> None:
        """Plain text content is not detected as vault."""
        assert not _is_vault_encrypted("password: s3cret")

    def test_empty_string(self) -> None:
        """Empty string is not vault-encrypted."""
        assert not _is_vault_encrypted("")


class TestJinjaFiltering:
    """Tests for Jinja expression filtering."""

    def test_jinja_expression(self) -> None:
        """Jinja variable expression is detected."""
        assert _value_is_jinja("{{ vault_password }}")

    def test_quoted_jinja(self) -> None:
        """Quoted Jinja lookup is detected."""
        assert _value_is_jinja('\'{{ lookup("env", "SECRET") }}\'')

    def test_literal_value(self) -> None:
        """Literal value is not detected as Jinja."""
        assert not _value_is_jinja("hardcoded_secret_123")

    def test_mixed_not_full_jinja(self) -> None:
        """Mixed literal with Jinja is not full Jinja."""
        assert not _value_is_jinja("prefix-{{ var }}-suffix")


class TestRuleIdMapping:
    """Tests for gitleaks rule ID mapping."""

    def test_unmapped_rule(self) -> None:
        """Unmapped rule gets RULE_PREFIX prefix."""
        assert _build_rule_id("aws-access-key-id") == f"{RULE_PREFIX}:aws-access-key-id"

    def test_mapped_rule(self) -> None:
        """Mapped rule uses RULE_ID_MAP value."""
        with patch.dict("apme_engine.validators.gitleaks.scanner.RULE_ID_MAP", {"generic-api-key": "SEC001"}):
            assert _build_rule_id("generic-api-key") == "SEC001"


class TestConvertFindings:
    """Tests for converting gitleaks findings to violations."""

    def test_basic_finding(self, tmp_path: Path) -> None:
        """Basic finding is converted to violation dict.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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
        assert violations[0]["severity"] == "critical"
        assert violations[0]["file"] == "vars.yml"
        assert violations[0]["line"] == 1

    def test_jinja_value_filtered(self, tmp_path: Path) -> None:
        """Findings in Jinja values are filtered out.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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

    def test_vault_encrypted_filtered(self, tmp_path: Path) -> None:
        """Findings in vault-encrypted files are filtered out.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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

    def test_multiline_range(self, tmp_path: Path) -> None:
        """Multiline findings use list for line range.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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
    """Tests for run_gitleaks subprocess wrapper."""

    def test_binary_not_found(self, tmp_path: Path) -> None:
        """When gitleaks binary not found, returns empty list.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        with patch("apme_engine.validators.gitleaks.scanner.GITLEAKS_BIN", "/nonexistent/gitleaks"):
            result = run_gitleaks(tmp_path)
        assert result == []

    def test_successful_scan_no_findings(self, tmp_path: Path) -> None:
        """Successful scan with no findings returns empty list.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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

    def test_successful_scan_with_findings(self, tmp_path: Path) -> None:
        """Successful scan with findings returns violation list.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
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

    def test_timeout_handled(self, tmp_path: Path) -> None:
        """TimeoutExpired returns empty list.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        import subprocess as sp

        with patch(
            "apme_engine.validators.gitleaks.scanner.subprocess.run", side_effect=sp.TimeoutExpired("gitleaks", 120)
        ):
            result = run_gitleaks(tmp_path)
        assert result == []


class TestGitleaksServicer:
    """Test the async gRPC servicer layer."""

    async def test_validate_no_files(self) -> None:
        """Validate with empty files returns empty violations."""
        from apme.v1 import validate_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        request = validate_pb2.ValidateRequest(files=[], request_id="gl-1")
        resp = await servicer.Validate(request, None)  # type: ignore[arg-type]
        assert len(resp.violations) == 0  # type: ignore[attr-defined]
        assert resp.request_id == "gl-1"  # type: ignore[attr-defined]

    async def test_validate_with_files(self) -> None:
        """Validate with file content returns violations from gitleaks."""
        from apme.v1 import common_pb2, validate_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()

        fake_violations: list[ViolationDict] = [
            {
                "rule_id": "SEC:aws-access-key-id",
                "severity": "critical",
                "message": "AWS Key",
                "file": "",
                "line": 1,
                "path": "vars.yml",
            }
        ]

        request = validate_pb2.ValidateRequest(
            request_id="gl-2", files=[common_pb2.File(path="vars.yml", content=b"api_key: AKIAIOSFODNN7EXAMPLE\n")]
        )

        with patch("apme_engine.daemon.gitleaks_validator_server.run_gitleaks_nodes", return_value=fake_violations):
            resp = await servicer.Validate(request, None)  # type: ignore[arg-type]

        assert len(resp.violations) == 1  # type: ignore[attr-defined]
        assert resp.violations[0].rule_id == "SEC:aws-access-key-id"  # type: ignore[attr-defined]
        assert resp.request_id == "gl-2"  # type: ignore[attr-defined]

    async def test_health_binary_present(self) -> None:
        """Health with gitleaks binary returns ok and version."""
        from apme.v1 import common_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"8.18.0", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            resp = await servicer.Health(common_pb2.HealthRequest(), None)  # type: ignore[arg-type]
        assert "ok" in resp.status
        assert "8.18.0" in resp.status

    async def test_health_binary_missing(self) -> None:
        """Health when binary not found returns not found status."""
        from apme.v1 import common_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError):
            resp = await servicer.Health(common_pb2.HealthRequest(), None)  # type: ignore[arg-type]
        assert "not found" in resp.status


# ---------------------------------------------------------------------------
# Tests for _extract_nodes_from_graph_data and _run_scan coverage
# ---------------------------------------------------------------------------


class TestExtractNodesFromGraphData:
    """Tests for ``_extract_nodes_from_graph_data`` node extraction and covered paths."""

    def test_returns_nodes_and_covered_paths(self) -> None:
        """Nodes with yaml_lines are extracted; their file_path is tracked."""
        from apme_engine.daemon.gitleaks_validator_server import _extract_nodes_from_graph_data

        graph = {
            "version": 1,
            "nodes": [
                {"id": "site.yml/plays[0]/tasks[0]", "data": {"yaml_lines": "- name: foo\n", "file_path": "site.yml"}},
                {"id": "site.yml/plays[0]", "data": {"yaml_lines": "", "file_path": "site.yml"}},
                {"id": "vars/main.yml", "data": {"yaml_lines": "", "file_path": "vars/main.yml"}},
            ],
            "edges": [],
        }
        nodes, covered = _extract_nodes_from_graph_data(json.dumps(graph).encode())
        assert len(nodes) == 1
        assert nodes[0] == ("site.yml/plays[0]/tasks[0]", "- name: foo\n")
        assert covered == {"site.yml"}

    def test_empty_input(self) -> None:
        """Empty bytes returns empty results."""
        from apme_engine.daemon.gitleaks_validator_server import _extract_nodes_from_graph_data

        nodes, covered = _extract_nodes_from_graph_data(b"")
        assert nodes == []
        assert covered == set()

    def test_invalid_json(self) -> None:
        """Invalid JSON returns empty results."""
        from apme_engine.daemon.gitleaks_validator_server import _extract_nodes_from_graph_data

        nodes, covered = _extract_nodes_from_graph_data(b"not json")
        assert nodes == []
        assert covered == set()


class TestRunScanCoverage:
    """Tests for ``_run_scan`` combining graph nodes with uncovered files."""

    def test_uncovered_files_included(self) -> None:
        """Files not covered by graph nodes are included in the scan."""
        from apme.v1.common_pb2 import File
        from apme_engine.daemon.gitleaks_validator_server import _run_scan

        graph = {
            "version": 1,
            "nodes": [
                {"id": "play.yml/plays[0]/tasks[0]", "data": {"yaml_lines": "- debug:\n", "file_path": "play.yml"}},
            ],
            "edges": [],
        }
        files = [
            File(path="play.yml", content=b"---\n- hosts: all\n  tasks:\n    - debug:\n"),
            File(path="vars/secrets.yml", content=b"api_key: AKIAIOSFODNN7EXAMPLE\n"),
        ]

        with patch("apme_engine.daemon.gitleaks_validator_server.run_gitleaks_nodes") as mock_scan:
            mock_scan.return_value = []
            _run_scan(json.dumps(graph).encode(), files)

            called_nodes = mock_scan.call_args[0][0]

        assert len(called_nodes) == 2
        node_keys = {n[0] for n in called_nodes}
        assert "play.yml/plays[0]/tasks[0]" in node_keys
        assert "vars/secrets.yml" in node_keys

    def test_covered_files_excluded(self) -> None:
        """Files whose path matches a graph node's file_path are excluded."""
        from apme.v1.common_pb2 import File
        from apme_engine.daemon.gitleaks_validator_server import _run_scan

        graph = {
            "version": 1,
            "nodes": [
                {"id": "play.yml/plays[0]/tasks[0]", "data": {"yaml_lines": "- debug:\n", "file_path": "play.yml"}},
            ],
            "edges": [],
        }
        files = [
            File(path="play.yml", content=b"---\n- hosts: all\n  tasks:\n    - debug:\n"),
        ]

        with patch("apme_engine.daemon.gitleaks_validator_server.run_gitleaks_nodes") as mock_scan:
            mock_scan.return_value = []
            _run_scan(json.dumps(graph).encode(), files)

            called_nodes = mock_scan.call_args[0][0]

        assert len(called_nodes) == 1
        assert called_nodes[0][0] == "play.yml/plays[0]/tasks[0]"

    def test_no_graph_data_uses_all_files(self) -> None:
        """Without graph data all files are scanned as file-keyed nodes."""
        from apme.v1.common_pb2 import File
        from apme_engine.daemon.gitleaks_validator_server import _run_scan

        files = [
            File(path="a.yml", content=b"key: val\n"),
            File(path="b.yml", content=b"other: val\n"),
        ]

        with patch("apme_engine.daemon.gitleaks_validator_server.run_gitleaks_nodes") as mock_scan:
            mock_scan.return_value = []
            _run_scan(b"", files)

            called_nodes = mock_scan.call_args[0][0]

        assert len(called_nodes) == 2
        assert {n[0] for n in called_nodes} == {"a.yml", "b.yml"}


# ---------------------------------------------------------------------------
# Tests for stdin-based node scanner (run_gitleaks_nodes)
# ---------------------------------------------------------------------------


class TestBuildStdinPayload:
    """Tests for ``_build_stdin_payload`` delimiter generation."""

    def test_single_node(self) -> None:
        """Single node produces delimiter + content."""
        nodes = [("node-1", "password: s3cret\n")]
        text, delims, ids, content_map = _build_stdin_payload(nodes)

        assert "# __apme_node__ node-1\n" in text
        assert "password: s3cret\n" in text
        assert len(delims) == 1
        assert ids == ["node-1"]
        assert content_map["node-1"] == "password: s3cret\n"

    def test_multiple_nodes(self) -> None:
        """Multiple nodes each get their own delimiter line."""
        nodes = [("a", "line1\n"), ("b", "line2\n"), ("c", "line3\n")]
        text, delims, ids, _ = _build_stdin_payload(nodes)

        assert len(delims) == 3
        assert ids == ["a", "b", "c"]
        assert text.count("# __apme_node__ ") == 3

    def test_delimiter_lines_ascending(self) -> None:
        """Delimiter lines increase monotonically."""
        nodes = [("a", "l1\nl2\n"), ("b", "l3\n")]
        _, delims, _, _ = _build_stdin_payload(nodes)

        assert delims[0] < delims[1]

    def test_content_without_trailing_newline(self) -> None:
        """Content without trailing newline gets one appended."""
        nodes = [("x", "no-newline")]
        text, _, _, _ = _build_stdin_payload(nodes)

        assert text.endswith("no-newline\n")


class TestResolveNodeId:
    """Tests for ``_resolve_node_id`` line-to-delimiter mapping."""

    def test_line_in_first_node(self) -> None:
        """Finding in first node resolves correctly."""
        delims = [1, 4]
        ids = ["node-a", "node-b"]
        assert _resolve_node_id(2, delims, ids) == ("node-a", 1)

    def test_line_in_second_node(self) -> None:
        """Finding in second node resolves correctly."""
        delims = [1, 4]
        ids = ["node-a", "node-b"]
        assert _resolve_node_id(5, delims, ids) == ("node-b", 4)

    def test_line_on_delimiter(self) -> None:
        """Finding exactly on delimiter resolves to that node."""
        delims = [1, 4]
        ids = ["node-a", "node-b"]
        assert _resolve_node_id(4, delims, ids) == ("node-b", 4)

    def test_line_before_any_delimiter(self) -> None:
        """Finding before first delimiter returns empty."""
        delims = [5]
        ids = ["node-a"]
        assert _resolve_node_id(1, delims, ids) == ("", 0)


class TestRunGitleaksNodes:
    """Tests for ``run_gitleaks_nodes`` stdin piping."""

    def test_empty_nodes(self) -> None:
        """Empty input returns no violations."""
        assert run_gitleaks_nodes([]) == []

    def test_binary_not_found(self) -> None:
        """Missing gitleaks binary returns empty list."""
        with patch("apme_engine.validators.gitleaks.scanner.GITLEAKS_BIN", "/nonexistent/gitleaks"):
            result = run_gitleaks_nodes([("n1", "secret: abc")])
        assert result == []

    def test_findings_attributed_to_nodes(self) -> None:
        """Findings are correctly attributed to node_ids via delimiter lines."""
        nodes = [("node-A", "clean\n"), ("node-B", "api_key: AKIA12345\n")]
        payload, delims, ids, _ = _build_stdin_payload(nodes)

        finding_line = delims[1] + 1
        findings_json = json.dumps(
            [
                {
                    "RuleID": "aws-access-key-id",
                    "Description": "AWS Key",
                    "File": "",
                    "StartLine": finding_line,
                    "EndLine": finding_line,
                    "Match": "AKIA12345",
                }
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = findings_json
        mock_proc.stderr = ""

        with patch("apme_engine.validators.gitleaks.scanner.subprocess.run", return_value=mock_proc):
            violations = run_gitleaks_nodes(nodes)

        assert len(violations) == 1
        assert violations[0]["path"] == "node-B"
        assert violations[0]["source"] == "gitleaks"

    def test_jinja_values_filtered(self) -> None:
        """Jinja template matches are filtered from stdin results."""
        nodes = [("n1", "token: '{{ vault_token }}'\n")]

        findings_json = json.dumps(
            [
                {
                    "RuleID": "generic-api-key",
                    "Description": "API Key",
                    "File": "",
                    "StartLine": 2,
                    "EndLine": 2,
                    "Match": "{{ vault_token }}",
                }
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = findings_json
        mock_proc.stderr = ""

        with patch("apme_engine.validators.gitleaks.scanner.subprocess.run", return_value=mock_proc):
            violations = run_gitleaks_nodes(nodes)

        assert len(violations) == 0

    def test_vault_encrypted_filtered(self) -> None:
        """Nodes with vault-encrypted content are filtered."""
        nodes = [("n1", "$ANSIBLE_VAULT;1.1;AES256\ndeadbeef\n")]

        findings_json = json.dumps(
            [
                {
                    "RuleID": "generic-api-key",
                    "Description": "Key",
                    "File": "",
                    "StartLine": 2,
                    "EndLine": 2,
                    "Match": "deadbeef",
                }
            ]
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = findings_json
        mock_proc.stderr = ""

        with patch("apme_engine.validators.gitleaks.scanner.subprocess.run", return_value=mock_proc):
            violations = run_gitleaks_nodes(nodes)

        assert len(violations) == 0


class TestHealthTimeout:
    """Timeout path bounds reaping and suppresses kill errors."""

    async def test_timeout_suppresses_kill_oserror_and_bounds_wait(self) -> None:
        """Non-ProcessLookupError OSError from kill is suppressed; wait is bounded."""
        from apme.v1 import common_pb2
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill.side_effect = OSError("kill failed")
        mock_proc.wait = AsyncMock(return_value=None)

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
            patch("asyncio.wait_for", wraps=asyncio.wait_for) as mock_wait_for,
        ):
            resp = await servicer.Health(common_pb2.HealthRequest(), None)  # type: ignore[arg-type]

        assert "timeout" in resp.status
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()
        assert mock_wait_for.call_count == 2
        assert mock_wait_for.call_args_list[1][1].get("timeout") is not None

    async def test_hanging_wait_still_returns_timeout(self) -> None:
        """A hung proc.wait does not leak a zombie; Health returns promptly."""
        from apme.v1 import common_pb2
        from apme_engine.daemon import gitleaks_validator_server
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())

        async def _hang() -> int:
            """Simulate a hung reaping wait.

            Returns:
                Never returns normally; cancelled by the bounded wait.
            """
            await asyncio.sleep(60)
            return 0

        mock_proc.wait = AsyncMock(side_effect=_hang)
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
            patch.object(gitleaks_validator_server, "_HEALTH_TIMEOUT_S", 0.05),
        ):
            t0 = asyncio.get_event_loop().time()
            resp = await servicer.Health(common_pb2.HealthRequest(), None)  # type: ignore[arg-type]
            elapsed = asyncio.get_event_loop().time() - t0
        assert "timeout" in resp.status
        assert elapsed < 1.0

    async def test_health_timeout_respects_total_budget(self) -> None:
        """communicate() and proc.wait() together stay within _HEALTH_TIMEOUT_S."""
        from apme.v1 import common_pb2
        from apme_engine.daemon import gitleaks_validator_server
        from apme_engine.daemon.gitleaks_validator_server import GitleaksValidatorServicer

        servicer = GitleaksValidatorServicer()
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.wait = AsyncMock(side_effect=TimeoutError())

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
            patch.object(gitleaks_validator_server, "_HEALTH_TIMEOUT_S", 0.1),
        ):
            t0 = asyncio.get_event_loop().time()
            resp = await servicer.Health(common_pb2.HealthRequest(), None)  # type: ignore[arg-type]
            elapsed = asyncio.get_event_loop().time() - t0

        assert "timeout" in resp.status
        assert elapsed < 0.5
        mock_proc.kill.assert_called_once()
