"""Tests for ``apme_plugin_sdk.PluginBase`` and the example orgpolicy plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from apme_plugin_sdk.base import PluginBase, _dict_to_violation


class _SamplePlugin(PluginBase):
    """Minimal plugin used in unit tests.

    Attributes:
        name: Plugin name used in EXT- rule IDs.
        version: Semver string.
    """

    name = "secteam"
    version = "9.9.9"

    def transform_rule_ids(self) -> list[str]:
        """Declare one Transform-capable rule.

        Returns:
            Prefixed rule IDs.
        """
        return [self.prefixed_id("010")]


def test_prefixed_id_and_describe() -> None:
    """``EXT-<name>-`` prefix is applied consistently."""
    plugin = _SamplePlugin()
    assert plugin.rule_id_prefix == "EXT-secteam-"
    assert plugin.prefixed_id("001") == "EXT-secteam-001"
    assert plugin.prefixed_id("EXT-secteam-001") == "EXT-secteam-001"
    desc = plugin.describe()
    assert desc.name == "secteam"
    assert desc.version == "9.9.9"
    assert desc.rule_id_prefix == "EXT-secteam-"
    assert list(desc.transform_rule_ids) == ["EXT-secteam-010"]


def test_violation_helper_sets_ai_guidance() -> None:
    """``violation()`` stores optional ``ai_guidance`` for Tier 2."""
    plugin = _SamplePlugin()
    item = plugin.violation(
        rule_id="001",
        message="no secrets",
        file="play.yml",
        line=3,
        ai_guidance="Remove the hardcoded token.",
    )
    assert item["rule_id"] == "EXT-secteam-001"
    proto = _dict_to_violation(item, plugin.rule_id_prefix)
    assert proto.rule_id == "EXT-secteam-001"
    assert proto.metadata["ai_guidance"] == "Remove the hardcoded token."
    assert proto.line == 3


def test_dict_to_violation_prefixes_bare_ids() -> None:
    """Bare rule IDs from validate() are prefixed before they hit the wire."""
    proto = _dict_to_violation({"rule_id": "007", "message": "x"}, "EXT-secteam-")
    assert proto.rule_id == "EXT-secteam-007"


def _load_orgpolicy() -> ModuleType:
    """Load the example plugin module from the repo examples tree.

    Returns:
        Imported module object.
    """
    path = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "orgpolicy" / "plugin.py"
    spec = importlib.util.spec_from_file_location("orgpolicy_example", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_orgpolicy_example_flags_community_general() -> None:
    """Example plugin emits ``EXT-orgpolicy-001`` for banned modules."""
    mod = _load_orgpolicy()
    plugin = mod.OrgPolicyPlugin()
    hierarchy = {
        "hierarchy": [
            {
                "nodes": [
                    {
                        "module": "community.general.apk",
                        "file": "site.yml",
                        "line": [4, 7],
                        "key": "site.yml/plays[0]/tasks[0]",
                    },
                    {
                        "module": "ansible.builtin.debug",
                        "file": "site.yml",
                        "line": 10,
                        "key": "site.yml/plays[0]/tasks[1]",
                    },
                ]
            }
        ]
    }
    findings = plugin.validate([], hierarchy)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "EXT-orgpolicy-001"
    assert "community.general.apk" in findings[0]["message"]
    assert plugin.transform_rule_ids() == []
