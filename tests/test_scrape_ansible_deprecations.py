"""Regression tests for the ansible-core deprecation scraper."""

import runpy
from pathlib import Path


def test_inventory_plugin_api_deprecation_is_for_developers(tmp_path: Path) -> None:
    """Keep Python API deprecations out of the content-only inventory report.

    Args:
        tmp_path: Temporary ansible-core source root.
    """
    scraper = Path(__file__).resolve().parents[1] / "scripts" / "scrape_ansible_deprecations.py"
    module = runpy.run_path(str(scraper))
    scan_file = module["scan_file"]
    inventory = tmp_path / "plugins" / "inventory" / "__init__.py"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        'display.deprecated("The disable_lookups arg has no effect.", version="2.23")\n'
        'display.deprecated("The inventory option legacy_key is deprecated.", version="2.23")\n',
        encoding="utf-8",
    )

    entries = scan_file(inventory, tmp_path)

    assert [(entry.message, entry.audience) for entry in entries] == [
        ("The disable_lookups arg has no effect.", "developer"),
        ("The inventory option legacy_key is deprecated.", "content"),
    ]
    rules = module["inventory_existing_rules"]()
    assert module["_match_deprecation_to_rules"](entries[0], rules) == ["M040"]
