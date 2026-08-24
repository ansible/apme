"""End-to-end integration test: rebuild containers, start pod, run scan, assert violations.

Requires Podman and built images. Mark with ``@pytest.mark.integration`` so it
can be skipped in normal ``pytest`` runs (use ``pytest -m integration``).

Options via environment variables:
    APME_E2E_SKIP_BUILD=1      skip image rebuild
    APME_E2E_SKIP_TEARDOWN=1   leave the pod running after the test
"""

import json
import os
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.models import ViolationDict, YAMLDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
POD_NAME = "apme-pod"
CLI_IMAGE = "apme-cli:latest"
ENGINE_ADDR = "127.0.0.1:50051"
POD_STARTUP_TIMEOUT = 90
SERVICE_SETTLE_SECONDS = 5


def _run(cmd: list[str], **kwargs: str | bool | int | float | None) -> subprocess.CompletedProcess[str]:
    """Run command and return CompletedProcess.

    Args:
        cmd: Command and args.
        **kwargs: Passed to subprocess.run.

    Returns:
        CompletedProcess with stdout, stderr, returncode.
    """
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # type: ignore[arg-type]


def _podman(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run podman command.

    Args:
        *args: Podman subcommand and args.
        check: If True, raise on non-zero exit.

    Returns:
        CompletedProcess.
    """
    return _run(["podman", *args], check=check)


def _pod_status() -> str:
    """Return pod status string.

    Returns:
        Pod status or empty string.
    """
    r = _podman("pod", "list", "--filter", f"name={POD_NAME}", "--format", "{{.Status}}", check=False)
    return (r.stdout or "").strip()


def _container_logs(name: str) -> str:
    """Return pod container logs.

    Args:
        name: Container name (e.g. 'engine', 'native').

    Returns:
        Combined stdout and stderr.
    """
    r = _podman("logs", f"{POD_NAME}-{name}", check=False)
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def pod() -> Generator[None, None, None]:
    """Build images (unless skipped), start the pod, yield, then tear down."""
    skip_build = os.environ.get("APME_E2E_SKIP_BUILD", "0") == "1"
    skip_teardown = os.environ.get("APME_E2E_SKIP_TEARDOWN", "0") == "1"

    if not skip_build:
        result = _run(["bash", str(REPO_ROOT / "containers" / "podman" / "build.sh")], cwd=str(REPO_ROOT))
        assert result.returncode == 0, f"Image build failed:\n{result.stderr}"

    _podman("pod", "rm", "-f", POD_NAME, check=False)
    result = _run(
        ["podman", "play", "kube", str(REPO_ROOT / "containers" / "podman" / "pod.yaml")],
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"Pod creation failed:\n{result.stderr}"

    deadline = time.time() + POD_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _pod_status() == "Running":
            break
        time.sleep(1)
    else:
        logs = _podman("pod", "logs", POD_NAME, check=False)
        pytest.fail(f"Pod did not reach Running within {POD_STARTUP_TIMEOUT}s.\n{logs.stdout}\n{logs.stderr}")

    time.sleep(SERVICE_SETTLE_SECONDS)
    yield

    if not skip_teardown:
        _podman("pod", "rm", "-f", POD_NAME, check=False)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_result(pod: None) -> YAMLDict:
    """Run a scan of the test playbook via the CLI container and return parsed JSON.

    Args:
        pod: Fixture that starts and tears down the podman pod.

    Returns:
        Parsed JSON scan result (violations, count, etc.).
    """
    test_dir = str(REPO_ROOT / "tests" / "integration")
    r = _run(
        [
            "podman",
            "run",
            "--rm",
            "--pod",
            POD_NAME,
            "-v",
            f"{test_dir}:/workspace:ro,Z",
            "-w",
            "/workspace",
            "-e",
            f"APME_ENGINE_ADDRESS={ENGINE_ADDR}",
            "--entrypoint",
            "apme",
            CLI_IMAGE,
            "check",
            "--json",
            ".",
        ]
    )
    assert r.returncode == 0, f"Scan failed (rc={r.returncode}):\n{r.stdout}\n{r.stderr}"
    try:
        return cast(YAMLDict, json.loads(r.stdout))
    except json.JSONDecodeError:
        pytest.fail(f"Scan output is not valid JSON:\n{r.stdout}")
        return {}  # unreachable; pytest.fail raises


def _violation_rule_ids(scan_result: YAMLDict) -> set[str]:
    """Extract set of rule IDs from scan violations.

    Args:
        scan_result: Parsed scan JSON.

    Returns:
        Set of rule_id strings.
    """
    violations = cast(list[ViolationDict], scan_result.get("violations", []))
    return {str(v.get("rule_id", "")) for v in violations}


# ---------------------------------------------------------------------------
# Phase 3: Health check
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHealthCheck:
    """Tests for health-check subcommand."""

    def test_overall_ok(self, pod: None) -> None:
        """Health-check reports overall: ok when all services up.

        Args:
            pod: Fixture that starts and tears down the podman pod.

        """
        r = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-e",
                f"APME_ENGINE_ADDRESS={ENGINE_ADDR}",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "health-check",
            ]
        )
        combined = r.stdout + r.stderr
        assert "overall: ok" in combined, f"Health check did not report ok:\n{combined}"


# ---------------------------------------------------------------------------
# Phase 4+5: Scan and assert expected violations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScanViolations:
    """Tests for scan violations."""

    def test_violations_returned(self, scan_result: YAMLDict) -> None:
        """Scan returns violations.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        raw_count = scan_result.get("count", 0)
        count = int(raw_count) if isinstance(raw_count, int | float | str) else 0
        assert count > 0, f"Expected >0 violations, got {count}"

    # OPA rules
    @pytest.mark.parametrize(
        "rule_id,desc",
        [
            ("L007", "shell when command suffices"),
            ("L010", "ignore_errors without register"),
            ("L021", "missing explicit mode on file/copy"),
            ("L025", "name not starting uppercase"),
            ("R118", "inbound transfer (annotation-based)"),
        ],
    )  # type: ignore[untyped-decorator]
    def test_opa_rule(self, scan_result: YAMLDict, rule_id: str, desc: str) -> None:
        """OPA rule fires for test playbook.

        Args:
            scan_result: Fixture providing scan result dict.
            rule_id: Parametrized rule ID.
            desc: Parametrized rule description.

        """
        assert rule_id in _violation_rule_ids(scan_result), f"{rule_id} ({desc}) not found"

    # Native rules
    @pytest.mark.parametrize(
        "rule_id,desc",
        [
            ("native:L046", "free-form args"),
        ],
    )  # type: ignore[untyped-decorator]
    def test_native_rule(self, scan_result: YAMLDict, rule_id: str, desc: str) -> None:
        """Native rule fires for test playbook.

        Args:
            scan_result: Fixture providing scan result dict.
            rule_id: Parametrized rule ID.
            desc: Parametrized rule description.

        """
        assert rule_id in _violation_rule_ids(scan_result), f"{rule_id} ({desc}) not found"

    # Modernize rules
    def test_m001_fqcn_resolution(self, scan_result: YAMLDict) -> None:
        """M001 FQCN resolution rule fires.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        assert "M001" in _violation_rule_ids(scan_result), "M001 (FQCN resolution) not found"

    # Ansible validator rules
    @pytest.mark.parametrize(
        "rule_id,desc",
        [
            ("L058", "argspec validation - docstring"),
            ("L059", "argspec validation - mock/patch"),
        ],
    )  # type: ignore[untyped-decorator]
    def test_ansible_rule(self, scan_result: YAMLDict, rule_id: str, desc: str) -> None:
        """Ansible validator rule fires.

        Args:
            scan_result: Fixture providing scan result dict.
            rule_id: Parametrized rule ID.
            desc: Parametrized rule description.

        """
        assert rule_id in _violation_rule_ids(scan_result), f"{rule_id} ({desc}) not found"


# ---------------------------------------------------------------------------
# Phase 6: No duplicates
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNoDuplicates:
    """Tests for violation deduplication."""

    def test_no_duplicate_violations(self, scan_result: YAMLDict) -> None:
        """Scan returns no duplicate violations.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        seen: set[tuple[str, str, int | tuple[int, ...]]] = set()
        dups: list[tuple[str, str, int | tuple[int, ...]]] = []
        violations = cast(list[ViolationDict], scan_result.get("violations", []))
        for v in violations:
            line = v.get("line")
            if isinstance(line, list):
                line = tuple(line)  # type: ignore[assignment]
            key = (
                str(v.get("rule_id", "")),
                str(v.get("file", "")),
                line if isinstance(line, int | tuple) else 0,
            )
            if key in seen:
                dups.append(key)
            seen.add(key)
        assert len(dups) == 0, f"Found {len(dups)} duplicate(s): {dups}"


# ---------------------------------------------------------------------------
# Phase 7-9: Container logs
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContainerLogs:
    """Tests for container log content."""

    def test_engine_logged_opa_count(self, scan_result: YAMLDict) -> None:
        """Engine logs OPA count.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("engine")
        assert "Opa=" in logs, f"Engine did not log OPA count:\n{logs[-500:]}"

    def test_engine_logged_native_count(self, scan_result: YAMLDict) -> None:
        """Engine logs Native count.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("engine")
        assert "Native=" in logs, f"Engine did not log Native count:\n{logs[-500:]}"

    def test_opa_wrapper_logged(self, scan_result: YAMLDict) -> None:
        """OPA wrapper logs OPA returned.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("opa")
        assert "OPA returned" in logs, f"OPA wrapper did not log:\n{logs[-500:]}"

    def test_native_validator_logged(self, scan_result: YAMLDict) -> None:
        """Native validator logs return message.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("native")
        assert "Native validator returned" in logs, f"Native did not log:\n{logs[-500:]}"

    def test_ansible_introspection(self, scan_result: YAMLDict) -> None:
        """AnsibleValidator logs introspecting.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("ansible")
        assert "introspecting" in logs, f"Ansible did not run introspection:\n{logs[-500:]}"

    def test_ansible_argspec(self, scan_result: YAMLDict) -> None:
        """AnsibleValidator logs argspec.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("ansible")
        assert "argspec" in logs, f"Ansible did not run argspec:\n{logs[-500:]}"


# ---------------------------------------------------------------------------
# Phase 10: Gitleaks validator (container-level)
# ---------------------------------------------------------------------------

SECRETS_FIXTURE = REPO_ROOT / "tests" / "integration" / "test_secrets_playbook.yml"


@pytest.mark.integration
class TestGitleaks:
    """Tests for Gitleaks validator."""

    def test_gitleaks_container_healthy(self, pod: None) -> None:
        """Gitleaks reported in health-check.

        Args:
            pod: Fixture that starts and tears down the podman pod.

        """
        r = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-e",
                f"APME_ENGINE_ADDRESS={ENGINE_ADDR}",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "health-check",
            ]
        )
        combined = r.stdout + r.stderr
        assert "gitleaks" in combined.lower() or "overall: ok" in combined, (
            f"Gitleaks not reported in health check:\n{combined}"
        )

    def test_gitleaks_logged(self, scan_result: YAMLDict) -> None:
        """Gitleaks container logs validator message.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("gitleaks")
        assert "Gitleaks validator" in logs, f"Gitleaks did not log:\n{logs[-500:]}"

    def test_engine_logged_gitleaks_count(self, scan_result: YAMLDict) -> None:
        """Engine logs Gitleaks count.

        Args:
            scan_result: Fixture providing scan result dict.

        """
        logs = _container_logs("engine")
        assert "Gitleaks=" in logs, f"Engine did not log Gitleaks count:\n{logs[-500:]}"


# ---------------------------------------------------------------------------
# Phase 11: Format subcommand (container-level)
# ---------------------------------------------------------------------------

FORMAT_FIXTURE = REPO_ROOT / "tests" / "integration" / "test_format_playbook.yml"


@pytest.mark.integration
class TestFormat:
    """Tests for format subcommand in container."""

    def test_format_check_detects_issues(self, pod: None) -> None:
        """Format --check on messy fixture exits 1.

        Args:
            pod: Fixture that starts and tears down the podman pod.

        """
        test_dir = str(FORMAT_FIXTURE.parent)
        r = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-v",
                f"{test_dir}:/workspace:ro,Z",
                "-w",
                "/workspace",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "format",
                "--check",
                "test_format_playbook.yml",
            ]
        )
        assert r.returncode == 1, f"Expected exit 1 for messy file:\n{r.stdout}\n{r.stderr}"

    def test_format_diff_shows_transforms(self, pod: None) -> None:
        """Format (no --apply) shows unified diff with expected transforms.

        Args:
            pod: Fixture that starts and tears down the podman pod.

        """
        test_dir = str(FORMAT_FIXTURE.parent)
        r = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-v",
                f"{test_dir}:/workspace:ro,Z",
                "-w",
                "/workspace",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "format",
                "test_format_playbook.yml",
            ]
        )
        assert r.returncode == 0
        assert "@@" in r.stdout or "---" in r.stdout, f"Expected unified diff output:\n{r.stdout[:500]}"

    def test_format_apply_then_check_passes(self, pod: None, tmp_path: Path) -> None:
        """Format --apply writes file; subsequent --check exits 0.

        Args:
            pod: Fixture that starts and tears down the podman pod.
            tmp_path: Pytest temporary directory fixture.

        """
        import shutil

        work = tmp_path / "fmt_test"
        work.mkdir()
        shutil.copy2(FORMAT_FIXTURE, work / "play.yml")
        r = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-v",
                f"{work}:/workspace:Z",
                "-w",
                "/workspace",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "format",
                "--apply",
                "play.yml",
            ]
        )
        assert r.returncode == 0, f"format --apply failed:\n{r.stderr}"

        r2 = _run(
            [
                "podman",
                "run",
                "--rm",
                "--pod",
                POD_NAME,
                "-v",
                f"{work}:/workspace:ro,Z",
                "-w",
                "/workspace",
                "--entrypoint",
                "apme",
                CLI_IMAGE,
                "format",
                "--check",
                "play.yml",
            ]
        )
        assert r2.returncode == 0, f"format --check failed after --apply:\n{r2.stderr}"
