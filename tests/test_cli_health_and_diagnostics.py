"""Tests for CLI health-check subcommand and scan -v/-vv diagnostics output."""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

import apme_engine.cli as cli_module
from apme.v1 import common_pb2, primary_pb2

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_scan_diagnostics():
    """Create a ScanDiagnostics proto with realistic data."""
    diag = primary_pb2.ScanDiagnostics(
        engine_parse_ms=12.5,
        engine_annotate_ms=8.3,
        engine_total_ms=20.8,
        files_scanned=5,
        trees_built=3,
        total_violations=7,
        fan_out_ms=45.2,
        total_ms=150.0,
    )

    # Add validator diagnostics
    native_diag = common_pb2.ValidatorDiagnostics(
        validator_name="native",
        total_ms=35.0,
        files_received=5,
        violations_found=4,
    )
    native_diag.rule_timings.append(
        common_pb2.RuleTiming(rule_id="L026", elapsed_ms=15.0, violations=2)
    )
    native_diag.rule_timings.append(
        common_pb2.RuleTiming(rule_id="L027", elapsed_ms=10.0, violations=1)
    )
    native_diag.rule_timings.append(
        common_pb2.RuleTiming(rule_id="R101", elapsed_ms=8.0, violations=1)
    )
    native_diag.metadata["rules_checked"] = "25"

    opa_diag = common_pb2.ValidatorDiagnostics(
        validator_name="opa",
        total_ms=40.0,
        files_received=5,
        violations_found=3,
    )
    opa_diag.rule_timings.append(
        common_pb2.RuleTiming(rule_id="L002", elapsed_ms=12.0, violations=2)
    )
    opa_diag.rule_timings.append(
        common_pb2.RuleTiming(rule_id="L003", elapsed_ms=8.0, violations=1)
    )
    opa_diag.metadata["opa_response_size"] = "1234"

    diag.validators.append(native_diag)
    diag.validators.append(opa_diag)

    return diag


@pytest.fixture
def mock_scan_response(mock_scan_diagnostics):
    """Create a ScanResponse proto with diagnostics."""
    resp = primary_pb2.ScanResponse(
        scan_id="test-scan-123",
    )
    resp.diagnostics.CopyFrom(mock_scan_diagnostics)
    # Add a violation
    v = resp.violations.add()
    v.rule_id = "L026"
    v.level = "warning"
    v.message = "Use FQCN for module"
    v.file = "playbook.yml"
    v.line = 10
    return resp


@pytest.fixture
def health_results_all_ok():
    """Health check results where all services are healthy."""
    return {
        "primary": {"ok": True, "status": "ok", "error": None, "latency_ms": 5.2},
        "native": {"ok": True, "status": "ok", "error": None, "latency_ms": 3.1},
        "opa": {"ok": True, "status": "ok", "error": None, "latency_ms": 4.5},
        "ansible": {"ok": True, "status": "ok", "error": None, "latency_ms": 6.0},
        "cache_maintainer": {"ok": True, "status": "ok", "error": None, "latency_ms": 2.8},
    }


@pytest.fixture
def health_results_some_fail():
    """Health check results where some services fail."""
    return {
        "primary": {"ok": True, "status": "ok", "error": None, "latency_ms": 5.2},
        "native": {"ok": False, "status": None, "error": "Connection refused", "latency_ms": 100.0},
        "opa": {"ok": True, "status": "ok", "error": None, "latency_ms": 4.5},
        "ansible": {"ok": False, "status": None, "error": "Deadline exceeded", "latency_ms": 5000.0},
        "cache_maintainer": {"ok": True, "status": "ok", "error": None, "latency_ms": 2.8},
    }


# ---------------------------------------------------------------------------
# health-check CLI tests
# ---------------------------------------------------------------------------


class TestHealthCheckCLI:
    """Tests for the health-check subcommand."""

    def test_health_check_requires_primary_addr(self):
        """health-check exits 1 if no --primary-addr or env var is set."""
        stderr_io = StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme-scan", "health-check"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        assert "primary" in stderr_io.getvalue().lower()

    def test_health_check_all_ok_exits_0(self, health_results_all_ok):
        """health-check exits 0 when all services are healthy."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0
        output = stdout_io.getvalue()
        assert "overall: ok" in output

    def test_health_check_some_fail_exits_1(self, health_results_some_fail):
        """health-check exits 1 when any service fails."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_some_fail),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        output = stdout_io.getvalue()
        assert "overall: fail" in output

    def test_health_check_shows_service_status(self, health_results_all_ok):
        """health-check prints status for each service."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit),
        ):
            cli_module.main()
        output = stdout_io.getvalue()
        assert "primary: ok" in output
        assert "native: ok" in output
        assert "opa: ok" in output
        assert "ansible: ok" in output
        assert "cache_maintainer: ok" in output

    def test_health_check_shows_latency(self, health_results_all_ok):
        """health-check prints latency for successful checks."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit),
        ):
            cli_module.main()
        output = stdout_io.getvalue()
        # Should show latency like "(5.2ms)"
        assert "ms)" in output

    def test_health_check_shows_error_message(self, health_results_some_fail):
        """health-check prints error message for failed services."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_some_fail),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit),
        ):
            cli_module.main()
        output = stdout_io.getvalue()
        assert "Connection refused" in output
        assert "Deadline exceeded" in output

    def test_health_check_json_output(self, health_results_all_ok):
        """health-check --json outputs valid JSON."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051", "--json"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0
        data = json.loads(stdout_io.getvalue())
        assert "primary" in data
        assert data["primary"]["ok"] is True
        assert "latency_ms" in data["primary"]

    def test_health_check_json_exits_1_on_failure(self, health_results_some_fail):
        """health-check --json exits 1 when any service fails."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_some_fail),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051", "--json"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1

    def test_health_check_uses_env_var(self, health_results_all_ok):
        """health-check uses APME_PRIMARY_ADDRESS env var."""
        stdout_io = StringIO()
        with (
            patch.dict("os.environ", {"APME_PRIMARY_ADDRESS": "envhost:50051"}),
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok) as mock_run,
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check"]),
            pytest.raises(SystemExit),
        ):
            cli_module.main()
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["primary_addr"] == "envhost:50051"

    def test_health_check_timeout_arg(self, health_results_all_ok):
        """health-check --timeout is passed to run_health_checks."""
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=health_results_all_ok) as mock_run,
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "health-check", "--primary-addr", "localhost:50051", "--timeout", "10"]),
            pytest.raises(SystemExit),
        ):
            cli_module.main()
        assert mock_run.call_args[1]["timeout"] == 10.0


# ---------------------------------------------------------------------------
# scan -v/-vv diagnostics tests
# ---------------------------------------------------------------------------


class TestScanDiagnosticsOutput:
    """Tests for scan -v and -vv diagnostics output."""

    def test_scan_v_prints_validator_summary(self, mock_scan_response):
        """scan -v prints validator summary with timings."""
        stderr_io = StringIO()
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stderr", stderr_io),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "scan", "-v", "--primary-addr", "localhost:50051", "."]),
        ):
            cli_module.main()

        output = stderr_io.getvalue()
        # Should show validator names and timings
        assert "Native" in output or "native" in output.lower()
        assert "Opa" in output or "opa" in output.lower()
        assert "Engine" in output or "engine" in output.lower()
        assert "Fan-out" in output or "fan" in output.lower()
        assert "Total" in output or "total" in output.lower()

    def test_scan_v_prints_top_slowest_rules(self, mock_scan_response):
        """scan -v prints top 10 slowest rules."""
        stderr_io = StringIO()
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stderr", stderr_io),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "scan", "-v", "--primary-addr", "localhost:50051", "."]),
        ):
            cli_module.main()

        output = stderr_io.getvalue()
        # Should show top slowest rules section
        assert "slowest" in output.lower() or "Top" in output
        # Should include rule IDs
        assert "L026" in output or "L002" in output

    def test_scan_vv_prints_per_rule_breakdown(self, mock_scan_response, tmp_path):
        """scan -vv prints full per-rule breakdown for each validator."""
        stderr_io = StringIO()
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stderr", stderr_io),
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme-scan", "scan", "-vv", "--primary-addr", "localhost:50051", str(tmp_path)],
            ),
        ):
            cli_module.main()

        output = stderr_io.getvalue()
        # Should show all rules, not just top 10
        assert "L026" in output
        assert "L027" in output
        assert "R101" in output
        assert "L002" in output
        assert "L003" in output

    def test_scan_vv_shows_metadata(self, mock_scan_response, tmp_path):
        """scan -vv shows validator metadata."""
        stderr_io = StringIO()
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stderr", stderr_io),
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme-scan", "scan", "-vv", "--primary-addr", "localhost:50051", str(tmp_path)],
            ),
        ):
            cli_module.main()

        output = stderr_io.getvalue()
        # Should show metadata
        assert "metadata" in output.lower()
        assert "rules_checked" in output

    def test_scan_json_v_includes_diagnostics(self, mock_scan_response, tmp_path):
        """scan --json -v includes diagnostics in JSON output."""
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                [
                    "apme-scan",
                    "scan",
                    "--json",
                    "-v",
                    "--primary-addr",
                    "localhost:50051",
                    str(tmp_path),
                ],
            ),
        ):
            cli_module.main()

        data = json.loads(stdout_io.getvalue())
        assert "diagnostics" in data
        assert "engine_parse_ms" in data["diagnostics"]
        assert "validators" in data["diagnostics"]
        assert len(data["diagnostics"]["validators"]) == 2

    def test_scan_json_without_v_no_diagnostics(self, mock_scan_response):
        """scan --json without -v does not include diagnostics."""
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "scan", "--json", "--primary-addr", "localhost:50051", "."]),
        ):
            cli_module.main()

        data = json.loads(stdout_io.getvalue())
        assert "diagnostics" not in data

    def test_scan_no_v_no_diagnostics_output(self, mock_scan_response):
        """scan without -v does not print diagnostics to stderr."""
        stderr_io = StringIO()
        stdout_io = StringIO()

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_stub.Scan.return_value = mock_scan_response

        with (
            patch("grpc.insecure_channel", return_value=mock_channel),
            patch("apme_engine.cli.primary_pb2_grpc.PrimaryStub", return_value=mock_stub),
            patch("sys.stderr", stderr_io),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "scan", "--primary-addr", "localhost:50051", "."]),
        ):
            cli_module.main()

        output = stderr_io.getvalue()
        # Should not have detailed diagnostics
        assert "Engine:" not in output
        assert "Fan-out:" not in output
        assert "Top slowest" not in output


# ---------------------------------------------------------------------------
# Diagnostics formatting unit tests
# ---------------------------------------------------------------------------


class TestDiagnosticsFormatting:
    """Unit tests for diagnostics formatting functions."""

    def test_fmt_ms_submillisecond(self):
        """_fmt_ms returns '<1ms' for values under 1ms."""
        assert cli_module._fmt_ms(0.5) == "<1ms"
        assert cli_module._fmt_ms(0.001) == "<1ms"

    def test_fmt_ms_milliseconds(self):
        """_fmt_ms returns 'Xms' for values under 1000ms."""
        assert cli_module._fmt_ms(50) == "50ms"
        assert cli_module._fmt_ms(999) == "999ms"
        assert cli_module._fmt_ms(1.5) == "2ms"  # rounded

    def test_fmt_ms_seconds(self):
        """_fmt_ms returns 'X.Ys' for values >= 1000ms."""
        assert cli_module._fmt_ms(1000) == "1.0s"
        assert cli_module._fmt_ms(1500) == "1.5s"
        assert cli_module._fmt_ms(12345) == "12.3s"

    def test_diag_to_dict_structure(self, mock_scan_diagnostics):
        """_diag_to_dict returns proper structure."""
        result = cli_module._diag_to_dict(mock_scan_diagnostics)

        assert result["engine_parse_ms"] == 12.5
        assert result["engine_annotate_ms"] == 8.3
        assert result["engine_total_ms"] == 20.8
        assert result["files_scanned"] == 5
        assert result["trees_built"] == 3
        assert result["total_violations"] == 7
        assert result["fan_out_ms"] == 45.2
        assert result["total_ms"] == 150.0

    def test_diag_to_dict_validators(self, mock_scan_diagnostics):
        """_diag_to_dict includes validator details."""
        result = cli_module._diag_to_dict(mock_scan_diagnostics)

        assert len(result["validators"]) == 2
        native = result["validators"][0]
        assert native["validator_name"] == "native"
        assert native["total_ms"] == 35.0
        assert native["violations_found"] == 4
        assert len(native["rule_timings"]) == 3
        assert native["rule_timings"][0]["rule_id"] == "L026"

    def test_diag_to_dict_metadata(self, mock_scan_diagnostics):
        """_diag_to_dict includes validator metadata."""
        result = cli_module._diag_to_dict(mock_scan_diagnostics)

        native = result["validators"][0]
        assert native["metadata"]["rules_checked"] == "25"


class TestPrintDiagnosticsV:
    """Tests for _print_diagnostics_v output."""

    def test_prints_engine_timing(self, mock_scan_diagnostics):
        """_print_diagnostics_v prints engine timing."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_v(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        assert "Engine:" in output
        assert "parse:" in output
        assert "annotate:" in output

    def test_prints_files_scanned(self, mock_scan_diagnostics):
        """_print_diagnostics_v prints files scanned."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_v(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        assert "Files:" in output
        assert "5" in output

    def test_prints_validator_tree(self, mock_scan_diagnostics):
        """_print_diagnostics_v prints validators in tree format."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_v(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        # Should use tree connectors
        assert "├──" in output or "└──" in output

    def test_prints_top_slowest_rules(self, mock_scan_diagnostics):
        """_print_diagnostics_v prints top slowest rules."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_v(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        assert "Top slowest rules:" in output
        # L026 is slowest at 15ms
        assert "L026" in output


class TestPrintDiagnosticsVV:
    """Tests for _print_diagnostics_vv output."""

    def test_prints_all_rules(self, mock_scan_diagnostics):
        """_print_diagnostics_vv prints all rules for each validator."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_vv(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        # All rule IDs should appear
        assert "L026" in output
        assert "L027" in output
        assert "R101" in output
        assert "L002" in output
        assert "L003" in output

    def test_prints_trees_built(self, mock_scan_diagnostics):
        """_print_diagnostics_vv prints trees built count."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_vv(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        assert "tree(s)" in output
        assert "3" in output

    def test_prints_metadata_section(self, mock_scan_diagnostics):
        """_print_diagnostics_vv prints metadata for each validator."""
        stderr_io = StringIO()
        with patch("sys.stderr", stderr_io):
            cli_module._print_diagnostics_vv(mock_scan_diagnostics)

        output = stderr_io.getvalue()
        assert "metadata:" in output
        assert "rules_checked=25" in output
