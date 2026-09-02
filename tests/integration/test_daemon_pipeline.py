"""Full daemon integration test: CLI -> gRPC -> Engine -> validators -> gateway.

Proves the FQCN collection auto-discovery pipeline (ADR-032) works
end-to-end using the ``terrible-playbook`` fixture.  ``ansible.posix`` is
intentionally omitted from ``requirements.yml``; L058/L059 can only fire
if the collection was auto-discovered from FQCNs and installed.

Also proves the ADR-020 reporting pipeline: scan events emitted by the engine
are persisted to the gateway's PostgreSQL database and served by the REST API.

Run with::

    pytest -m integration tests/integration/ -v
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import cast

import pytest

from apme_engine.cli._exit_codes import EXIT_ERROR, EXIT_VIOLATIONS
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
    assert r.returncode != EXIT_ERROR, (
        f"Scan exited {r.returncode}:\nstdout: {r.stdout[:2000]}\nstderr: {r.stderr[:4000]}"
    )
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
    assert scan_verbose.returncode in (0, EXIT_VIOLATIONS), (
        f"Check exited {scan_verbose.returncode} (expected 0 or 1):\n"
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
def test_posix_argspec_violation(scan_data: YAMLDict) -> None:
    """L058/L059 fires for ansible.posix.sysctl with bogus_param (ADR-032 proof).

    ``ansible.posix`` is intentionally omitted from ``requirements.yml``.
    This can only fire if the collection was auto-discovered from FQCNs
    and installed by the daemon's collection cache.

    With graph-authoritative violations, tier1 violations are no longer
    dropped — L058/L059 should always appear in the final output.

    Args:
        scan_data: Parsed scan result.
    """
    violations = cast(list[ViolationDict], scan_data.get("violations", []))
    posix_violations = [v for v in violations if "ansible.posix.sysctl" in str(v.get("message", ""))]
    argspec_hits = [v for v in posix_violations if v.get("rule_id") in ("L058", "L059")]

    assert argspec_hits, (
        "Expected L058/L059 for ansible.posix.sysctl bogus_param — "
        "auto-discovery may not have installed the collection.\n"
        f"All rule_ids: {sorted({str(v.get('rule_id', '')) for v in violations})}\n"
        f"scan_data keys: {sorted(scan_data.keys())}"
    )


# ---------------------------------------------------------------------------
# Idempotency: remediate twice → same violations, zero fixes on second pass
# ---------------------------------------------------------------------------


def _remediate_json(fixture_dir: Path) -> YAMLDict:
    """Run ``apme remediate --json`` on the given directory.

    Args:
        fixture_dir: Path to the Ansible project to remediate.

    Returns:
        Parsed JSON dict from stdout.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "apme_engine.cli",
            "remediate",
            "--json",
            "-v",
            "--session",
            f"idempotent-{time.monotonic_ns()}",
            str(fixture_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode != EXIT_ERROR, (
        f"Remediate exited {r.returncode}:\nstdout: {r.stdout[:2000]}\nstderr: {r.stderr[:4000]}"
    )
    try:
        return cast(YAMLDict, json.loads(r.stdout))
    except json.JSONDecodeError:
        pytest.fail(f"Remediate output not valid JSON:\n{r.stdout[:2000]}\nstderr: {r.stderr[:4000]}")
        return {}  # unreachable


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_remediation_idempotent(infrastructure: object, tmp_path: Path) -> None:
    """Remediate twice — second pass finds same violations with zero fixes.

    Proves the ContentGraph is authoritative: what it said remained after
    convergence actually remains when the patched files are re-scanned.

    1. Copy terrible-playbook to a temp directory.
    2. Run ``apme remediate --json`` — capture violations and fix count.
    3. Run ``apme remediate --json`` again on the (now patched) directory.
    4. Assert: second pass has zero fixes.
    5. Assert: second pass reports the same remaining violations.

    Args:
        infrastructure: Daemon infrastructure fixture (ensures daemon is up).
        tmp_path: Pytest temporary directory.
    """
    import shutil

    work_dir = tmp_path / "terrible-playbook"
    shutil.copytree(FIXTURE_DIR, work_dir)

    pass1 = _remediate_json(work_dir)
    pass1_summary = cast(dict[str, int], pass1.get("remediation_summary", {}))
    pass1_fixed = pass1_summary.get("auto_fixable", 0)

    assert pass1_fixed > 0, (
        "First remediate pass should fix at least one violation — terrible-playbook has many auto-fixable issues"
    )

    pass2 = _remediate_json(work_dir)
    pass2_violations = cast(list[ViolationDict], pass2.get("violations", []))
    pass2_summary = cast(dict[str, int], pass2.get("remediation_summary", {}))
    pass2_fixed = pass2_summary.get("auto_fixable", 0)
    pass2_rule_ids = sorted(str(v.get("rule_id", "")) for v in pass2_violations)

    assert pass2_fixed == 0, f"Second remediate pass should fix zero violations (idempotent), but fixed {pass2_fixed}."
    assert pass2_violations, "Second pass should still report remaining unfixable violations"

    pass3 = _remediate_json(work_dir)
    pass3_violations = cast(list[ViolationDict], pass3.get("violations", []))
    pass3_summary = cast(dict[str, int], pass3.get("remediation_summary", {}))
    pass3_fixed = pass3_summary.get("auto_fixable", 0)
    pass3_rule_ids = sorted(str(v.get("rule_id", "")) for v in pass3_violations)

    assert pass3_fixed == 0, f"Third remediate pass should fix zero violations (idempotent), but fixed {pass3_fixed}."

    assert pass3_rule_ids == pass2_rule_ids, (
        f"Third pass should report identical violations to second pass.\n"
        f"Pass 2 ({len(pass2_rule_ids)}): {pass2_rule_ids}\n"
        f"Pass 3 ({len(pass3_rule_ids)}): {pass3_rule_ids}"
    )


# ---------------------------------------------------------------------------
# ADR-020: Reporting pipeline — scan events persisted to gateway DB
# ---------------------------------------------------------------------------


def _poll_db(
    database_url: str,
    query: str,
    params: tuple[object, ...] = (),
    *,
    timeout: float = 10.0,
) -> list[tuple[object, ...]]:
    """Poll PostgreSQL until the query returns rows, or timeout.

    Event emission is fire-and-forget (``asyncio.create_task``) so there is
    a small window between the CLI returning and the gateway committing the
    row.  This helper retries until data appears.

    Args:
        database_url: SQLAlchemy PostgreSQL URL for the gateway database.
        query: SQL SELECT statement to execute (``$1``-style placeholders).
        params: Optional bind parameters for the query.
        timeout: Maximum seconds to wait for a non-empty result.

    Returns:
        List of row tuples from the query.
    """
    import asyncpg

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _fetch(remaining: float) -> list[tuple[object, ...]]:
        conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=remaining), timeout=remaining)
        try:
            fetch_remaining = max(deadline - time.monotonic(), 0.1)
            records = await asyncio.wait_for(conn.fetch(query, *params), timeout=fetch_remaining)
            return [tuple(record.values()) for record in records]
        finally:
            await conn.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            rows = asyncio.run(_fetch(remaining))
            if rows:
                return rows
        except Exception:
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
    what the gateway persisted to PostgreSQL and exposes via REST:

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
        infrastructure: Daemon infrastructure (provides gateway_database_url, gateway_http_url).
    """
    database_url = getattr(infrastructure, "gateway_database_url", "")
    assert database_url, "gateway_database_url not set on Infrastructure — gateway may not have started"
    http_url = getattr(infrastructure, "gateway_http_url", "")
    assert http_url, "gateway_http_url not set on Infrastructure"

    cli_scan_id = str(scan_data.get("scan_id", ""))
    assert cli_scan_id, "CLI scan output missing scan_id"

    # -- 1. Scan row exists and scan_id matches -------------------------
    scans = _poll_db(
        database_url,
        "SELECT scan_id, session_id, scan_type, total_violations, "
        "auto_fixable, ai_candidate, manual_review "
        "FROM scans WHERE scan_id = $1",
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
        database_url,
        "SELECT rule_id FROM violations WHERE scan_id = $1",
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
        database_url,
        "SELECT session_id, project_path FROM sessions WHERE session_id = $1",
        (str(db_session_id),),
    )
    assert sessions, f"No session row for session_id {db_session_id!r} linked to scan {cli_scan_id}"
    _, db_project_path = sessions[0]
    assert db_project_path, f"project_path should be non-empty, got {db_project_path!r}"

    # -- 5. Pipeline logs were persisted --------------------------------
    logs = _poll_db(
        database_url,
        "SELECT message, phase FROM scan_logs WHERE scan_id = $1",
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


# ---------------------------------------------------------------------------
# Ansible introspection cache: second scan should get cache hits
# ---------------------------------------------------------------------------


@pytest.mark.integration  # type: ignore[untyped-decorator]
def test_ansible_cache_hits_on_second_scan(
    scan_data: YAMLDict,
) -> None:
    """Second scan of the same project gets plugin introspection cache hits.

    The first scan (via ``scan_data`` fixture) warms the daemon's
    in-memory PluginCache. A second scan of the same fixture with the
    same session reuses the same venv and collection versions, so
    M001-M004/L058/L059 should get cache hits. We verify by reading
    the daemon log file.

    Uses the launcher module's resolved ``_DATA_DIR`` to find
    ``daemon.log`` — the module-level path is fixed at import time and
    may differ from the conftest's ``data_dir`` when the env var was
    set after import.

    Args:
        scan_data: Parsed scan data from the first scan (ensures it ran first).
    """
    from apme_engine.daemon.launcher import _DATA_DIR

    daemon_log = _DATA_DIR / "daemon.log"

    _scan_json(FIXTURE_DIR)

    assert daemon_log.is_file(), f"daemon.log not found at {daemon_log}"
    log_text = daemon_log.read_text()

    cache_lines = [
        line for line in log_text.splitlines() if "Ansible validator: total" in line and "cache introspect=" in line
    ]
    assert len(cache_lines) >= 2, (
        f"Expected at least 2 Ansible validator cache log lines (first scan + second scan), "
        f"found {len(cache_lines)}.\nAll cache lines:\n" + "\n".join(cache_lines)
    )

    last_line = cache_lines[-1]
    hit_counts = re.findall(r"(\d+)h/", last_line)
    total_hits = sum(int(h) for h in hit_counts)
    assert total_hits > 0, f"Second scan should have cache hits but found 0.\nLast cache line: {last_line}"
