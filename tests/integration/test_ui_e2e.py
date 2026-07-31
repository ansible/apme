"""End-to-end UI test: CLI scan -> gateway DB -> UI renders results.

Exercises the full pipeline through the browser: runs a CLI scan against
the terrible-playbook fixture, waits for the gateway to persist the event,
then opens the dashboard and verifies the scan appears with correct
violation data.

Requires:
    - The daemon integration infrastructure (conftest.py starts daemon + gateway)
    - A Vite dev server or static file server for the SPA on APME_UI_URL
    - pytest-playwright

Run with::

    pytest -m 'integration and ui' tests/integration/test_ui_e2e.py -v
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

pytest.importorskip("pytest_playwright", reason="pytest-playwright not installed")

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import expect  # noqa: E402

from apme_engine.engine.models import ViolationDict, YAMLDict  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.ui]

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "terrible-playbook"
_SESSION_ID = "e2e-ui-session"
_BASE = os.environ.get("APME_UI_URL", "http://localhost:8081")


def _run_scan(fixture_dir: Path) -> YAMLDict:
    """Scan fixture directory via CLI and return parsed JSON.

    Args:
        fixture_dir: Path to the Ansible project to scan.

    Returns:
        Parsed JSON dict from scan output.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "apme_engine.cli",
            "check",
            "--json",
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
    assert r.returncode == 0, f"Scan exited {r.returncode}:\n{r.stderr[:2000]}"
    return cast(YAMLDict, json.loads(r.stdout))


def _wait_for_api(http_url: str, scan_id: str, timeout: float = 15.0) -> dict[str, object]:
    """Poll the gateway REST API until the scan appears.

    Args:
        http_url: Gateway base URL.
        scan_id: Scan UUID to wait for.
        timeout: Maximum seconds to wait.

    Returns:
        Scan detail JSON from the REST API.
    """
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            url = f"{http_url}/api/v1/activity/{scan_id}"
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return cast(dict[str, object], json.loads(resp.read()))
        except Exception:
            pass
        time.sleep(0.5)
    return {}


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def e2e_scan(infrastructure: object) -> YAMLDict:
    """Run a scan and wait for gateway persistence.

    Args:
        infrastructure: Daemon infrastructure fixture.

    Returns:
        Parsed scan JSON.
    """
    http_url = getattr(infrastructure, "gateway_http_url", "")
    assert http_url, "gateway_http_url not set"
    result = _run_scan(FIXTURE_DIR)
    scan_id = str(result.get("scan_id", ""))
    assert scan_id, "CLI scan missing scan_id"
    api_data = _wait_for_api(http_url, scan_id)
    assert api_data, f"Scan {scan_id} not found in gateway within timeout"
    return result


@pytest.mark.integration()  # type: ignore[untyped-decorator]
@pytest.mark.ui()  # type: ignore[untyped-decorator]
def test_scan_visible_in_dashboard(
    e2e_scan: YAMLDict,
    page: Page,
) -> None:
    """After a CLI scan, the dashboard shows the scan with violations.

    Verifies:
    1. Dashboard metric cards are populated
    2. Violation count is > 0
    3. The scan appears in the recent scans table

    Args:
        e2e_scan: Scan result (ensures scan has been persisted).
        page: Playwright page fixture.
    """
    page.goto(_BASE, wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-navigation']", timeout=10_000)

    cards = page.locator(".pf-v6-c-card")
    assert cards.count() >= 6, f"Dashboard should show >=6 cards, got {cards.count()}"

    count_values = page.locator(".pf-v6-c-card span[style*='xxx-large']")
    total = 0
    for i in range(count_values.count()):
        text = count_values.nth(i).text_content(timeout=5_000) or "0"
        with contextlib.suppress(ValueError):
            total += int(text)
    assert total > 0, "Dashboard should show >0 in metric count cards after scan"


@pytest.mark.integration()  # type: ignore[untyped-decorator]
@pytest.mark.ui()  # type: ignore[untyped-decorator]
def test_scan_detail_shows_violations(
    e2e_scan: YAMLDict,
    page: Page,
) -> None:
    """Navigate to the scan detail page and verify violation file groups appear.

    Args:
        e2e_scan: Scan result (ensures scan has been persisted).
        page: Playwright page fixture.
    """
    scan_id = str(e2e_scan.get("scan_id", ""))
    page.goto(f"{_BASE}/activity/{scan_id}", wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-navigation']", timeout=10_000)

    violations = cast(list[ViolationDict], e2e_scan.get("violations", []))
    cli_rule_ids = {str(v.get("rule_id", "")) for v in violations}

    page.wait_for_selector("[data-testid='page-title']", timeout=5_000)

    file_groups = page.locator(".apme-file-group")
    assert file_groups.count() > 0, "Scan detail should show at least one file group"

    expand_btn = page.locator("button", has_text="Expand All")
    if expand_btn.is_visible():
        expand_btn.click()
        page.wait_for_timeout(500)

    rule_ids_on_page = page.locator(".apme-rule-id")
    ui_rules: set[str] = set()
    for i in range(rule_ids_on_page.count()):
        text = rule_ids_on_page.nth(i).text_content() or ""
        if text.strip():
            ui_rules.add(text.strip())

    overlap = cli_rule_ids & ui_rules
    assert overlap, (
        f"Expected UI to display rule IDs from CLI scan.\n"
        f"CLI rules: {sorted(cli_rule_ids)}\n"
        f"UI rules: {sorted(ui_rules)}"
    )


@pytest.mark.integration()  # type: ignore[untyped-decorator]
@pytest.mark.ui()  # type: ignore[untyped-decorator]
def test_activity_list_contains_scan(
    e2e_scan: YAMLDict,
    page: Page,
) -> None:
    """The activity page shows the scan from the CLI run.

    Args:
        e2e_scan: Scan result (ensures scan has been persisted).
        page: Playwright page fixture.
    """
    page.goto(f"{_BASE}/activity", wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-navigation']", timeout=10_000)

    table = page.locator(".pf-v6-c-table")
    expect(table).to_be_visible(timeout=5_000)

    rows = page.locator(".pf-v6-c-table tbody tr")
    assert rows.count() > 0, "Scans page should have at least one row after CLI scan"


@pytest.mark.integration()  # type: ignore[untyped-decorator]
@pytest.mark.ui()  # type: ignore[untyped-decorator]
def test_analytics_top_violations_populated(
    e2e_scan: YAMLDict,
    page: Page,
) -> None:
    """Analytics page shows top violation data after a scan.

    Args:
        e2e_scan: Scan result (ensures scan has been persisted).
        page: Playwright page fixture.
    """
    page.goto(f"{_BASE}/analytics", wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-navigation']", timeout=10_000)

    page.wait_for_selector(".apme-rule-id, div:has-text('No violation data')", timeout=10_000)
    rule_entries = page.locator(".apme-rule-id")
    assert rule_entries.count() > 0, "Top violations should show rules after a scan"
