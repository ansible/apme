"""Run OPA on hierarchy payload and return violations.

This module evaluates hierarchy payloads against Rego policies using either a
Podman container (openpolicyagent/opa) or a local ``opa`` binary.

A timeout-based circuit-breaker avoids repeatedly running OPA when evaluations
consistently time out. After a configurable number of consecutive timeouts
(see ``APME_OPA_MAX_CONSECUTIVE_TIMEOUTS``), OPA evaluation is temporarily
disabled and :func:`run_opa` raises :exc:`OpaInfrastructureError`. A successful
call resets the counter. Use :func:`reset_opa_circuit_breaker` to clear the
counter and re-enable OPA evaluation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from apme_engine.engine.models import ViolationDict, YAMLDict

OPA_IMAGE = "docker.io/openpolicyagent/opa:1.17.1"

_consecutive_timeouts = 0
_opa_disabled = False


class OpaInfrastructureError(RuntimeError):
    """OPA evaluation failed due to infrastructure or runtime errors."""


def _max_consecutive_timeouts() -> int:
    """Return max consecutive timeouts before disabling OPA (from env or default 3).

    Returns:
        Positive int from APME_OPA_MAX_CONSECUTIVE_TIMEOUTS, or 3 if unset/invalid.
    """
    raw = os.environ.get("APME_OPA_MAX_CONSECUTIVE_TIMEOUTS", "3")
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return 3


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
        "-I",
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
        ["opa", "eval", "-I", "-d", str(bundle_path), entrypoint, "--format", "json"],
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


def opa_eval_unavailable_reason(
    bundle_path: str | Path,
    entrypoint: str = "data.apme.rules.violations",
    timeout: int = 60,
) -> str | None:
    """Return a reason when OPA eval cannot complete, else None.

    Args:
        bundle_path: Path to OPA bundle directory.
        entrypoint: Rego entrypoint for the probe eval.
        timeout: Timeout in seconds.

    Returns:
        Error message when eval fails, otherwise None.
    """
    if _opa_disabled:
        return "OPA evaluation disabled by circuit breaker"

    bundle = Path(bundle_path)
    if not bundle.is_dir():
        return f"OPA bundle path is not a directory: {bundle_path}"

    input_str = json.dumps({"hierarchy": []})
    use_podman = os.environ.get("OPA_USE_PODMAN", "1").lower() not in ("0", "false", "no")

    out: subprocess.CompletedProcess[str] | None = None
    if use_podman:
        try:
            out = _run_opa_podman(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            out = None
        except subprocess.TimeoutExpired:
            return f"OPA eval timed out after {timeout}s via Podman"
    if out is None:
        try:
            out = _run_opa_local(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            if use_podman:
                return "podman: command not found. Set OPA_USE_PODMAN=0 to use local opa, or install podman."
            return "opa: command not found. Install OPA or set OPA_USE_PODMAN=1 to use the OPA container."
        except subprocess.TimeoutExpired:
            return f"OPA eval timed out after {timeout}s via local binary"

    if out.returncode != 0:
        return (out.stderr or out.stdout or "OPA eval failed").strip()
    try:
        json.loads(out.stdout)
    except json.JSONDecodeError:
        return f"OPA returned invalid JSON: {(out.stdout or '')[:200]}"
    return None


def run_opa(
    input_data: YAMLDict, bundle_path: str, entrypoint: str = "data.apme.rules.violations"
) -> list[ViolationDict]:
    """Run OPA eval with input_data as input and bundle at bundle_path.

    Uses Podman container (openpolicyagent/opa) by default; set ``OPA_USE_PODMAN=0``
    to use a local ``opa`` binary.

    A timeout-based circuit-breaker is applied. If evaluations time out
    consecutively (see ``APME_OPA_MAX_CONSECUTIVE_TIMEOUTS``, default 3), OPA
    evaluation is temporarily disabled for the process. This function raises
    :class:`OpaInfrastructureError` without invoking OPA. Call
    :func:`reset_opa_circuit_breaker` to re-enable.

    Args:
        input_data: Hierarchy payload as YAML dict for OPA input.
        bundle_path: Path to OPA bundle directory.
        entrypoint: Rego entrypoint (default: ``data.apme.rules.violations``).

    Returns:
        List of violation objects (each with ``rule_id``, ``level``, ``message``,
        ``file``, ``line``, ``path``). An empty list means OPA found no violations.

    Raises:
        FileNotFoundError: If bundle_path is not a directory.
        OpaInfrastructureError: If OPA is unavailable, evaluation fails, or the
            circuit breaker has disabled evaluation.
    """
    global _consecutive_timeouts, _opa_disabled

    if _opa_disabled:
        raise OpaInfrastructureError("OPA evaluation is disabled by the circuit breaker")

    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise FileNotFoundError(f"OPA bundle path is not a directory: {bundle_path}")
    input_str = json.dumps(input_data)
    timeout = 60
    max_timeouts = _max_consecutive_timeouts()
    use_podman = os.environ.get("OPA_USE_PODMAN", "1").lower() not in ("0", "false", "no")

    def _on_timeout(via: str) -> None:
        global _consecutive_timeouts, _opa_disabled
        _consecutive_timeouts += 1
        input_kb = len(input_str) / 1024
        if _consecutive_timeouts >= max_timeouts:
            _opa_disabled = True
            sys.stderr.write(
                f"OPA eval timed out {_consecutive_timeouts} consecutive times "
                f"(input: {input_kb:.0f} KB). Disabling OPA validation for this run.\n"
            )
        else:
            sys.stderr.write(
                f"OPA eval timed out after {timeout}s via {via} (input: {input_kb:.0f} KB) "
                f"[{_consecutive_timeouts}/{max_timeouts}].\n"
            )

    out = None
    if use_podman:
        try:
            out = _run_opa_podman(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            out = None  # fall back to local opa
        except subprocess.TimeoutExpired:
            _on_timeout("Podman")
            raise OpaInfrastructureError("OPA evaluation timed out via Podman") from None
    if out is None:
        try:
            out = _run_opa_local(input_str, bundle, entrypoint, timeout)
        except FileNotFoundError:
            _consecutive_timeouts = 0
            if use_podman:
                sys.stderr.write(
                    "podman: command not found. Set OPA_USE_PODMAN=0 to use local opa, or install podman.\n"
                )
            else:
                sys.stderr.write(
                    "opa: command not found. Install OPA or set OPA_USE_PODMAN=1 to use the OPA container.\n"
                )
            raise OpaInfrastructureError("OPA binary is not available") from None
        except subprocess.TimeoutExpired:
            _on_timeout("local binary")
            raise OpaInfrastructureError("OPA evaluation timed out via local binary") from None

    _consecutive_timeouts = 0

    if out.returncode != 0:
        sys.stderr.write("OPA eval failed: [REDACTED]\n")
        raise OpaInfrastructureError("OPA evaluation returned a non-zero exit code")
    try:
        result = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("OPA returned invalid JSON: [REDACTED]\n")
        raise OpaInfrastructureError("OPA evaluation returned invalid JSON") from None
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
