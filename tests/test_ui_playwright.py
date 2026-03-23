"""Playwright-based browser tests for the APME Executive Dashboard.

These tests verify layout, navigation, and theme toggling against a live
gateway + UI stack.  Marked ``ui`` so they are skipped in both the normal
unit-test run and the daemon integration run.

Requires:
    pytest-playwright (``pip install pytest-playwright``)
    A running UI on ``APME_UI_URL`` (default ``http://localhost:8081``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("pytest_playwright", reason="pytest-playwright not installed")

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.ui

_BASE = os.environ.get("APME_UI_URL", "http://localhost:8081")


@pytest.fixture()  # type: ignore[untyped-decorator]
def dashboard(page: Page) -> Page:
    """Navigate to the dashboard and wait for the sidebar to appear.

    Args:
        page: Playwright page fixture.

    Returns:
        Page positioned on the dashboard.
    """
    page.goto(_BASE, wait_until="networkidle")
    page.wait_for_selector(".apme-sidebar", timeout=10_000)
    return page


def test_page_title(dashboard: Page) -> None:
    """Dashboard page title contains APME.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    expect(dashboard).to_have_title("APME Dashboard")


def test_sidebar_nav_items(dashboard: Page) -> None:
    """Sidebar contains expected navigation links.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    expected = ["Dashboard", "Scans", "Top Violations", "Fix Tracker", "AI Metrics", "Health"]
    items = dashboard.locator(".apme-nav .apme-nav-item")
    expect(items).to_have_count(len(expected))
    for i, label in enumerate(expected):
        expect(items.nth(i)).to_contain_text(label)


def test_metric_cards_visible(dashboard: Page) -> None:
    """Dashboard shows metric cards.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    cards = dashboard.locator(".apme-metric-card")
    expect(cards).to_have_count(6)


def test_navigate_to_scans(dashboard: Page) -> None:
    """Clicking Scans in sidebar navigates to /scans.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Scans")
    dashboard.wait_for_url(f"{_BASE}/scans", timeout=5_000)
    expect(dashboard.locator(".apme-page-title")).to_have_text("All Scans")


def test_navigate_to_health(dashboard: Page) -> None:
    """Clicking Health in sidebar navigates to /health.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Health")
    dashboard.wait_for_url(f"{_BASE}/health", timeout=5_000)
    expect(dashboard.locator(".apme-page-title")).to_have_text("System Health")


def test_navigate_to_violations(dashboard: Page) -> None:
    """Clicking Top Violations in sidebar navigates to /violations.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Top Violations")
    dashboard.wait_for_url(f"{_BASE}/violations", timeout=5_000)
    expect(dashboard.locator(".apme-page-title")).to_have_text("Top Violations")


def test_navigate_to_fix_tracker(dashboard: Page) -> None:
    """Clicking Fix Tracker in sidebar navigates to /fix-tracker.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Fix Tracker")
    dashboard.wait_for_url(f"{_BASE}/fix-tracker", timeout=5_000)
    expect(dashboard.locator(".apme-page-title")).to_have_text("Fix Tracker")


def test_navigate_to_ai_metrics(dashboard: Page) -> None:
    """Clicking AI Metrics in sidebar navigates to /ai-metrics.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=AI Metrics")
    dashboard.wait_for_url(f"{_BASE}/ai-metrics", timeout=5_000)
    expect(dashboard.locator(".apme-page-title")).to_have_text("AI Metrics")


def test_theme_toggle(dashboard: Page) -> None:
    """Theme toggle switches data-theme attribute between dark and light.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    html = dashboard.locator("html")
    expect(html).to_have_attribute("data-theme", "dark")

    dashboard.click(".apme-theme-btn")
    expect(html).to_have_attribute("data-theme", "light")

    dashboard.click(".apme-theme-btn")
    expect(html).to_have_attribute("data-theme", "dark")


def test_scans_page_has_table(dashboard: Page) -> None:
    """Scans page renders a data table (or an empty state).

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Scans")
    dashboard.wait_for_url(f"{_BASE}/scans", timeout=5_000)
    table_or_empty = dashboard.locator(".apme-data-table, .apme-empty")
    expect(table_or_empty.first).to_be_visible()


def test_health_shows_status(dashboard: Page) -> None:
    """Health page displays gateway status rows.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.click("text=Health")
    dashboard.wait_for_url(f"{_BASE}/health", timeout=5_000)
    dashboard.wait_for_selector(".apme-data-table, .apme-empty", timeout=10_000)
    status = dashboard.locator(".apme-data-table td")
    if status.count() >= 2:
        expect(status.first).to_have_text("Gateway")
