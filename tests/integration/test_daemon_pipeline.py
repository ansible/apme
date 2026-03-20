"""Full daemon integration test: CLI -> gRPC -> Primary -> validators.

Proves the FQCN collection auto-discovery pipeline (ADR-032) works
end-to-end using the ``terrible-playbook`` fixture.  ``ansible.posix`` is
intentionally omitted from ``requirements.yml``; L058/L059 can only fire
if the collection was auto-discovered from FQCNs and installed.

Run with::

    pytest -m integration tests/integration/ -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.models import ViolationDict, YAMLDict

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "terrible-playbook"


def _scan_json(fixture_dir: Path) -> YAMLDict:
    """Scan the fixture directory and return parsed JSON.

    Args:
        fixture_dir: Path to the Ansible project to scan.

    Returns:
        Parsed JSON dict from scan output.
    """
    r = subprocess.run(
        [sys.executable, "-m", "apme_engine.cli", "scan", "--json", "--timeout", "300", str(fixture_dir)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"Scan exited {r.returncode}:\nstdout: {r.stdout[:2000]}\nstderr: {r.stderr[:2000]}"
    try:
        return cast(YAMLDict, json.loads(r.stdout))
    except json.JSONDecodeError:
        pytest.fail(f"Scan output not valid JSON:\n{r.stdout[:2000]}")
        return {}  # unreachable


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_data(infrastructure: object) -> YAMLDict:
    """Scan terrible-playbook once and cache for all tests in this module.

    Args:
        infrastructure: Daemon infrastructure fixture (ensures daemon is up).

    Returns:
        Parsed scan JSON.
    """
    return _scan_json(FIXTURE_DIR)


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_posix_argspec_violation(scan_data: YAMLDict) -> None:
    """L058/L059 fires for ansible.posix.sysctl with bogus_param (ADR-032 proof).

    ``ansible.posix`` is intentionally omitted from ``requirements.yml``.
    This can only fire if the collection was auto-discovered from FQCNs
    and installed by the daemon's collection cache.

    Args:
        scan_data: Parsed scan result.
    """
    violations = cast(list[ViolationDict], scan_data.get("violations", []))
    posix_violations = [v for v in violations if "ansible.posix.sysctl" in str(v.get("message", ""))]
    argspec_hits = [v for v in posix_violations if v.get("rule_id") in ("L058", "L059")]
    assert argspec_hits, (
        "Expected L058/L059 for ansible.posix.sysctl bogus_param — "
        "auto-discovery may not have installed the collection.\n"
        f"All rule_ids: {sorted({str(v.get('rule_id', '')) for v in violations})}"
    )
