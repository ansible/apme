"""Chromium smoke for the mounted shared BUI workflow, with fixture HTTP data.

Build with ``tox -e frontend-ci,frontend-dev-build``, then run
``tox -e ui-workflow-browser``. The test serves the built harness itself;
APME_WORKFLOW_UI_URL can point it at an already-running server. This checks the
development harness; real host acceptance remains #833.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("pytest_playwright", reason="pytest-playwright not installed")

from playwright.sync_api import expect  # noqa: E402

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

pytestmark = pytest.mark.ui

_PROJECT_ID = "workflow-smoke"
_SCAN_ID = "smoke-scan"
_PROJECT_PATH = f"/api/v1/projects/{_PROJECT_ID}"
_CREATED_AT = "2026-01-01T00:00:00Z"


@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def workflow_harness_url() -> Iterator[str]:
    """Serve the built harness for the browser tests.

    Yields:
        str: Harness origin.
    """
    configured_url = os.environ.get("APME_WORKFLOW_UI_URL")
    if configured_url:
        yield configured_url.rstrip("/")
        return

    harness_directory = Path(__file__).resolve().parent.parent / "frontend/backstage/dist-dev"
    if not harness_directory.is_dir():
        pytest.fail(
            "The shared workflow harness is missing; build it with tox -e frontend-ci,frontend-dev-build first.",
        )

    class HarnessRequestHandler(SimpleHTTPRequestHandler):
        def __init__(
            self,
            request: socket.socket,
            client_address: tuple[str, int],
            server: ThreadingHTTPServer,
        ) -> None:
            super().__init__(request, client_address, server, directory=str(harness_directory))

    server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


@pytest.fixture(params=["light", "dark"])  # type: ignore[untyped-decorator]
def workflow_app(
    page: Page,
    request: pytest.FixtureRequest,
    workflow_harness_url: str,
) -> Iterator[Page]:
    """Open the real harness with deterministic Gateway HTTP/SSE responses.

    Args:
        page: Isolated Playwright page.
        request: Parametrized BUI theme mode.
        workflow_harness_url: Locally served harness origin.

    Yields:
        Page: Page showing the fixture repository list.
    """
    theme: Literal["light", "dark"] = request.param
    # Emulate the *opposing* system preference: the host-selected mode, not
    # prefers-color-scheme, has to drive color-scheme and every light-dark()
    # color the workflow renders.
    page.emulate_media(color_scheme="dark" if theme == "light" else "light")
    project = {
        "id": _PROJECT_ID,
        "name": "Workflow smoke repository",
        "repo_url": "https://example.com/smoke/ansible.git",
        "branch": "main",
        "created_at": _CREATED_AT,
        "health_score": 100,
        "total_violations": 0,
        "violation_trend": "stable",
        "scan_count": 0,
        "last_scanned_at": None,
        "scm_provider": None,
        "has_scm_token": False,
        "last_scanned_commit": "",
        "has_new_commits": False,
        "active_operation": None,
        "latest_scan": None,
        "severity_breakdown": {},
    }
    result = {
        "total_violations": 1,
        "fixable": 1,
        "manual_review": 0,
        "ai_proposed": 0,
        "ai_declined": 0,
        "ai_accepted": 0,
        "remediated_count": 0,
    }
    operation = {
        "operation_id": "smoke-operation",
        "project_id": _PROJECT_ID,
        "scan_id": _SCAN_ID,
        "status": "scanning",
        "scan_type": "check",
        "started_at": _CREATED_AT,
        "progress": [],
    }
    started = False
    unexpected: list[str] = []
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def gateway(route: Route) -> None:
        """Fulfil only the Gateway requests supported by this smoke fixture.

        Args:
            route: Intercepted HTTP request to fulfil.
        """
        nonlocal started
        path = urlparse(route.request.url).path
        method = route.request.method
        if method == "GET" and path == "/api/v1/projects":
            route.fulfill(json={"items": [project], "total": 1})
        elif method == "GET" and path == _PROJECT_PATH:
            route.fulfill(json=project)
        elif method == "GET" and path == f"{_PROJECT_PATH}/graph":
            route.fulfill(
                json={
                    "version": 1,
                    "nodes": [{"id": "task", "data": {"name": "Show message", "node_type": "task"}}],
                    "edges": [],
                },
            )
        elif method == "GET" and path == "/api/v1/ai/models":
            route.fulfill(json=[{"id": "smoke-model", "name": "Smoke model", "provider": "fixture"}])
        elif method == "POST" and path == f"{_PROJECT_PATH}/operation":
            started = True
            route.fulfill(status=202, json={"operation_id": operation["operation_id"]})
        elif method == "GET" and path == f"{_PROJECT_PATH}/operation":
            if started:
                route.fulfill(json=operation)
            else:
                route.fulfill(status=404, json={"detail": "No active operation"})
        elif method == "GET" and path == f"{_PROJECT_PATH}/operation/events" and started:
            # Exercise authenticated fetch-based SSE through the actual adapter,
            # including snapshot, result and terminal status handling.
            events = [
                ("snapshot", operation),
                ("result", result),
                ("status_changed", {"status": "completed", "previous": "scanning"}),
            ]
            body = "".join(f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events)
            route.fulfill(content_type="text/event-stream", body=body)
        elif method == "GET" and path == f"/api/v1/activity/{_SCAN_ID}":
            route.fulfill(
                json={
                    **result,
                    "scan_id": _SCAN_ID,
                    "session_id": "smoke-session",
                    "project_id": _PROJECT_ID,
                    "project_path": project["repo_url"],
                    "source": "ui",
                    "created_at": _CREATED_AT,
                    "scan_type": "check",
                    "ai_candidate": 0,
                    "pr_url": None,
                    "diagnostics_json": None,
                    "violations": [
                        {
                            "rule_id": "L050",
                            "level": "high",
                            "message": "Use an FQCN",
                            "file": "play.yml",
                            "path": "play.yml/tasks[0]",
                            "line": 1,
                            "node_type": "task",
                            "remediation_class": 1,
                            "original_yaml": "- name: Show message\n  debug:\n    msg: hello\n",
                        }
                    ],
                    "proposals": [],
                    "patches": [],
                    "logs": [],
                },
            )
        else:
            unexpected.append(f"{method} {path}")
            route.fulfill(status=501, json={"detail": "Unexpected smoke fixture request"})

    page.route("**/api/v1/**", gateway)
    page.goto(workflow_harness_url, wait_until="domcontentloaded")
    # The minimal dev harness has no host theme selector. Apply BUI's public
    # document attribute, including overlays portaled outside the workflow.
    page.evaluate("mode => document.documentElement.setAttribute('data-theme-mode', mode)", theme)
    expect(page.get_by_role("grid", name="APME repositories")).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-theme-mode", theme)
    # The host palette must declare the scheme light-dark() resolves against,
    # otherwise the OS preference leaks into badges, pills and graph strokes.
    expect(page.locator("html")).to_have_css("color-scheme", theme)
    yield page
    assert unexpected == [], f"Unmocked Gateway requests: {unexpected}"
    assert page_errors == [], f"Browser runtime errors: {page_errors}"


def test_data_surfaces_and_graph_use_the_current_palette(workflow_app: Page) -> None:
    """Verify rendered colors on the table, portaled dialog, and graph.

    Args:
        workflow_app: Harness with fixture data in the selected theme.
    """
    page = workflow_app
    mode = page.locator("html").get_attribute("data-theme-mode")
    dark = mode == "dark"
    surface = "rgb(25, 31, 38)" if dark else "rgb(255, 255, 255)"
    heading = "rgb(33, 41, 51)" if dark else "rgb(247, 247, 247)"
    grid = page.get_by_role("grid", name="APME repositories")
    expect(grid.get_by_role("columnheader").first).to_have_css("background-color", heading)
    expect(grid.get_by_role("gridcell").first).to_have_css("background-color", surface)

    page.get_by_role("button", name="Add repository", exact=True).click()
    dialog = page.get_by_role("dialog", name="Add repository")
    expect(dialog.locator(".bui-DialogContent")).to_have_css("background-color", surface)
    # Overlays are portaled to <body>, so they must inherit the same scheme.
    expect(dialog).to_have_css("color-scheme", mode)
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)

    page.get_by_role("button", name="Workflow smoke repository", exact=True).click()
    page.get_by_role("tab", name="Graph", exact=True).click()
    canvas = page.locator(".graph-container")
    expect(canvas).to_have_css("background-color", surface)
    blue = "rgb(156, 202, 255)" if dark else "rgb(21, 95, 169)"
    expect(canvas.locator(".graph-node")).to_have_css("stroke", blue)
    # The transitional native host also uses this renderer without BUI tokens.
    canvas.evaluate("element => element.style.setProperty('--bui-bg-neutral-1', 'initial')")
    expect(canvas).to_have_css("background-color", "rgb(13, 17, 23)" if dark else "rgb(255, 255, 255)")


def test_shared_workflow_controls_and_results(workflow_app: Page) -> None:
    """Use accessible BUI controls, start a check, and open its host activity.

    Args:
        workflow_app: Harness with fixture data in the selected theme.
    """
    page = workflow_app
    page.get_by_role("button", name="Workflow smoke repository", exact=True).click()
    expect(page.get_by_role("tab", name="Workflow", exact=True)).to_have_attribute("aria-selected", "true")
    advanced = page.get_by_role("button", name="Advanced Options", exact=True)
    advanced.focus()
    advanced.press("Enter")
    expect(advanced).to_have_attribute("aria-expanded", "true")

    # BUI Select is a React Aria popup, not a native <select>.
    version = page.get_by_role("button", name=re.compile("Ansible Core Version"))
    version.click()
    page.get_by_role("option", name="Custom version…", exact=True).click()
    page.get_by_role("textbox", name="Custom Ansible Core version", exact=True).fill("2.16.3")
    page.get_by_role("textbox", name="Collections (comma-separated)", exact=True).fill(
        "ansible.posix, community.general",
    )
    ai = page.get_by_role("checkbox", name="Enable AI fix", exact=True)
    expect(ai).not_to_be_checked()
    ai.focus()
    ai.press("Space")
    expect(ai).to_be_checked()
    model = page.get_by_role("button", name=re.compile("Select AI model|AI Model"))
    expect(model).to_be_visible()
    model.click()
    page.get_by_role("option", name="Smoke model (fixture)", exact=True).click()
    auto_apply = page.get_by_role("checkbox", name="Auto-apply rule-based fixes (skip review)", exact=True)
    expect(auto_apply).not_to_be_checked()
    auto_apply.focus()
    auto_apply.press("Space")
    expect(auto_apply).to_be_checked()

    with (
        page.expect_request(
            lambda req: req.method == "POST" and urlparse(req.url).path == f"{_PROJECT_PATH}/operation",
        ) as submitted,
        page.expect_response(
            lambda res: urlparse(res.url).path == f"{_PROJECT_PATH}/operation/events",
        ) as streamed,
    ):
        page.get_by_role("button", name="Check repository", exact=True).click()
    assert submitted.value.post_data_json == {
        "action": "check",
        "options": {
            "ansible_version": "2.16.3",
            "collection_specs": ["ansible.posix", "community.general"],
            "enable_ai": True,
            "ai_model": "smoke-model",
            "assess_pause": True,
            "interactive": False,
        },
    }
    assert streamed.value.ok
    expect(page.get_by_role("heading", name="Operation Complete", exact=True)).to_be_visible()
    expect(page.get_by_text("Violations", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Dismiss", exact=True)).to_be_enabled()

    # This harness supplies onViewDetails; exercise shared-to-host navigation.
    page.get_by_role("button", name="View details", exact=True).click()
    expect(page.get_by_role("heading", name=f"Run {_SCAN_ID}", exact=True)).to_be_visible()
    query = parse_qs(urlparse(page.url).query)
    assert query == {"project": [_PROJECT_ID], "tab": ["history"], "scan": [_SCAN_ID]}
    expect(page.get_by_role("tab", name="History", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.get_by_text("Use an FQCN", exact=True)).to_be_visible()
    high_filter = page.get_by_role("group", name="Filter by severity").get_by_role("button", name="High (1)")
    dark = page.locator("html").get_attribute("data-theme-mode") == "dark"
    orange = "rgb(255, 186, 134)" if dark else "rgb(150, 62, 0)"
    green = "rgb(152, 217, 169)" if dark else "rgb(33, 104, 59)"
    expect(high_filter).to_have_css("color", orange)
    # Findings badges in the run detail resolve the same light-dark() pair.
    finding_row = page.locator(".apme-assess-finding-row").first
    severity_badge = finding_row.locator(".bui-Badge").first
    expect(severity_badge).to_have_text("High")
    expect(severity_badge).to_have_css("color", orange)
    expect(finding_row.locator(".bui-Badge").nth(1)).to_have_text("Rule-based fix")
    expect(finding_row.locator(".bui-Badge").nth(1)).to_have_css("color", green)
    expect(high_filter).to_have_attribute("aria-pressed", "true")
    expect(high_filter).to_have_css("box-shadow", f"{orange} 0px 0px 0px 1px inset")
    yaml_view = page.locator(".apme-side-by-side.apme-yaml-hl").first
    expect(yaml_view).to_be_visible()
    # The custom renderer must resolve colors without any PatternFly stylesheet
    # or theme-class bridge in either host theme.
    colors = yaml_view.evaluate("""element => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, foreground: style.color,
        font: getComputedStyle(element.querySelector('.apme-yaml-code')).fontFamily };
    }""")
    assert colors["background"] != "rgba(0, 0, 0, 0)"
    assert colors["foreground"] != colors["background"]
    assert "mono" in colors["font"].lower()
    expect(page.locator("html")).not_to_have_class(re.compile("pf-v6-theme-dark"))
