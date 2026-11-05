"""Playwright smoke tests for the default RHDH Local APME experience.

Run with ``tox -e up`` followed by ``tox -e ui``. The separate
``tox -e ui-native`` environment retains checks for the retired native host.
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

_BASE = os.environ.get("APME_UI_URL", "http://localhost:8081").rstrip("/")


@pytest.fixture()  # type: ignore[untyped-decorator]
def local_app(page: Page) -> Page:
    """Open Local and wait for its APME sidebar.

    Args:
        page: Playwright page fixture.

    Returns:
        Page positioned on the Local Projects route.
    """
    page.goto(_BASE, wait_until="domcontentloaded")
    brand = page.get_by_role("link", name="APME", exact=True)
    guest_sign_in = page.get_by_role("button", name=re.compile("guest", re.I))
    expect(brand.or_(guest_sign_in).first).to_be_visible(timeout=20_000)
    if guest_sign_in.is_visible():
        guest_sign_in.click()
    expect(brand).to_be_visible(timeout=20_000)
    expect(page.locator("#apme-local-sections")).to_be_visible()
    return page


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("label", "path"),
    [
        ("Projects", "/"),
        ("Rules", "/apme/rules"),
        ("Health", "/apme/health"),
        ("Paste / upload", "/apme/upload"),
        ("Settings", "/apme/settings"),
    ],
)
def test_local_apme_sections_navigate(
    local_app: Page,
    label: str,
    path: str,
) -> None:
    """Each APME sidebar entry opens its Local page.

    Args:
        local_app: Page positioned on the Local Projects route.
        label: Visible APME sidebar label.
        path: Expected application path.
    """
    local_app.get_by_role("link", name=label, exact=True).click()
    expect(local_app).to_have_url(f"{_BASE}{path}")
    expect(local_app.locator(".apme-page")).to_be_visible()


def test_brand_link_returns_to_projects(local_app: Page) -> None:
    """The APME brand link returns to Projects from a nested section.

    Args:
        local_app: Page positioned on the Local Projects route.
    """
    local_app.goto(f"{_BASE}/apme/settings", wait_until="domcontentloaded")
    local_app.get_by_role("link", name="APME", exact=True).click()
    expect(local_app).to_have_url(f"{_BASE}/")
    expect(local_app.locator(".apme-page")).to_be_visible()


def test_local_uploads_are_enabled(local_app: Page) -> None:
    """Local exposes the upload controls enabled by its host configuration.

    Args:
        local_app: Page positioned on the Local Projects route.
    """
    local_app.goto(f"{_BASE}/apme/upload", wait_until="domcontentloaded")
    expect(local_app.get_by_label("Paste Ansible content")).to_be_visible()
    expect(local_app.get_by_text("Select files", exact=True)).to_be_visible()
    expect(
        local_app.get_by_text("Uploads are unavailable for this account.", exact=True),
    ).to_have_count(0)


def test_local_theme_selector_switches_variants(local_app: Page) -> None:
    """The Local sidebar theme selector applies light and dark variants.

    Args:
        local_app: Page positioned on the Local Projects route.
    """
    theme = local_app.get_by_label("Color theme")
    expect(theme).to_be_visible()
    theme.select_option("light")
    root = local_app.locator("html")
    assert not root.evaluate(
        "element => element.classList.contains('pf-v6-theme-dark')",
    )
    theme.select_option("dark")
    assert root.evaluate("element => element.classList.contains('pf-v6-theme-dark')")
