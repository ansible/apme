"""Unit tests for the ansible-lint runner module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from apme_engine.lint_runner import LintResult, run_ansible_lint


class TestRunAnsibleLint:
    """Tests for run_ansible_lint subprocess wrapper."""

    def test_report_mode_default_profile(self, tmp_path: Path) -> None:
        """Report mode uses --profile production and no --fix.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            result = run_ansible_lint(tmp_path, fix=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "ansible-lint"
        assert "--fix" not in args
        assert "--profile" in args
        assert args[args.index("--profile") + 1] == "production"
        assert str(tmp_path) in args
        assert result.returncode == 0
        assert result.fixed is False

    def test_fix_mode(self, tmp_path: Path) -> None:
        """Fix mode passes --fix flag.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            result = run_ansible_lint(tmp_path, fix=True)

        args = mock_run.call_args[0][0]
        assert "--fix" in args
        assert result.fixed is True

    def test_custom_profile(self, tmp_path: Path) -> None:
        """Custom profile overrides the default.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            run_ansible_lint(tmp_path, profile="shared")

        args = mock_run.call_args[0][0]
        assert args[args.index("--profile") + 1] == "shared"

    def test_config_file_flag(self, tmp_path: Path) -> None:
        """Config file is passed as -c.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            run_ansible_lint(tmp_path, config_file="/path/to/.ansible-lint.yml")

        args = mock_run.call_args[0][0]
        assert "-c" in args
        assert args[args.index("-c") + 1] == "/path/to/.ansible-lint.yml"

    def test_no_config_file_omits_flag(self, tmp_path: Path) -> None:
        """Without config_file, -c flag is not passed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            run_ansible_lint(tmp_path)

        args = mock_run.call_args[0][0]
        assert "-c" not in args

    def test_violations_found_nonzero_exit(self, tmp_path: Path) -> None:
        """Non-zero exit code is captured in result.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 2
        mock_proc.stdout = "playbook.yml:3: [yaml] trailing spaces"
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc):
            result = run_ansible_lint(tmp_path)

        assert result.returncode == 2
        assert "trailing spaces" in result.stdout

    def test_binary_not_found_raises(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised when ansible-lint is missing.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        with patch(
            "apme_engine.lint_runner.subprocess.run",
            side_effect=FileNotFoundError("No such file"),
        ):
            try:
                run_ansible_lint(tmp_path)
                raised = False
            except FileNotFoundError as e:
                raised = True
                assert "ansible-lint" in str(e)

        assert raised, "Expected FileNotFoundError"

    def test_cwd_set_for_directory(self, tmp_path: Path) -> None:
        """When target is a directory, cwd is set to that directory.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            run_ansible_lint(tmp_path)

        assert mock_run.call_args[1]["cwd"] == str(tmp_path)

    def test_cwd_none_for_file(self, tmp_path: Path) -> None:
        """When target is a file, cwd is None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        target_file = tmp_path / "playbook.yml"
        target_file.write_text("---\n- name: Test\n  hosts: all\n  tasks: []\n")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc) as mock_run:
            run_ansible_lint(target_file)

        assert mock_run.call_args[1]["cwd"] is None

    def test_stdout_and_stderr_captured(self, tmp_path: Path) -> None:
        """Both stdout and stderr are captured in result.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "some output"
        mock_proc.stderr = "some warning"

        with patch("apme_engine.lint_runner.subprocess.run", return_value=mock_proc):
            result = run_ansible_lint(tmp_path)

        assert result.stdout == "some output"
        assert result.stderr == "some warning"


class TestLintResult:
    """Tests for the LintResult dataclass."""

    def test_fields(self) -> None:
        """LintResult stores all expected fields."""
        r = LintResult(returncode=0, stdout="ok", stderr="", fixed=True)
        assert r.returncode == 0
        assert r.stdout == "ok"
        assert r.stderr == ""
        assert r.fixed is True
