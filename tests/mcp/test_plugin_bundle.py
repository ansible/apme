"""Tests for bundled APME Skill and MCP host configuration."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
CANONICAL_SKILL = ROOT / "skills/apme-scan-review/SKILL.md"
BUNDLE = ROOT / "plugins/apme"


def test_plugin_bundle_declares_skill_and_mcp_config() -> None:
    """Verify bundle metadata points to valid MCP and Skill files."""
    metadata = json.loads((BUNDLE / "plugin.json").read_text())
    config = json.loads((BUNDLE / "mcp.json").read_text())

    assert metadata["name"] == "apme"
    assert metadata["mcpConfig"] == "mcp.json"
    assert metadata["skills"] == ["skills/apme-scan-review/SKILL.md"]
    assert config["mcpServers"]["apme"]["command"] == "apme-mcp"
    assert config["mcpServers"]["apme"]["env"]["APME_GATEWAY_URL"] == "http://127.0.0.1:8080"


def test_plugin_bundle_skill_matches_canonical_skill() -> None:
    """Verify shipped Skill cannot drift from repository Skill source."""
    bundled_skill = BUNDLE / "skills/apme-scan-review/SKILL.md"

    assert bundled_skill.read_text() == CANONICAL_SKILL.read_text()
