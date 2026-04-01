"""Full daemon integration test: CLI -> gRPC -> Primary -> validators -> gateway.

Proves the FQCN collection auto-discovery pipeline (ADR-032) works
end-to-end using the ``terrible-playbook`` fixture.  ``ansible.posix`` is
intentionally omitted from ``requirements.yml``; L058/L059 can only fire
if the collection was auto-discovered from FQCNs and installed.

Also proves the ADR-020 reporting pipeline: scan events emitted by the engine
are persisted to the gateway's SQLite database and served by the REST API.

Run with::

    pytest -m integration tests/integration/ -v
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from apme_engine.engine.models import ViolationDict, YAMLDict

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "terrible-playbook"

_SESSION_ID = "integ-test-session"


def _scan_json(fixture_dir: Path) -> tuple[YAMLDict, str]:
    """Scan the fixture directory and return parsed JSON plus stderr logs.

    Args:
        fixture_dir: Path to the Ansible project to scan.

    Returns:
        Tuple of (parsed JSON dict, stderr log output).
    """
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "apme_engine.cli",
            "check",
            "--json",
            "-v",
            "--session",
            _SESSION_ID,
            "--timeout",
            "300",
            str(fixture_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"Scan exited {r.returncode}:\nstdout: {r.stdout[:2000]}\nstderr: {r.stderr[:4000]}"
    try:
        return cast(YAMLDict, json.loads(r.stdout)), r.stderr
    except json.JSONDecodeError:
        pytest.fail(f"Scan output not valid JSON:\n{r.stdout[:2000]}\nstderr: {r.stderr[:4000]}")
        return {}, ""  # unreachable


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_result(infrastructure: object) -> tuple[YAMLDict, str]:
    """Scan terrible-playbook once and cache for all tests in this module.

    Args:
        infrastructure: Daemon infrastructure fixture (ensures daemon is up).

    Returns:
        Tuple of (parsed scan JSON, stderr log output).
    """
    return _scan_json(FIXTURE_DIR)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_data(scan_result: tuple[YAMLDict, str]) -> YAMLDict:
    """Parsed scan JSON (convenience alias).

    Args:
        scan_result: Tuple from scan_result fixture.

    Returns:
        Parsed scan JSON dict.
    """
    return scan_result[0]


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_stderr(scan_result: tuple[YAMLDict, str]) -> str:
    """Stderr log output from the scan (convenience alias).

    Args:
        scan_result: Tuple from scan_result fixture.

    Returns:
        Stderr string from the scan subprocess.
    """
    return scan_result[1]


def _scan_verbose(fixture_dir: Path) -> subprocess.CompletedProcess[str]:
    """Scan the fixture directory with -v (human-readable, not JSON).

    Args:
        fixture_dir: Path to the Ansible project to scan.

    Returns:
        CompletedProcess with stdout (table output) and stderr (milestone logs).
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "apme_engine.cli",
            "check",
            "-v",
            "--session",
            _SESSION_ID,
            "--timeout",
            "300",
            str(fixture_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def scan_verbose(infrastructure: object) -> subprocess.CompletedProcess[str]:
    """Scan terrible-playbook with -v and cache for all tests in this module.

    Args:
        infrastructure: Daemon infrastructure fixture (ensures daemon is up).

    Returns:
        CompletedProcess with stdout and stderr.
    """
    return _scan_verbose(FIXTURE_DIR)


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_milestone_logs_displayed(scan_verbose: subprocess.CompletedProcess[str]) -> None:
    """With -v the CLI renders pipeline milestone logs on stderr in real time.

    The check command uses FixSession (ADR-039) which streams
    ``SessionEvent(progress=...)`` as milestones are reached.
    This test verifies that key milestones are visible to the user.

    Args:
        scan_verbose: Completed scan process with -v output.
    """
    assert scan_verbose.returncode == 0, (
        f"Check exited {scan_verbose.returncode}:\n"
        f"stdout: {scan_verbose.stdout[:2000]}\n"
        f"stderr: {scan_verbose.stderr[:2000]}"
    )

    stderr = scan_verbose.stderr

    has_format_phase = "[format]" in stderr or "Formatting" in stderr
    has_tier1_phase = "[tier1]" in stderr or "Tier 1" in stderr
    has_pipeline = "Dispatching to" in stderr or "Fan-out:" in stderr
    assert has_format_phase or has_tier1_phase or has_pipeline, (
        f"Expected format/tier1/pipeline milestones in stderr:\n{stderr[:2000]}"
    )
    assert has_pipeline, f"Expected 'Dispatching to' milestone in stderr:\n{stderr[:2000]}"

    assert "[scan]" in stderr, f"Expected [scan] phase in stderr:\n{stderr[:2000]}"
    assert "Native:" in stderr, f"Expected Native milestone in stderr:\n{stderr[:2000]}"

    # stdout should have the human-readable check results table, not JSON
    assert "Check Results" in scan_verbose.stdout, (
        f"Expected 'Check Results' in stdout (human-readable mode):\n{scan_verbose.stdout[:2000]}"
    )
    assert scan_verbose.stdout.strip()[0] != "{", "stdout should not be JSON in non-JSON mode"


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_posix_argspec_violation(scan_data: YAMLDict, scan_stderr: str) -> None:
    """L058/L059 fires for ansible.posix.sysctl with bogus_param (ADR-032 proof).

    ``ansible.posix`` is intentionally omitted from ``requirements.yml``.
    This can only fire if the collection was auto-discovered from FQCNs
    and installed by the daemon's collection cache.

    Args:
        scan_data: Parsed scan result.
        scan_stderr: Stderr log output from scan.
    """
    violations = cast(list[ViolationDict], scan_data.get("violations", []))
    posix_violations = [v for v in violations if "ansible.posix.sysctl" in str(v.get("message", ""))]
    argspec_hits = [v for v in posix_violations if v.get("rule_id") in ("L058", "L059")]

    assert argspec_hits, (
        "Expected L058/L059 for ansible.posix.sysctl bogus_param — "
        "auto-discovery may not have installed the collection.\n"
        f"All rule_ids: {sorted({str(v.get('rule_id', '')) for v in violations})}\n"
        f"scan_data keys: {sorted(scan_data.keys())}\n"
        f"Full stderr ({len(scan_stderr)} chars):\n{scan_stderr[-6000:]}"
    )


# ---------------------------------------------------------------------------
# ADR-020: Reporting pipeline — scan events persisted to gateway DB
# ---------------------------------------------------------------------------


def _poll_db(
    db_path: str,
    query: str,
    params: tuple[object, ...] = (),
    *,
    timeout: float = 10.0,
) -> list[tuple[object, ...]]:
    """Poll SQLite until the query returns rows, or timeout.

    Event emission is fire-and-forget (``asyncio.create_task``) so there is
    a small window between the CLI returning and the gateway committing the
    row.  This helper retries until data appears.

    Args:
        db_path: Filesystem path to the SQLite database.
        query: SQL SELECT statement to execute.
        params: Optional bind parameters for the query.
        timeout: Maximum seconds to wait for a non-empty result.

    Returns:
        List of row tuples from the query.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(query, params).fetchall()
            conn.close()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
        time.sleep(0.5)
    return []


def _poll_api(url: str, *, timeout: float = 10.0) -> dict[str, object]:
    """Poll a REST API endpoint until it returns a non-empty JSON response.

    Args:
        url: Full URL to GET.
        timeout: Maximum seconds to wait for data.

    Returns:
        Parsed JSON response dict.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                if data:
                    return cast(dict[str, object], data)
        except Exception:
            pass
        time.sleep(0.5)
    return {}


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_scan_persisted_to_gateway(scan_data: YAMLDict, infrastructure: object) -> None:
    """Cross-reference CLI scan output with gateway DB and REST API (ADR-020).

    The engine emits a FixCompletedEvent after ``check`` (FixSession).  This test
    proves the full pipeline by matching the scan the CLI received against
    what the gateway persisted to SQLite and exposes via REST:

    1. scan_id matches between CLI JSON and the DB scans row.
    2. Remediation summary counts (auto_fixable, ai_candidate, manual_review)
       match exactly — they come from the same ScanSummary proto.
    3. Every rule_id the CLI reported also appears in the DB violations table.
    4. A session row was upserted with a non-empty project_path.
    5. Pipeline log entries were persisted for historical debugging.
    6. REST /activity/{id} returns matching scan_type, violation counts,
       remediation breakdown, violation rule_ids, and pipeline logs.
    7. REST /sessions lists the session linked to our scan.
    8. REST /violations/top includes rules from the terrible-playbook
       and every entry has count > 0.

    Args:
        scan_data: Parsed scan JSON from the terrible-playbook scan.
        infrastructure: Daemon infrastructure (provides gateway_db_path, gateway_http_url).
    """
    db_path = getattr(infrastructure, "gateway_db_path", "")
    assert db_path, "gateway_db_path not set on Infrastructure — gateway may not have started"
    http_url = getattr(infrastructure, "gateway_http_url", "")
    assert http_url, "gateway_http_url not set on Infrastructure"

    cli_scan_id = str(scan_data.get("scan_id", ""))
    assert cli_scan_id, "CLI scan output missing scan_id"

    # -- 1. Scan row exists and scan_id matches -------------------------
    scans = _poll_db(
        db_path,
        "SELECT scan_id, session_id, scan_type, total_violations, "
        "auto_fixable, ai_candidate, manual_review "
        "FROM scans WHERE scan_id = ?",
        (cli_scan_id,),
    )
    assert scans, (
        f"scan_id {cli_scan_id} not found in gateway DB within timeout — "
        "event emitter may not have delivered the FixCompletedEvent"
    )

    db_scan_id, db_session_id, db_scan_type, db_total, db_auto, db_ai, db_manual = scans[0]
    assert db_scan_type == "remediate", f"Expected scan_type='remediate' (FixSession check path), got {db_scan_type!r}"
    assert int(str(db_total)) > 0, "Scan should have found violations in terrible-playbook"

    # -- 2. Remediation summary matches CLI output ----------------------
    cli_summary = cast(dict[str, int], scan_data.get("remediation_summary", {}))
    assert int(str(db_auto)) == cli_summary.get("auto_fixable", 0), (
        f"auto_fixable mismatch: DB={db_auto} CLI={cli_summary.get('auto_fixable')}"
    )
    assert int(str(db_ai)) == cli_summary.get("ai_candidate", 0), (
        f"ai_candidate mismatch: DB={db_ai} CLI={cli_summary.get('ai_candidate')}"
    )
    assert int(str(db_manual)) == cli_summary.get("manual_review", 0), (
        f"manual_review mismatch: DB={db_manual} CLI={cli_summary.get('manual_review')}"
    )

    # -- 3. Violation rule_ids from CLI appear in DB --------------------
    cli_violations = cast(list[ViolationDict], scan_data.get("violations", []))
    cli_rule_ids = {str(v.get("rule_id", "")) for v in cli_violations}

    db_violations = _poll_db(
        db_path,
        "SELECT rule_id FROM violations WHERE scan_id = ?",
        (cli_scan_id,),
    )
    db_rule_ids = {str(row[0]) for row in db_violations}

    missing = cli_rule_ids - db_rule_ids
    assert not missing, (
        f"Rule IDs in CLI output but missing from gateway DB: {sorted(missing)}\n"
        f"CLI rule_ids: {sorted(cli_rule_ids)}\n"
        f"DB rule_ids:  {sorted(db_rule_ids)}"
    )

    assert len(db_violations) >= len(cli_violations), (
        f"DB violations ({len(db_violations)}) < CLI violations ({len(cli_violations)}); "
        "DB should have the full (pre-dedup) set"
    )

    # -- 4. Session row exists with correct project path ----------------
    assert db_session_id, f"session_id on scan row should be non-empty, got {db_session_id!r}"
    sessions = _poll_db(
        db_path,
        "SELECT session_id, project_path FROM sessions WHERE session_id = ?",
        (str(db_session_id),),
    )
    assert sessions, f"No session row for session_id {db_session_id!r} linked to scan {cli_scan_id}"
    _, db_project_path = sessions[0]
    assert db_project_path, f"project_path should be non-empty, got {db_project_path!r}"

    # -- 5. Pipeline logs were persisted --------------------------------
    logs = _poll_db(
        db_path,
        "SELECT message, phase FROM scan_logs WHERE scan_id = ?",
        (cli_scan_id,),
    )
    assert logs, f"No pipeline logs persisted for scan_id {cli_scan_id}"
    log_phases = {str(row[1]) for row in logs}
    assert "scan" in log_phases, f"Expected 'scan' phase in persisted logs, got phases: {sorted(log_phases)}"

    # -- 6. REST /activity/{id} returns full detail matching CLI output ------
    scan_detail = _poll_api(f"{http_url}/api/v1/activity/{cli_scan_id}")
    assert scan_detail, f"GET /activity/{cli_scan_id} returned empty response"
    assert scan_detail.get("scan_id") == cli_scan_id, (
        f"REST scan_id mismatch: {scan_detail.get('scan_id')!r} != {cli_scan_id!r}"
    )
    assert scan_detail.get("scan_type") == "remediate"
    assert scan_detail.get("total_violations") == int(str(db_total)), (
        f"REST total_violations={scan_detail.get('total_violations')} != DB {db_total}"
    )
    assert scan_detail.get("fixable") == cli_summary.get("auto_fixable", 0), (
        f"REST fixable={scan_detail.get('fixable')} != CLI {cli_summary.get('auto_fixable')}"
    )
    assert scan_detail.get("ai_candidate") == cli_summary.get("ai_candidate", 0), (
        f"REST ai_candidate={scan_detail.get('ai_candidate')} != CLI {cli_summary.get('ai_candidate')}"
    )
    assert scan_detail.get("manual_review") == cli_summary.get("manual_review", 0), (
        f"REST manual_review={scan_detail.get('manual_review')} != CLI {cli_summary.get('manual_review')}"
    )

    rest_violations = scan_detail.get("violations", [])
    assert isinstance(rest_violations, list) and len(rest_violations) > 0, (
        "REST /activity/{id} should include violations list"
    )
    rest_rule_ids = {str(v.get("rule_id", "")) for v in rest_violations}
    missing_rest = cli_rule_ids - rest_rule_ids
    assert not missing_rest, (
        f"Rule IDs from CLI missing in REST violations: {sorted(missing_rest)}\n"
        f"CLI rule_ids: {sorted(cli_rule_ids)}\n"
        f"REST rule_ids: {sorted(rest_rule_ids)}"
    )

    rest_logs = scan_detail.get("logs", [])
    assert isinstance(rest_logs, list) and len(rest_logs) > 0, "REST /activity/{id} should include pipeline logs"
    rest_log_phases = {str(lg.get("phase", "")) for lg in rest_logs}
    assert "scan" in rest_log_phases, f"REST logs missing 'scan' phase, got: {sorted(rest_log_phases)}"

    # -- 7. REST /sessions lists the session with our scan ---------------
    api_sessions = _poll_api(f"{http_url}/api/v1/sessions")
    assert api_sessions, "GET /sessions returned empty response"
    session_items = api_sessions.get("items", [])
    assert isinstance(session_items, list) and len(session_items) > 0, "GET /sessions returned no items"
    rest_session_ids = {str(s.get("session_id", "")) for s in session_items}
    assert str(db_session_id) in rest_session_ids, f"Session {db_session_id!r} not found in REST /sessions response"

    # -- 8. REST /violations/top includes rules we know the playbook triggers
    top_resp = _poll_api(f"{http_url}/api/v1/violations/top")
    assert isinstance(top_resp, list) and len(top_resp) > 0, (
        "GET /violations/top should return at least one aggregated rule"
    )
    top_rule_ids = {str(entry.get("rule_id", "")) for entry in top_resp}
    assert top_rule_ids & cli_rule_ids, (
        f"No overlap between /violations/top rule_ids and CLI rule_ids.\n"
        f"Top: {sorted(top_rule_ids)}\n"
        f"CLI: {sorted(cli_rule_ids)}"
    )
    for entry in top_resp:
        assert entry.get("count", 0) > 0, f"Rule {entry.get('rule_id')} in /violations/top should have count>0"
