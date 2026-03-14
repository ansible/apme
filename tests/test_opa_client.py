"""Tests for apme_engine.opa_client."""

import json
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.opa_client import reset_opa_circuit_breaker, run_opa, run_opa_test


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def _reset_circuit_breaker() -> None:
    """Reset the OPA timeout circuit-breaker before each test."""
    reset_opa_circuit_breaker()


class TestRunOpa:
    """Tests for run_opa()."""

    def test_bundle_not_directory_raises(self, tmp_path: Path) -> None:
        """Non-directory bundle path raises FileNotFoundError.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("x")
        with pytest.raises(FileNotFoundError, match="is not a directory"):
            run_opa({"hierarchy": []}, str(not_dir))

    def test_bundle_nonexistent_raises(self, tmp_path: Path) -> None:
        """Nonexistent bundle path raises FileNotFoundError.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        missing = tmp_path / "missing"
        with pytest.raises(FileNotFoundError, match="is not a directory"):
            run_opa({"hierarchy": []}, str(missing))

    def test_opa_not_found_returns_empty_list(self, opa_bundle_path: Path) -> None:
        """When opa command is not found, returns [] and writes to stderr.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.

        """
        with (
            patch("apme_engine.opa_client.subprocess.run", side_effect=FileNotFoundError("opa not found")),
            patch("sys.stderr.write") as mock_stderr,
        ):
            result = run_opa({"hierarchy": []}, str(opa_bundle_path))
        assert result == []
        mock_stderr.assert_called_once()
        assert "opa" in mock_stderr.call_args[0][0].lower()

    def test_opa_nonzero_exit_returns_empty_list(
        self, opa_bundle_path: Path, sample_hierarchy_payload: YAMLDict
    ) -> None:
        """When OPA returns non-zero exit code, returns [] and writes stderr.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="policy error")
            with patch("sys.stderr.write") as mock_stderr:
                result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []
        mock_stderr.assert_called_once()
        assert "policy error" in mock_stderr.call_args[0][0]

    def test_opa_invalid_json_returns_empty_list(
        self, opa_bundle_path: Path, sample_hierarchy_payload: YAMLDict
    ) -> None:
        """When OPA stdout is not valid JSON, returns [] and writes stderr.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
            with patch("sys.stderr.write") as mock_stderr:
                result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []
        mock_stderr.assert_called_once()
        assert "invalid JSON" in mock_stderr.call_args[0][0]

    def test_opa_empty_result_returns_empty_list(
        self,
        opa_bundle_path: Path,
        sample_hierarchy_payload: YAMLDict,
        opa_eval_result_empty: YAMLDict,
    ) -> None:
        """When OPA result has no expressions, returns [].

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_empty: Fixture providing empty OPA eval result.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"result": []}), stderr="")
            result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []

    def test_opa_value_none_returns_empty_list(self, opa_bundle_path: Path, sample_hierarchy_payload: YAMLDict) -> None:
        """When expressions[0].expressions[0].value is None, returns [].

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"result": [{"expressions": [{"value": None}]}]}),
                stderr="",
            )
            result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []

    def test_opa_value_not_list_returns_empty_list(
        self, opa_bundle_path: Path, sample_hierarchy_payload: YAMLDict
    ) -> None:
        """When value is not a list (e.g. object), returns [].

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"result": [{"expressions": [{"value": {"foo": "bar"}}]}]}),
                stderr="",
            )
            result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []

    def test_opa_success_returns_violations_list(
        self,
        opa_bundle_path: Path,
        sample_hierarchy_payload: YAMLDict,
        opa_eval_result_with_violations: YAMLDict,
    ) -> None:
        """When OPA returns valid result with violations, returns that list.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_with_violations: Fixture providing OPA eval result with violations.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(opa_eval_result_with_violations),
                stderr="",
            )
            result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert len(result) == 1
        assert result[0]["rule_id"] == "task-name"
        assert result[0]["level"] == "warning"
        assert result[0]["file"] == "/examples/pb.yml"
        assert result[0]["line"] == 5

    def test_opa_success_empty_violations(
        self,
        opa_bundle_path: Path,
        sample_hierarchy_payload: YAMLDict,
        opa_eval_result_empty: YAMLDict,
    ) -> None:
        """When OPA returns valid result with empty value list, returns [].

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_empty: Fixture providing empty OPA eval result.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(opa_eval_result_empty),
                stderr="",
            )
            result = run_opa(sample_hierarchy_payload, str(opa_bundle_path))
        assert result == []

    def test_opa_custom_entrypoint(
        self,
        opa_bundle_path: Path,
        sample_hierarchy_payload: dict[str, object],
        opa_eval_result_empty: dict[str, object],
    ) -> None:
        """run_opa passes custom entrypoint to opa eval.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_empty: Fixture providing empty OPA eval result.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(opa_eval_result_empty), stderr="")
            run_opa(
                cast(YAMLDict, sample_hierarchy_payload),
                str(opa_bundle_path),
                entrypoint="data.custom.violations",
            )
        call_args = mock_run.call_args[0][0]
        assert "data.custom.violations" in call_args

    def test_opa_input_passed_via_stdin(
        self,
        opa_bundle_path: Path,
        sample_hierarchy_payload: dict[str, object],
        opa_eval_result_empty: dict[str, object],
    ) -> None:
        """Input JSON is passed to opa via stdin.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_empty: Fixture providing empty OPA eval result.

        """
        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(opa_eval_result_empty), stderr="")
            run_opa(cast(YAMLDict, sample_hierarchy_payload), str(opa_bundle_path))
        kwargs = mock_run.call_args[1]
        assert kwargs["input"] == json.dumps(sample_hierarchy_payload)

    def test_timeout_circuit_breaker_disables_after_3(self, opa_bundle_path: Path) -> None:
        """After 3 consecutive timeouts, run_opa short-circuits without calling subprocess.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.

        """
        import apme_engine.opa_client as mod

        with (
            patch.dict("os.environ", {"OPA_USE_PODMAN": "0"}),
            patch("apme_engine.opa_client.subprocess.run", side_effect=subprocess.TimeoutExpired("opa", 60)),
            patch("sys.stderr.write"),
        ):
            for _ in range(3):
                run_opa({"hierarchy": []}, str(opa_bundle_path))

        assert mod._opa_disabled is True
        assert mod._consecutive_timeouts == 3

        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            result = run_opa({"hierarchy": []}, str(opa_bundle_path))
        assert result == []
        mock_run.assert_not_called()

    def test_successful_call_resets_timeout_counter(
        self, opa_bundle_path: Path, sample_hierarchy_payload: YAMLDict, opa_eval_result_empty: YAMLDict
    ) -> None:
        """A successful OPA call resets the consecutive timeout counter to 0.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.
            sample_hierarchy_payload: Fixture providing sample hierarchy data.
            opa_eval_result_empty: Fixture providing empty OPA eval result.

        """
        import apme_engine.opa_client as mod

        with (
            patch.dict("os.environ", {"OPA_USE_PODMAN": "0"}),
            patch("apme_engine.opa_client.subprocess.run", side_effect=subprocess.TimeoutExpired("opa", 60)),
            patch("sys.stderr.write"),
        ):
            run_opa({"hierarchy": []}, str(opa_bundle_path))
            run_opa({"hierarchy": []}, str(opa_bundle_path))

        assert mod._consecutive_timeouts == 2

        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(opa_eval_result_empty), stderr="")
            run_opa(sample_hierarchy_payload, str(opa_bundle_path))

        assert mod._consecutive_timeouts == 0
        assert mod._opa_disabled is False

    def test_reset_circuit_breaker_re_enables(self, opa_bundle_path: Path) -> None:
        """reset_opa_circuit_breaker re-enables OPA after it was disabled by timeouts.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.

        """
        import apme_engine.opa_client as mod

        mod._opa_disabled = True
        mod._consecutive_timeouts = 3

        reset_opa_circuit_breaker()

        assert mod._opa_disabled is False
        assert mod._consecutive_timeouts == 0

        with patch("apme_engine.opa_client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"result": []}), stderr="")
            result = run_opa({"hierarchy": []}, str(opa_bundle_path))
        mock_run.assert_called_once()
        assert result == []


class TestRunOpaTest:
    """Tests for run_opa_test() — runs OPA Rego unit tests in the bundle."""

    def test_opa_bundle_rego_tests_pass(self, opa_bundle_path: Path) -> None:
        """Run `opa test . -v` in the bundle (Podman or local opa). All Rego tests must pass.

        Args:
            opa_bundle_path: Fixture providing path to OPA bundle.

        """
        success, stdout, stderr = run_opa_test(opa_bundle_path)
        if not success and "not found" in stderr.lower():
            pytest.skip("podman and opa not available; install one to run OPA bundle tests")
        assert success, f"OPA Rego tests failed.\nstdout:\n{stdout}\nstderr:\n{stderr}"

    def test_run_opa_test_bundle_not_directory_raises(self, tmp_path: Path) -> None:
        """Non-directory bundle path raises FileNotFoundError.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("x")
        with pytest.raises(FileNotFoundError, match="is not a directory"):
            run_opa_test(not_dir)

    def test_run_opa_test_bundle_nonexistent_raises(self, tmp_path: Path) -> None:
        """Nonexistent bundle path raises FileNotFoundError.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        missing = tmp_path / "missing"
        with pytest.raises(FileNotFoundError, match="is not a directory"):
            run_opa_test(missing)
