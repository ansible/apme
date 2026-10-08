"""Example org-policy plugin: flag ``community.general.*`` modules (ADR-042).

Run (after Engine is up)::

    APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/orgpolicy/plugin.py

Point Engine at it::

    export APME_PLUGIN_ORGPOLICY_ADDRESS=127.0.0.1:50100
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

# Allow ``python examples/plugins/orgpolicy/plugin.py`` from repo root.
_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from apme_plugin_sdk import PluginBase  # noqa: E402


class OrgPolicyPlugin(PluginBase):
    """Banned-collection policy (the use case that does not belong in built-in OPA)."""

    name = "orgpolicy"
    version = "1.0.0"

    def transform_rule_ids(self) -> list[str]:
        """No deterministic rewrite for collection bans.

        Returns:
            Empty list — remaining findings are manual review until ADR-042 Phase 4.
        """
        return []

    def validate(
        self,
        files: Sequence[tuple[str, bytes]],
        hierarchy: object,
    ) -> list[dict[str, str | int]]:
        """Flag task nodes whose module starts with ``community.general.``.

        Args:
            files: Unused (hierarchy has module names).
            hierarchy: Engine hierarchy JSON.

        Returns:
            EXT-orgpolicy-001 violations.
        """
        del files
        violations: list[dict[str, str | int]] = []
        trees = _hierarchy_trees(hierarchy)
        for tree in trees:
            nodes = tree.get("nodes") if isinstance(tree, dict) else None
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                module = str(node.get("module") or "")
                if not module.startswith("community.general."):
                    continue
                line_raw = node.get("line")
                line = 0
                if isinstance(line_raw, list) and line_raw:
                    try:
                        line = int(line_raw[0])
                    except (TypeError, ValueError):
                        line = 0
                elif isinstance(line_raw, int):
                    line = line_raw
                violations.append(
                    self.violation(
                        rule_id="001",
                        message=f"Banned collection: {module}",
                        file=str(node.get("file") or ""),
                        line=line,
                        path=str(node.get("key") or node.get("path") or ""),
                        severity="high",
                        ai_guidance=(
                            "Replace community.general modules with ansible.builtin "
                            "or a certified collection equivalent. There is no "
                            "automatic 1:1 rewrite."
                        ),
                    )
                )
        return violations


def _hierarchy_trees(hierarchy: object) -> list[object]:
    """Normalize hierarchy_payload shapes to a list of trees.

    Args:
        hierarchy: Parsed JSON from Engine.

    Returns:
        List of tree objects that may contain ``nodes``.
    """
    if hierarchy is None:
        return []
    if isinstance(hierarchy, list):
        return list(hierarchy)
    if isinstance(hierarchy, dict):
        inner = hierarchy.get("hierarchy")
        if isinstance(inner, list):
            return list(inner)
        return [hierarchy]
    return []


if __name__ == "__main__":
    OrgPolicyPlugin.serve()
