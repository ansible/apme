"""Run ansible-lint as a subprocess with profile and config support."""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LintResult:
    """Result of an ansible-lint invocation.

    Attributes:
        returncode: Process exit code (0 = clean, 2 = violations found).
        stdout: Captured standard output.
        stderr: Captured standard error.
        fixed: Whether --fix mode was used.
    """

    returncode: int
    stdout: str
    stderr: str
    fixed: bool


def run_ansible_lint(
    target: Path,
    *,
    fix: bool = False,
    profile: str = "production",
    config_file: str | None = None,
) -> LintResult:
    """Run ansible-lint on a target path.

    Args:
        target: File or directory to lint.
        fix: If True, pass --fix to auto-correct violations.
        profile: ansible-lint profile name (default: production).
        config_file: Optional path to an ansible-lint config file (-c).

    Returns:
        LintResult with exit code and captured output.

    Raises:
        FileNotFoundError: If ansible-lint is not installed.
    """
    cmd: list[str] = ["ansible-lint"]

    if fix:
        cmd.append("--fix")

    cmd.extend(["--profile", profile])

    if config_file:
        cmd.extend(["-c", config_file])

    cmd.append(str(target))

    sys.stderr.write(f"  Running: {' '.join(cmd)}\n")
    sys.stderr.flush()

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(target) if target.is_dir() else None)
    except FileNotFoundError:
        raise FileNotFoundError("ansible-lint is not installed. Install it with: pip install ansible-lint") from None

    return LintResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        fixed=fix,
    )
