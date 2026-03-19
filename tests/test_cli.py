"""Tests for apme_engine.cli."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import apme_engine.cli as cli_module
from apme_engine.ansi import strip_ansi
from apme_engine.cli import (
    _deduplicate_violations,
    _find_role_root,
    _fmt_ms,
    _group_files_by_scan_root,
    _sort_violations,
)
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.validators.base import ScanContext


def _make_context(hierarchy_payload: YAMLDict, scandata: object | None = None) -> ScanContext:
    return ScanContext(hierarchy_payload=hierarchy_payload, scandata=scandata, root_dir="")


class TestMain:
    """Tests for main() CLI entrypoint."""

    @pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
    def _repo_root(self, repo_root: Path) -> None:
        """Ensure repo_root is available; main uses Path(__file__).parent.parent.

        Args:
            repo_root: Fixture providing the repository root path.

        """
        pass

    def test_main_scan_failure_exits_1(self) -> None:
        """When run_scan raises, main writes to stderr and exits 1."""
        stderr_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", side_effect=FileNotFoundError("path not found")),
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme-scan", "scan", "."]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        assert "path not found" in stderr_io.getvalue() or "Scan failed" in stderr_io.getvalue()

    def test_main_empty_payload_exits_0_with_json(self) -> None:
        """When hierarchy_payload is empty and --json, print JSON and exit 0."""
        stdout_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context({})),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme-scan", "scan", "--json", "."]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0
        out = stdout_io.getvalue()
        data = json.loads(out)
        assert data["violations"] == []
        assert "hierarchy_payload" in data

    def test_main_empty_payload_exits_0_without_json(self) -> None:
        """When hierarchy_payload is empty and no --json, exit 0 after stderr message."""
        stderr_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context({})),
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme-scan", "scan", "."]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0
        assert "No hierarchy payload" in stderr_io.getvalue()

    def test_main_no_validators_json_outputs_hierarchy_only(self, sample_hierarchy_payload: YAMLDict) -> None:
        """With --no-opa --no-native --no-ansible and --json, output is hierarchy_payload only.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        stdout_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("sys.argv", ["apme-scan", "scan", "--no-opa", "--no-native", "--no-ansible", "--json", "."]),
            patch("sys.stdout", stdout_io),
        ):
            cli_module.main()
        out = stdout_io.getvalue()
        data = json.loads(out)
        assert "hierarchy_payload" in data
        assert data["hierarchy_payload"]["scan_id"] == sample_hierarchy_payload["scan_id"]
        assert "violations" not in data

    def test_main_no_validators_no_json_prints_message(self, sample_hierarchy_payload: YAMLDict) -> None:
        """With --no-opa --no-native --no-ansible and no --json, print message about validators skipped.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        stdout_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("sys.argv", ["apme-scan", "scan", "--no-opa", "--no-native", "--no-ansible", "."]),
            patch("sys.stdout", stdout_io),
        ):
            cli_module.main()
        assert "validators skipped" in stdout_io.getvalue().lower() or "hierarchy" in stdout_io.getvalue().lower()

    def test_main_with_opa_json_outputs_violations_and_count(
        self, sample_hierarchy_payload: YAMLDict, opa_eval_result_with_violations: YAMLDict
    ) -> None:
        """With OPA and --json, output includes violations and count.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_with_violations: Fixture providing OPA eval results with violations.

        """
        stdout_io = StringIO()
        violations = opa_eval_result_with_violations["result"][0]["expressions"][0]["value"]  # type: ignore[index,call-overload]
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("apme_engine.validators.opa.run_opa", return_value=violations),
            patch("sys.argv", ["apme-scan", "scan", "--no-native", "--json", "."]),
            patch("sys.stdout", stdout_io),
        ):
            cli_module.main()
        out = stdout_io.getvalue()
        data = json.loads(out)
        assert "violations" in data
        assert data["count"] == 1
        assert data["violations"][0]["rule_id"] == "task-name"

    def test_main_with_opa_no_json_prints_summary_and_list(self, sample_hierarchy_payload: YAMLDict) -> None:
        """With OPA and no --json, print Scan line and violation lines.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        stdout_io = StringIO()
        violations = [{"rule_id": "r1", "level": "warning", "message": "msg", "file": "f.yml", "line": 1, "path": "p"}]
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("apme_engine.validators.opa.run_opa", return_value=violations),
            patch("sys.argv", ["apme-scan", "scan", "--no-native", "."]),
            patch("sys.stdout", stdout_io),
        ):
            cli_module.main()
        out = stdout_io.getvalue()
        clean = strip_ansi(out)
        assert "1 warning(s)" in clean or "WARN" in clean
        assert "r1" in clean
        assert "f.yml" in clean
        assert "msg" in clean

    def test_main_with_opa_no_violations_prints_no_violations(self, sample_hierarchy_payload: YAMLDict) -> None:
        """With OPA and no violations, print 'No issues found'.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        stdout_io = StringIO()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("apme_engine.validators.opa.run_opa", return_value=[]),
            patch("sys.argv", ["apme-scan", "scan", "--no-native", "."]),
            patch("sys.stdout", stdout_io),
        ):
            cli_module.main()
        assert "No issues found" in strip_ansi(stdout_io.getvalue())

    def test_main_uses_custom_opa_bundle_when_provided(
        self, sample_hierarchy_payload: YAMLDict, tmp_path: Path
    ) -> None:
        """When --opa-bundle is passed, OpaValidator receives that path.

        Args:
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            tmp_path: Pytest temporary directory fixture.

        """
        bundle = tmp_path / "custom_bundle"
        bundle.mkdir()
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)),
            patch("apme_engine.validators.opa.run_opa", return_value=[]) as mock_opa,
            patch("sys.argv", ["apme-scan", "scan", "--no-native", "--opa-bundle", str(bundle), "."]),
        ):
            cli_module.main()
        mock_opa.assert_called_once()
        assert mock_opa.call_args[0][1] == str(bundle)


class TestRunScan:
    """Tests for run_scan (runner module) via CLI integration."""

    def test_run_scan_nonexistent_path_raises(self, repo_root: Path) -> None:
        """run_scan raises FileNotFoundError when target does not exist.

        Args:
            repo_root: Fixture providing the repository root path.

        """
        with (
            patch.object(
                cli_module, "run_scan", side_effect=FileNotFoundError("Target path does not exist: /nonexistent")
            ),
            pytest.raises(SystemExit),
            patch("sys.argv", ["apme-scan", "scan", "/nonexistent/path/xyz"]),
        ):
            cli_module.main()

    def test_run_scan_playbook_file_called_with_correct_args(
        self, repo_root: Path, tmp_path: Path, sample_hierarchy_payload: YAMLDict
    ) -> None:
        """When target is a file, run_scan is called with playbook path and repo_root (from CLI's __file__).

        Args:
            repo_root: Fixture providing the repository root path.
            tmp_path: Pytest temporary directory fixture.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("---\n- hosts: localhost\n  tasks: []\n")
        with (
            patch.object(cli_module, "run_scan", return_value=_make_context(sample_hierarchy_payload)) as mock_run_scan,
            patch("apme_engine.validators.opa.run_opa", return_value=[]),
            patch("sys.argv", ["apme-scan", "scan", "--no-native", str(playbook)]),
        ):
            cli_module.main()
        mock_run_scan.assert_called_once()
        call_args = mock_run_scan.call_args
        assert call_args[0][0] == str(playbook)
        # CLI uses Path(__file__).parent.parent (apme_engine -> src when run from source)
        expected_root = str(Path(cli_module.__file__).resolve().parent.parent)
        assert call_args[0][1] == expected_root
        assert call_args[1]["include_scandata"] is True

    def test_run_scan_returns_context_with_payload(
        self, repo_root: Path, tmp_path: Path, sample_hierarchy_payload: YAMLDict
    ) -> None:
        """run_scan returns ScanContext with hierarchy_payload.

        Args:
            repo_root: Fixture providing the repository root path.
            tmp_path: Pytest temporary directory fixture.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        from apme_engine.runner import run_scan

        playbook = tmp_path / "play.yml"
        playbook.write_text("---\n- hosts: localhost\n  tasks: []\n")
        with patch("apme_engine.runner.ARIScanner") as MockScanner:
            mock_scanner = MagicMock()
            mock_scanner._current = MagicMock()
            mock_scanner._current.hierarchy_payload = sample_hierarchy_payload
            MockScanner.return_value = mock_scanner
            context = run_scan(str(playbook), str(repo_root), include_scandata=False)
        assert context.hierarchy_payload == sample_hierarchy_payload
        assert context.scandata is None


class TestSortViolations:
    """Tests for _sort_violations helper."""

    def test_sort_by_file_then_line(self) -> None:
        """Violations are sorted by file path then line number."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "b.yml", "line": 10},
            {"rule_id": "r2", "file": "a.yml", "line": 5},
            {"rule_id": "r3", "file": "a.yml", "line": 1},
        ]
        result = _sort_violations(violations)
        assert result[0]["file"] == "a.yml"
        assert result[0]["line"] == 1
        assert result[1]["line"] == 5
        assert result[2]["file"] == "b.yml"

    def test_sort_with_list_line(self) -> None:
        """Violations with list line values sort correctly."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "a.yml", "line": [10, 15]},
            {"rule_id": "r2", "file": "a.yml", "line": [2, 5]},
        ]
        result = _sort_violations(violations)
        assert result[0]["line"] == [2, 5]

    def test_sort_with_none_line(self) -> None:
        """Violations with None line sort before numeric lines."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "a.yml", "line": 5},
            {"rule_id": "r2", "file": "a.yml", "line": None},
        ]
        result = _sort_violations(violations)
        assert result[0]["line"] is None
        assert result[1]["line"] == 5

    def test_sort_with_missing_file(self) -> None:
        """Violations without file key sort first."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "line": 1},
            {"rule_id": "r2", "file": "a.yml", "line": 1},
        ]
        result = _sort_violations(violations)
        assert result[0].get("file") is None or result[0].get("file") == ""

    def test_empty_list(self) -> None:
        """Empty violation list returns empty list."""
        assert _sort_violations([]) == []


class TestDeduplicateViolations:
    """Tests for _deduplicate_violations helper."""

    def test_removes_duplicates(self) -> None:
        """Duplicate violations (same rule, file, line) are removed."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "a.yml", "line": 5},
            {"rule_id": "r1", "file": "a.yml", "line": 5},
            {"rule_id": "r2", "file": "a.yml", "line": 5},
        ]
        result = _deduplicate_violations(violations)
        assert len(result) == 2

    def test_keeps_different_lines(self) -> None:
        """Violations with different lines are kept."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "a.yml", "line": 5},
            {"rule_id": "r1", "file": "a.yml", "line": 10},
        ]
        result = _deduplicate_violations(violations)
        assert len(result) == 2

    def test_dedup_with_list_line(self) -> None:
        """Duplicate violations with list line are deduplicated."""
        violations: list[ViolationDict] = [
            {"rule_id": "r1", "file": "a.yml", "line": [5, 10]},
            {"rule_id": "r1", "file": "a.yml", "line": [5, 10]},
        ]
        result = _deduplicate_violations(violations)
        assert len(result) == 1

    def test_empty_list(self) -> None:
        """Empty violation list returns empty list."""
        assert _deduplicate_violations([]) == []


class TestFmtMs:
    """Tests for _fmt_ms helper."""

    def test_sub_millisecond(self) -> None:
        """Values under 1ms format as '<1ms'."""
        assert _fmt_ms(0.5) == "<1ms"

    def test_milliseconds(self) -> None:
        """Values in milliseconds format with 'ms' suffix."""
        assert _fmt_ms(42) == "42ms"

    def test_seconds(self) -> None:
        """Values >= 1000ms format as seconds."""
        assert _fmt_ms(1500) == "1.5s"

    def test_zero(self) -> None:
        """Zero formats as '<1ms'."""
        assert _fmt_ms(0) == "<1ms"

    def test_exactly_one_second(self) -> None:
        """Exactly 1000ms formats as '1.0s'."""
        assert _fmt_ms(1000) == "1.0s"

    def test_large_value(self) -> None:
        """Large values format as seconds."""
        assert _fmt_ms(65000) == "65.0s"


class TestFormatCommand:
    """Tests for the 'format' subcommand."""

    def test_format_nonexistent_target_exits_1(self) -> None:
        """Format on nonexistent path exits with code 1."""
        stderr_io = StringIO()
        with (
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme", "format", "/nonexistent/path"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        assert "Path not found" in stderr_io.getvalue()

    def test_format_check_no_changes_exits_0(self, tmp_path: Path) -> None:
        """Format --check on already-formatted file exits 0.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "ok.yml"
        yml.write_text("---\nname: test\n")
        mock_result = MagicMock()
        mock_result.changed = False
        mock_result.path = yml
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.argv", ["apme", "format", "--check", str(yml)]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0

    def test_format_check_with_changes_exits_1(self, tmp_path: Path) -> None:
        """Format --check on file needing changes exits 1.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "bad.yml"
        yml.write_text("name: test\n")
        mock_result = MagicMock()
        mock_result.changed = True
        mock_result.path = yml
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.stderr", StringIO()),
            patch("sys.argv", ["apme", "format", "--check", str(yml)]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1

    def test_format_no_changes_prints_message(self, tmp_path: Path) -> None:
        """Format on already-formatted file prints 'already formatted'.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "ok.yml"
        yml.write_text("---\nname: test\n")
        mock_result = MagicMock()
        mock_result.changed = False
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "format", str(yml)]),
        ):
            cli_module.main()
        assert "already formatted" in stdout_io.getvalue().lower()

    def test_format_apply_writes_files(self, tmp_path: Path) -> None:
        """Format --apply writes reformatted content and prints message.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "fix.yml"
        yml.write_text("name: test\n")
        mock_result = MagicMock()
        mock_result.changed = True
        mock_result.formatted = "---\nname: test\n"
        mock_result.path = yml
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "format", "--apply", str(yml)]),
        ):
            cli_module.main()
        assert "reformatted" in stdout_io.getvalue().lower()

    def test_format_directory(self, tmp_path: Path) -> None:
        """Format on directory processes files and prints message.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        mock_result = MagicMock()
        mock_result.changed = False
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.format_directory", return_value=[mock_result]),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "format", str(tmp_path)]),
        ):
            cli_module.main()
        assert "already formatted" in stdout_io.getvalue().lower()

    def test_format_diff_output(self, tmp_path: Path) -> None:
        """Format without --apply prints unified diff to stdout.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "fix.yml"
        yml.write_text("name: test\n")
        mock_result = MagicMock()
        mock_result.changed = True
        mock_result.diff = "--- a/fix.yml\n+++ b/fix.yml\n"
        mock_result.path = yml
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.stdout", stdout_io),
            patch("sys.stderr", StringIO()),
            patch("sys.argv", ["apme", "format", str(yml)]),
        ):
            cli_module.main()
        assert "---" in stdout_io.getvalue()


class TestHealthCheckCommand:
    """Tests for the 'health-check' subcommand."""

    def test_health_check_no_addr_exits_1(self) -> None:
        """Health-check without address env var exits 1."""
        stderr_io = StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme", "health-check"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1

    def test_health_check_json_all_ok(self) -> None:
        """Health-check --json with all services ok exits 0."""
        mock_results = {
            "Primary": {"ok": True, "latency_ms": 5.0, "error": None},
        }
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=mock_results),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "health-check", "--primary-addr", "localhost:50051", "--json"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0
        data = json.loads(stdout_io.getvalue())
        assert data["Primary"]["ok"] is True

    def test_health_check_json_with_failure(self) -> None:
        """Health-check --json with service failure exits 1."""
        mock_results = {
            "Primary": {"ok": False, "latency_ms": None, "error": "connection refused"},
        }
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=mock_results),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "health-check", "--primary-addr", "localhost:50051", "--json"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1

    def test_health_check_text_output(self) -> None:
        """Health-check without --json prints text with ok/fail status."""
        mock_results = {
            "Primary": {"ok": True, "latency_ms": 3.2, "error": None},
            "Native": {"ok": False, "latency_ms": None, "error": "timeout"},
        }
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.run_health_checks", return_value=mock_results),
            patch("sys.stdout", stdout_io),
            patch("sys.argv", ["apme", "health-check", "--primary-addr", "localhost:50051"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        out = stdout_io.getvalue()
        assert "Primary: ok" in out
        assert "Native: fail" in out
        assert "overall: fail" in out


class TestCacheCommand:
    """Tests for the 'cache' subcommand."""

    def test_cache_pull_galaxy(self, tmp_path: Path) -> None:
        """Cache pull-galaxy installs collection and prints message.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.pull_galaxy_collection") as mock_pull,
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme", "cache", "--cache-root", str(tmp_path), "pull-galaxy", "ns.col"],
            ),
        ):
            cli_module.main()
        mock_pull.assert_called_once()
        assert "Installed ns.col" in stdout_io.getvalue()

    def test_cache_pull_requirements(self, tmp_path: Path) -> None:
        """Cache pull-requirements installs from requirements file.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        stdout_io = StringIO()
        req_file = tmp_path / "requirements.yml"
        req_file.write_text("---\ncollections: []\n")
        with (
            patch("apme_engine.cli.pull_galaxy_requirements") as mock_pull,
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme", "cache", "--cache-root", str(tmp_path), "pull-requirements", str(req_file)],
            ),
        ):
            cli_module.main()
        mock_pull.assert_called_once()
        assert "Installed requirements" in stdout_io.getvalue()

    def test_cache_clone_org(self, tmp_path: Path) -> None:
        """Cache clone-org clones GitHub org repos.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.pull_github_org") as mock_pull,
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme", "cache", "--cache-root", str(tmp_path), "clone-org", "ansible"],
            ),
        ):
            cli_module.main()
        mock_pull.assert_called_once()
        assert "Cloned org ansible" in stdout_io.getvalue()

    def test_cache_clone_org_with_repos(self, tmp_path: Path) -> None:
        """Cache clone-org with --repos clones specified repos.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        stdout_io = StringIO()
        with (
            patch("apme_engine.cli.pull_github_repos") as mock_pull,
            patch("sys.stdout", stdout_io),
            patch(
                "sys.argv",
                ["apme", "cache", "--cache-root", str(tmp_path), "clone-org", "ansible", "--repos", "repo1", "repo2"],
            ),
        ):
            cli_module.main()
        mock_pull.assert_called_once()


class TestFixCommand:
    """Tests for the 'fix' subcommand."""

    def test_fix_nonexistent_target_exits_1(self) -> None:
        """Fix on nonexistent path exits 1."""
        stderr_io = StringIO()
        with (
            patch("sys.stderr", stderr_io),
            patch("sys.argv", ["apme", "fix", "/nonexistent/path"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 1
        assert "Path not found" in stderr_io.getvalue()

    def test_fix_check_no_changes_exits_0(self, tmp_path: Path) -> None:
        """Fix --check on already-fixed file exits 0.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        yml = tmp_path / "ok.yml"
        yml.write_text("---\nname: test\n")
        mock_result = MagicMock()
        mock_result.changed = False
        with (
            patch("apme_engine.cli.format_file", return_value=mock_result),
            patch("sys.stderr", StringIO()),
            patch("sys.argv", ["apme", "fix", "--check", str(yml)]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_module.main()
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# _find_role_root
# ---------------------------------------------------------------------------


class TestFindRoleRoot:
    """Tests for _find_role_root()."""

    def test_file_inside_role_tasks(self, tmp_path: Path) -> None:
        """File under tasks/ returns the role root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role = tmp_path / "my_role"
        (role / "tasks").mkdir(parents=True)
        (role / "defaults").mkdir()
        yml = role / "tasks" / "main.yml"
        yml.write_text("---\n")
        assert _find_role_root(yml.resolve()) == role

    def test_file_inside_role_handlers(self, tmp_path: Path) -> None:
        """File under handlers/ returns the role root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role = tmp_path / "my_role"
        (role / "handlers").mkdir(parents=True)
        yml = role / "handlers" / "main.yml"
        yml.write_text("---\n")
        assert _find_role_root(yml.resolve()) == role

    def test_file_not_in_role(self, tmp_path: Path) -> None:
        """File outside any role structure returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        yml = tmp_path / "playbook.yml"
        yml.write_text("---\n")
        assert _find_role_root(yml.resolve()) is None

    def test_nested_role(self, tmp_path: Path) -> None:
        """Finds the nearest role root when roles are nested.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        outer = tmp_path / "roles" / "outer"
        (outer / "tasks").mkdir(parents=True)
        inner = outer / "tasks" / "inner_role"
        (inner / "tasks").mkdir(parents=True)
        yml = inner / "tasks" / "main.yml"
        yml.write_text("---\n")
        assert _find_role_root(yml.resolve()) == inner

    def test_role_with_meta_only(self, tmp_path: Path) -> None:
        """A directory with only meta/ is still detected as a role root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role = tmp_path / "meta_role"
        (role / "meta").mkdir(parents=True)
        yml = role / "meta" / "main.yml"
        yml.write_text("---\n")
        assert _find_role_root(yml.resolve()) == role


# ---------------------------------------------------------------------------
# _group_files_by_scan_root
# ---------------------------------------------------------------------------


class TestGroupFilesByScanRoot:
    """Tests for _group_files_by_scan_root()."""

    def test_single_role(self, tmp_path: Path) -> None:
        """All files in one role group under the role root.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role = tmp_path / "my_role"
        (role / "tasks").mkdir(parents=True)
        (role / "handlers").mkdir()
        t = role / "tasks" / "main.yml"
        h = role / "handlers" / "main.yml"
        t.write_text("---\n")
        h.write_text("---\n")

        groups = _group_files_by_scan_root([str(t), str(h)])
        assert len(groups) == 1
        root = next(iter(groups))
        assert root == role
        assert set(groups[root]) == {t.resolve(), h.resolve()}

    def test_two_roles(self, tmp_path: Path) -> None:
        """Files from different roles are grouped separately.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        r1 = tmp_path / "role_a"
        (r1 / "tasks").mkdir(parents=True)
        r2 = tmp_path / "role_b"
        (r2 / "tasks").mkdir(parents=True)
        f1 = r1 / "tasks" / "main.yml"
        f2 = r2 / "tasks" / "main.yml"
        f1.write_text("---\n")
        f2.write_text("---\n")

        groups = _group_files_by_scan_root([str(f1), str(f2)])
        assert len(groups) == 2
        roots = set(groups.keys())
        assert roots == {r1, r2}

    def test_ungrouped_files_share_common_ancestor(self, tmp_path: Path) -> None:
        """Non-role files are grouped under their common ancestor.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        d = tmp_path / "project"
        d.mkdir()
        a = d / "site.yml"
        b = d / "deploy.yml"
        a.write_text("---\n")
        b.write_text("---\n")

        groups = _group_files_by_scan_root([str(a), str(b)])
        assert len(groups) == 1
        root = next(iter(groups))
        assert root == d

    def test_mixed_role_and_loose_files(self, tmp_path: Path) -> None:
        """Role files and loose playbooks are grouped separately.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        role = tmp_path / "my_role"
        (role / "tasks").mkdir(parents=True)
        rf = role / "tasks" / "main.yml"
        rf.write_text("---\n")
        loose = tmp_path / "site.yml"
        loose.write_text("---\n")

        groups = _group_files_by_scan_root([str(rf), str(loose)])
        assert len(groups) == 2
        assert role in groups
        resolved_loose = [p for root, paths in groups.items() if root != role for p in paths]
        assert loose.resolve() in resolved_loose

    def test_single_ungrouped_file(self, tmp_path: Path) -> None:
        """A single non-role file is grouped under its parent directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        f = tmp_path / "playbook.yml"
        f.write_text("---\n")

        groups = _group_files_by_scan_root([str(f)])
        assert len(groups) == 1
        root = next(iter(groups))
        assert root == tmp_path
