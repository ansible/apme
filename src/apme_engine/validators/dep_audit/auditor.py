"""Run pip-audit against a session venv and map findings to ViolationDicts.

Uses pip-audit to check installed packages, then converts each vulnerability
to an APME ``ViolationDict`` with rule ID ``R200``. pip-audit uses PyPI by
default and accepts ``PIP_AUDIT_VULNERABILITY_SERVICE=osv`` in its environment.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from apme_engine.engine.models import ViolationDict

logger = logging.getLogger("apme.dep_audit")

PIP_AUDIT_BIN = "pip-audit"

RULE_ID_CVE = "R200"


def pip_audit_available() -> tuple[bool, str]:
    """Check whether pip-audit is on PATH.

    Returns:
        Tuple of ``(available, version_or_error)``.
    """
    try:
        proc = subprocess.run(
            [PIP_AUDIT_BIN, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        return False, f"pip-audit exited {proc.returncode}"
    except FileNotFoundError:
        return False, "pip-audit binary not found"
    except subprocess.TimeoutExpired:
        return False, "pip-audit --version timed out"


def _find_site_packages(venv_dir: Path) -> Path | None:
    """Locate the site-packages directory inside a venv.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        Path to site-packages, or None if not found.
    """
    lib_dir = venv_dir / "lib"
    if not lib_dir.is_dir():
        return None
    for pydir in lib_dir.iterdir():
        sp = pydir / "site-packages"
        if sp.is_dir():
            return sp
    return None


def run_pip_audit(
    venv_dir: Path,
    *,
    timeout: int = 120,
) -> list[ViolationDict]:
    """Run pip-audit against a session venv and return violation dicts.

    Args:
        venv_dir: Root of the virtual environment to audit.
        timeout: Subprocess timeout in seconds.

    Returns:
        List of ViolationDicts, one per vulnerability found.
    """
    site_packages = _find_site_packages(venv_dir)
    if site_packages is None:
        logger.warning("No site-packages found in %s", venv_dir)
        return []

    cmd = [
        PIP_AUDIT_BIN,
        "-f",
        "json",
        "--strict",
        "--progress-spinner=off",
        "--path",
        str(site_packages),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        logger.warning("pip-audit binary not found; skipping Python CVE audit")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("pip-audit timed out after %ds", timeout)
        return []

    stdout = proc.stdout.strip()
    if not stdout:
        if proc.returncode == 0:
            return []
        logger.warning("pip-audit exited %d with no output: %s", proc.returncode, proc.stderr[:500])
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logger.warning("pip-audit JSON parse error: %s", exc)
        return []

    return _convert_findings(data)


def _convert_findings(data: object) -> list[ViolationDict]:
    """Convert pip-audit JSON output to ViolationDicts.

    pip-audit ``--json`` emits ``{"dependencies": [...]}``.  Each dependency
    entry has ``name``, ``version``, and ``vulns`` (list of vulnerabilities).

    Args:
        data: Parsed JSON from pip-audit.

    Returns:
        List of ViolationDicts.
    """
    if not isinstance(data, dict):
        return []

    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list):
        return []

    violations: list[ViolationDict] = []
    for dep in dependencies:
        if not isinstance(dep, dict):
            continue
        pkg_name = str(dep.get("name", ""))
        pkg_version = str(dep.get("version", ""))
        vulns = dep.get("vulns", [])
        if not isinstance(vulns, list) or not vulns:
            continue

        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            cve_id = str(vuln.get("id", "unknown"))
            description = str(vuln.get("description", f"Vulnerability {cve_id}"))
            fix_versions_raw = vuln.get("fix_versions", [])
            fix_versions = ", ".join(str(v) for v in fix_versions_raw) if isinstance(fix_versions_raw, list) else ""

            aliases = vuln.get("aliases", [])
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str) and alias.startswith("CVE-"):
                        cve_id = alias
                        break

            severity = "medium"
            message = f"{pkg_name}=={pkg_version} has known vulnerability {cve_id}: {description}"

            violations.append(
                {
                    "rule_id": RULE_ID_CVE,
                    "severity": severity,
                    "message": message,
                    "file": "",
                    "line": 0,
                    "path": "",
                    "scope": "playbook",
                    "source": "dep_audit",
                    "cve_id": cve_id,
                    "dep_package": pkg_name,
                    "dep_installed_version": pkg_version,
                    "dep_fix_versions": fix_versions,
                }
            )

    return violations
