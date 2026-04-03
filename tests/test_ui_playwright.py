"""Playwright-based browser tests for the APME UI (ADR-037 project-centric model).

These tests verify layout, navigation, project pages, dashboard, and the
playground against a live gateway + UI stack.  Marked ``ui`` so they are
skipped in the normal unit-test run.

Requires:
    pytest-playwright (``pip install pytest-playwright``)
    A running UI on ``APME_UI_URL`` (default ``http://localhost:8081``).
"""

from __future__ import annotations

import os
import re
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
    """Navigate to the dashboard and wait for sidebar nav.

    Args:
        page: Playwright page fixture.

    Returns:
        Page positioned on the dashboard.
    """
    page.goto(_BASE, wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-navigation']", timeout=10_000)
    nav = page.locator("[data-testid='page-navigation']")
    collapsed = nav.locator("button.pf-v6-c-nav__link[aria-expanded='false']")
    while collapsed.count() > 0:
        collapsed.first.click()
    return page


# ── Navigation & Layout ───────────────────────────────────────────────


def test_page_title(dashboard: Page) -> None:
    """Dashboard page title contains Dashboard.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    expect(dashboard).to_have_title(re.compile(r"Dashboard"))


def test_sidebar_nav_groups(dashboard: Page) -> None:
    """Sidebar contains the new navigation groups (ADR-037).

    Args:
        dashboard: Page positioned on the dashboard.
    """
    nav = dashboard.locator("[data-testid='page-navigation']")
    for group in ["Overview", "Projects", "Operations", "System"]:
        expect(nav.locator(f"button[aria-expanded]:has-text('{group}')").first).to_be_visible()


def test_sidebar_nav_items(dashboard: Page) -> None:
    """Sidebar contains expected navigation links (ADR-037).

    Args:
        dashboard: Page positioned on the dashboard.
    """
    expected = [
        "Dashboard",
        "Projects",
        "Playground",
        "Activity",
        "Sessions",
        "Health",
        "Settings",
    ]
    nav = dashboard.locator("[data-testid='page-navigation']")
    for label in expected:
        expect(nav.locator(f".pf-v6-c-nav__item >> text='{label}'").first).to_be_visible()


def test_old_nav_items_removed(dashboard: Page) -> None:
    """Old session-centric nav items are no longer present.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    nav = dashboard.locator("[data-testid='page-navigation']")
    for removed in ["New Scan", "Top Violations", "Fix Tracker", "AI Metrics"]:
        assert nav.locator(f".pf-v6-c-nav__item >> text='{removed}'").count() == 0


def test_theme_toggle(dashboard: Page) -> None:
    """Theme toggle switches between dark and light.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    html = dashboard.locator("html")
    theme_btn = dashboard.locator("[data-testid='settings-icon'], [data-testid='theme-icon']").first
    expect(theme_btn).to_be_visible()
    initial_is_dark = html.evaluate("el => el.classList.contains('pf-v6-theme-dark')")
    theme_btn.click()
    after_toggle = html.evaluate("el => el.classList.contains('pf-v6-theme-dark')")
    assert initial_is_dark != after_toggle


# ── Dashboard ─────────────────────────────────────────────────────────


def test_dashboard_metric_cards_visible(dashboard: Page) -> None:
    """Dashboard shows metric count cards.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    cards = dashboard.locator(".pf-v6-c-card")
    expect(cards.first).to_be_attached()
    assert cards.count() >= 5, f"Expected >=5 dashboard cards, got {cards.count()}"


def test_dashboard_ranking_tables(dashboard: Page) -> None:
    """Dashboard shows ranking tables (cleanest, most violations, stale, most scanned).

    Args:
        dashboard: Page positioned on the dashboard.
    """
    for title in ["Top 10 Cleanest", "Top 10 Most Violations", "Stale Projects", "Most Active"]:
        expect(dashboard.locator(f"text='{title}'").first).to_be_visible()


# ── Projects ──────────────────────────────────────────────────────────


def test_navigate_to_projects(dashboard: Page) -> None:
    """Clicking Projects in sidebar navigates to /projects.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='projects']").click()
    dashboard.wait_for_url(f"{_BASE}/projects", timeout=5_000)
    expect(dashboard.locator("[data-testid='page-title']")).to_have_text("Projects")


def test_projects_page_create_button(dashboard: Page) -> None:
    """Projects page has a Create Project button.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/projects", wait_until="networkidle")
    btn = dashboard.locator("button:has-text('Create Project')")
    expect(btn).to_be_visible()


def test_projects_page_create_modal(dashboard: Page) -> None:
    """Create Project button opens a modal with name, URL, and branch fields.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/projects", wait_until="networkidle")
    dashboard.locator("button:has-text('Create Project')").click()
    expect(dashboard.locator("#proj-name")).to_be_visible()
    expect(dashboard.locator("#proj-url")).to_be_visible()
    expect(dashboard.locator("#proj-branch")).to_be_visible()


# ── Playground ────────────────────────────────────────────────────────


def test_navigate_to_playground(dashboard: Page) -> None:
    """Clicking Playground in sidebar navigates to /playground.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='playground']").click()
    dashboard.wait_for_url(f"{_BASE}/playground", timeout=5_000)
    expect(dashboard.locator("[data-testid='page-title']")).to_have_text("Playground")


def test_playground_drop_zone(dashboard: Page) -> None:
    """Playground shows a file drop zone.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/playground", wait_until="networkidle")
    drop_zone = dashboard.locator(".apme-drop-zone")
    expect(drop_zone).to_be_visible()
    expect(drop_zone).to_contain_text("Drop Ansible files here")


def test_playground_start_disabled_without_files(dashboard: Page) -> None:
    """Start Check button is disabled when no files are selected.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/playground", wait_until="networkidle")
    btn = dashboard.locator("button:has-text('Start Check')")
    expect(btn).to_be_disabled()


def test_playground_no_session_id(dashboard: Page) -> None:
    """Playground page does not show any session ID text.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/playground", wait_until="networkidle")
    page_content = dashboard.locator("main").inner_text()
    assert "session_id" not in page_content.lower()
    assert "session id" not in page_content.lower()


def test_playground_advanced_options(dashboard: Page) -> None:
    """Playground Advanced Options panel expands to show version, collections, and AI toggle.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.goto(f"{_BASE}/playground", wait_until="networkidle")
    dashboard.click("text=Advanced Options")
    expect(dashboard.locator("#ansible-version")).to_be_visible()
    expect(dashboard.locator("#collections")).to_be_visible()
    expect(dashboard.locator("#enable-ai")).to_be_visible()
    expect(dashboard.locator("#enable-ai")).to_be_checked()


# ── Activity / Health / Settings ──────────────────────────────────────


def test_navigate_to_activity(dashboard: Page) -> None:
    """Clicking Activity in sidebar navigates to /activity.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='activity']").click()
    dashboard.wait_for_url(f"{_BASE}/activity", timeout=5_000)
    expect(dashboard.locator("[data-testid='page-title']")).to_have_text("Activity")


def test_navigate_to_health(dashboard: Page) -> None:
    """Clicking Health in sidebar navigates to /health.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='health']").click()
    dashboard.wait_for_url(f"{_BASE}/health", timeout=5_000)
    expect(dashboard.locator("[data-testid='page-title']")).to_have_text("System Health")


def test_navigate_to_settings(dashboard: Page) -> None:
    """Clicking Settings in sidebar navigates to /settings.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='settings']").click()
    dashboard.wait_for_url(f"{_BASE}/settings", timeout=5_000)
    expect(dashboard.locator("[data-testid='page-title']")).to_have_text("Settings")


def test_activity_page_has_table(dashboard: Page) -> None:
    """Activity page renders a table or empty-state message.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='activity']").click()
    dashboard.wait_for_url(f"{_BASE}/activity", timeout=5_000)
    table_or_empty = dashboard.locator(".pf-v6-c-table, div:has-text('No activity recorded')")
    expect(table_or_empty.first).to_be_visible()


def test_health_shows_status(dashboard: Page) -> None:
    """Health page displays gateway status rows.

    Args:
        dashboard: Page positioned on the dashboard.
    """
    dashboard.locator("[data-testid='health']").click()
    dashboard.wait_for_url(f"{_BASE}/health", timeout=5_000)
    dashboard.wait_for_selector(".pf-v6-c-table, div:has-text('Unable to reach')", timeout=10_000)


# ── Settings (unchanged) ──────────────────────────────────────────────


@pytest.fixture()  # type: ignore[untyped-decorator]
def settings_page(page: Page) -> Page:
    """Navigate to the Settings page.

    Args:
        page: Playwright page fixture.

    Returns:
        Page positioned on /settings.
    """
    page.goto(f"{_BASE}/settings", wait_until="networkidle")
    page.wait_for_selector("[data-testid='page-title']", timeout=10_000)
    return page


def test_settings_page_title(settings_page: Page) -> None:
    """Settings page displays correct title.

    Args:
        settings_page: Page positioned on /settings.
    """
    expect(settings_page.locator("[data-testid='page-title']")).to_have_text("Settings")


def test_settings_ai_config_heading(settings_page: Page) -> None:
    """Settings page shows AI Configuration heading.

    Args:
        settings_page: Page positioned on /settings.
    """
    expect(settings_page.locator("h3:has-text('AI Configuration')")).to_be_visible()


def test_settings_info_text(settings_page: Page) -> None:
    """Settings page shows explanatory text about model preference.

    Args:
        settings_page: Page positioned on /settings.
    """
    expect(settings_page.locator("p:has-text('stored in your browser')")).to_be_visible()
