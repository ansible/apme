"""Run OPA on hierarchy payload and return violations. Uses Podman container or local opa binary."""

import json
import os
import subprocess
import sys
from pathlib import Path

from apme_engine.engine.models import ViolationDict, YAMLDict

OPA_IMAGE = "docker.io/openpolicyagent/opa:latest"

_MAX_CONSECUTIVE_TIMEOUTS = 3
_consecutive_timeouts = 0
_opa_disabled = False


def reset_opa_circuit_breaker() -> None:
    """Reset the timeout circuit-breaker so OPA eval is re-enabled."""
    global _consecutive_timeouts, _opa_disabled
    _consecutive_timeouts = 0
    _opa_disabled = False


def _run_opa_podman(
    input_str: str,
    bundle_path: Path,
    entrypoint: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run OPA via podman run with bundle mounted.

    Uses --userns=keep-id and -u root so the container can read the bind mount
    when the OPA image runs as non-root (rootless Podman). :z allows SELinux
    to relabel the mount for container read access.

    Args:
        input_str: JSON input for OPA eval.
        bundle_path: Path to OPA bundle directory.
        entrypoint: Rego entrypoint (e.g. data.apme.rules.violations).
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess from subprocess.run.
    """
    bundle_abs = bundle_path.resolve()
    cmd = [
        "podman",
        "run",
        "--rm",
        "-i",
        "--userns=keep-id",
        "-u",
        "root",
        "-v",
        f"{bundle_abs}:/bundle:ro,z",
        OPA_IMAGE,
        "eval",
        "-i",
        "-",
        "-d",
        "/bundle",
        entrypoint,
        "--format",
        "json",
    ]
    return subprocess.run(
        cmd,
        input=input_str,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_opa_local(
    input_str: str,
    bundle_path: Path,
    entrypoint: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run local opa binary.

    Args:
        input_str: JSON input for OPA eval.
        bundle_path: Path to OPA bundle directory.
        entrypoint: Rego entrypoint.
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess from subprocess.run.
    """
    return subprocess.run(
        ["opa", "eval", "-i", "-", "-d", str(bundle_path), entrypoint, "--format", "json"],
        input=input_str,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_opa_test_podman(bundle_path: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run `opa test . -v` inside Podman with bundle mounted.

    Same volume/user flags as eval.

    Args:
        bundle_path: Path to OPA bundle directory.
        timeout: Timeout in seconds (default 120).

    Returns:
        CompletedProcess from subprocess.run.
    """
    bundle_abs = bundle_path.resolve()
    cmd = [
        "podman",
        "run",
        "--rm",
        "--userns=keep-id",
        "-u",
        "root",
        "-v",
        f"{bundle_abs}:/bundle:ro,z",
        OPA_IMAGE,
        "test",
        "/bundle",
        "-v",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_opa_test_local(bundle_path: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run `opa test . -v` using local opa binary with cwd = bundle_path.

    Args:
        bundle_path: Path to OPA bundle directory.
        timeout: Timeout in seconds (default 120).

    Returns:
        CompletedProcess from subprocess.run.
    """
    return subprocess.run(
        ["opa", "test", ".", "-v"],
        cwd=str(bundle_path.resolve()),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_opa_test(bundle_path: str | Path, timeout: int = 120) -> tuple[bool, str, str]:
    """Run OPA Rego unit tests (`opa test . -v`) in the bundle directory.

    Uses Podman by default; set OPA_USE_PODMAN=0 to use a local opa binary.

    Args:
        bundle_path: Path to OPA bundle directory.
        timeout: Timeout in seconds (default 120).

    Returns:
        Tuple of (success, stdout, stderr).

    Raises:
        FileNotFoundError: If bundle_path is not a directory.
    """
    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"OPA bundle path is not a directory: {bundle_path}")
    use_podman = os.environ.get("OPA_USE_PODMAN", "1").lower() not in ("0", "false", "no")

    out = None
    if use_podman:
        try:
            out = _run_opa_test_podman(bundle, timeout)
        except FileNotFoundError:
            out = None
    if out is None:
        try:
            out = _run_opa_test_local(bundle, timeout)
        except FileNotFoundError:
            return (False, "", "podman and opa not found. Install one or set OPA_USE_PODMAN=1 and install podman.")
    return (out.returncode == 0, out.stdout or "", out.stderr or "")


def run_opa(
    input_data: YAMLDict, bundle_path: str, entrypoint: str = "data.apme.rules.violations"
) -> list[ViolationDict]:
    """Run OPA eval with input_data as input and bundle at bundle_path.

    Uses Podman container (openpolicyagent/opa) by default; set OPA_USE_PODMAN=0
    to use a local opa binary.

    Args:
        input_data: Hierarchy payload as YAML dict for OPA input.
        bundle_path: Path to OPA bundle directory.
        entrypoint: Rego entrypoint (default: data.apme.rules.violations).

    Returns:
        List of violation objects (each with rule_id, level, message, file, line, path).

    Raises:
        FileNotFoundError: If bundle_path is not a directory.
    """
    global _consecutive_timeouts, _opa_disabled

    if _opa_disabled:
        return []

    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"OPA bundle path is not a directory: {bundle_path}")
    input_str = json.dumps(input_data)
    timeout = 60
    use_podman = os.environ.get("OPA_USE_PODMAN", "1").lower() not in ("0", "false", "no")

    out = None
    if use_podman:
        try:
            out = _run_opa_podman(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            out = None  # fall back to local opa
        except subprocess.TimeoutExpired:
            _consecutive_timeouts += 1
            input_kb = len(input_str) / 1024
            if _consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                _opa_disabled = True
                sys.stderr.write(
                    f"OPA eval timed out {_consecutive_timeouts} consecutive times "
                    f"(input: {input_kb:.0f} KB). Disabling OPA validation for this run.\n"
                )
            else:
                sys.stderr.write(
                    f"OPA eval timed out after {timeout}s via Podman (input: {input_kb:.0f} KB) "
                    f"[{_consecutive_timeouts}/{_MAX_CONSECUTIVE_TIMEOUTS}].\n"
                )
            return []
    if out is None:
        try:
            out = _run_opa_local(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            if use_podman:
                sys.stderr.write(
                    "podman: command not found. Set OPA_USE_PODMAN=0 to use local opa, or install podman.\n"
                )
            else:
                sys.stderr.write(
                    "opa: command not found. Install OPA or set OPA_USE_PODMAN=1 to use the OPA container.\n"
                )
            return []
        except subprocess.TimeoutExpired:
            _consecutive_timeouts += 1
            input_kb = len(input_str) / 1024
            if _consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
                _opa_disabled = True
                sys.stderr.write(
                    f"OPA eval timed out {_consecutive_timeouts} consecutive times "
                    f"(input: {input_kb:.0f} KB). Disabling OPA validation for this run.\n"
                )
            else:
                sys.stderr.write(
                    f"OPA eval timed out after {timeout}s via local binary (input: {input_kb:.0f} KB) "
                    f"[{_consecutive_timeouts}/{_MAX_CONSECUTIVE_TIMEOUTS}].\n"
                )
            return []

    _consecutive_timeouts = 0

    if out.returncode != 0:
        sys.stderr.write(f"OPA eval failed: {out.stderr or out.stdout}\n")
        return []
    try:
        result = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"OPA returned invalid JSON: {out.stdout[:500]}\n")
        return []
    # OPA eval returns { "result": [ { "expressions": [ { "value": [...] } ] } ] }
    expressions = result.get("result", [])
    if not expressions:
        return []
    value = expressions[0].get("expressions", [{}])[0].get("value")
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return []
