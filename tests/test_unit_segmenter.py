"""Tests for unit segmentation and unit-level AI escalation."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

from apme_engine.engine.models import ViolationDict
from apme_engine.engine.node_index import NodeIndex
from apme_engine.remediation.ai_provider import AIPatch
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.registry import TransformRegistry
from apme_engine.remediation.unit_segmenter import (
    FixableUnit,
    assign_violations_to_units,
    extract_units,
    group_violations_by_file,
)


def _make_payload(*node_dicts: dict[str, object]) -> dict[str, object]:
    """Build a minimal hierarchy payload from node dicts.

    Args:
        *node_dicts: Node dicts with key, type, file, line.

    Returns:
        Hierarchy payload dict.
    """
    return {"hierarchy": [{"nodes": list(node_dicts)}]}


PLAYBOOK_CONTENT = textwrap.dedent("""\
- name: Test play
  hosts: all
  tasks:
    - name: Install package
      yum:
        name: httpd
        state: latest

    - name: Copy config
      copy:
        src: /tmp/a
        dest: /tmp/b

    - name: Restart service
      service:
        name: httpd
        state: restarted
""")


class TestExtractUnits:
    """Tests for extract_units."""

    def test_extracts_tasks_from_hierarchy(self) -> None:
        """Verifies tasks are extracted with correct line ranges."""
        payload = _make_payload(
            {"key": "task0", "type": "taskcall", "file": "/a/play.yml", "line": [4, 7]},
            {"key": "task1", "type": "taskcall", "file": "/a/play.yml", "line": [9, 11]},
            {"key": "task2", "type": "taskcall", "file": "/a/play.yml", "line": [13, 16]},
        )
        idx = NodeIndex(payload)

        units = extract_units("/a/play.yml", PLAYBOOK_CONTENT, idx)

        assert len(units) == 3
        assert units[0].line_start == 4
        assert units[0].line_end == 7
        assert "yum" in units[0].snippet
        assert units[1].line_start == 9
        assert units[2].line_start == 13

    def test_ignores_non_taskcall_nodes(self) -> None:
        """Verifies play-level nodes are not extracted as units."""
        payload = _make_payload(
            {"key": "play0", "type": "playcall", "file": "/a/play.yml", "line": [1, 16]},
            {"key": "task0", "type": "taskcall", "file": "/a/play.yml", "line": [4, 7]},
        )
        idx = NodeIndex(payload)

        units = extract_units("/a/play.yml", PLAYBOOK_CONTENT, idx)

        assert len(units) == 1
        assert units[0].node_type == "taskcall"

    def test_ignores_nodes_from_other_files(self) -> None:
        """Verifies nodes from other files are not included."""
        payload = _make_payload(
            {"key": "task0", "type": "taskcall", "file": "/other.yml", "line": [1, 5]},
        )
        idx = NodeIndex(payload)

        units = extract_units("/a/play.yml", PLAYBOOK_CONTENT, idx)

        assert len(units) == 0

    def test_empty_index_returns_no_units(self) -> None:
        """Verifies empty NodeIndex produces no units."""
        idx = NodeIndex({"hierarchy": []})
        units = extract_units("/a/play.yml", PLAYBOOK_CONTENT, idx)
        assert len(units) == 0


class TestAssignViolations:
    """Tests for assign_violations_to_units."""

    def test_assigns_by_path(self) -> None:
        """Verifies violations with matching path are assigned to units."""
        unit = FixableUnit(
            node_key="task0",
            node_type="taskcall",
            file="/a.yml",
            line_start=4,
            line_end=7,
            snippet="...",
        )
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "file": "/a.yml", "line": 5, "path": "task0"},
        ]

        orphans = assign_violations_to_units([unit], violations)

        assert len(orphans) == 0
        assert len(unit.violations) == 1

    def test_assigns_by_line_range(self) -> None:
        """Verifies violations without path fall back to line range matching."""
        unit = FixableUnit(
            node_key="task0",
            node_type="taskcall",
            file="/a.yml",
            line_start=4,
            line_end=7,
            snippet="...",
        )
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "file": "/a.yml", "line": 5, "path": ""},
        ]

        orphans = assign_violations_to_units([unit], violations)

        assert len(orphans) == 0
        assert len(unit.violations) == 1

    def test_orphans_outside_any_unit(self) -> None:
        """Verifies violations outside all units become orphans."""
        unit = FixableUnit(
            node_key="task0",
            node_type="taskcall",
            file="/a.yml",
            line_start=4,
            line_end=7,
            snippet="...",
        )
        violations: list[ViolationDict] = [
            {"rule_id": "L003", "file": "/a.yml", "line": 1, "path": ""},
        ]

        orphans = assign_violations_to_units([unit], violations)

        assert len(orphans) == 1
        assert len(unit.violations) == 0

    def test_multiple_violations_same_unit(self) -> None:
        """Verifies multiple violations map to the same unit."""
        unit = FixableUnit(
            node_key="task0",
            node_type="taskcall",
            file="/a.yml",
            line_start=4,
            line_end=7,
            snippet="...",
        )
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "file": "/a.yml", "line": 5, "path": "task0"},
            {"rule_id": "M001", "file": "/a.yml", "line": 5, "path": "task0"},
        ]

        orphans = assign_violations_to_units([unit], violations)

        assert len(orphans) == 0
        assert len(unit.violations) == 2


class TestGroupByFile:
    """Tests for group_violations_by_file."""

    def test_groups_by_resolved_path(self) -> None:
        """Verifies violations are grouped by resolved file path."""
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "file": "/a.yml", "line": 5},
            {"rule_id": "M001", "file": "/a.yml", "line": 10},
            {"rule_id": "L007", "file": "/b.yml", "line": 3},
        ]

        result = group_violations_by_file(violations, lambda x: x)

        assert len(result) == 2
        assert len(result["/a.yml"]) == 2
        assert len(result["/b.yml"]) == 1


class TestEngineUnitEscalation:
    """Integration tests for unit-level AI escalation in the engine."""

    def test_escalation_uses_units_when_index_available(self, tmp_path: Path) -> None:
        """Verifies unit-level propose_unit_fixes is called when NodeIndex is present.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text(PLAYBOOK_CONTENT)

        payload = _make_payload(
            {"key": "task0", "type": "taskcall", "file": str(playbook), "line": [4, 7]},
            {"key": "task1", "type": "taskcall", "file": str(playbook), "line": [9, 11]},
        )
        node_index = NodeIndex(payload)

        mock_provider = AsyncMock()
        mock_provider.propose_unit_fixes = AsyncMock(
            return_value=(
                [
                    AIPatch(
                        rule_id="M001",
                        line_start=5,
                        line_end=5,
                        fixed_lines="      ansible.builtin.yum:",
                        explanation="FQCN",
                        confidence=0.95,
                    )
                ],
                [],
            )
        )
        mock_provider.propose_fixes = AsyncMock(return_value=(None, []))

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [
                {"rule_id": "M001", "file": str(playbook), "line": 5, "path": "task0"},
            ]

        reg = TransformRegistry()
        engine = RemediationEngine(
            reg,
            scan_fn,
            max_passes=1,
            node_index=node_index,
            ai_provider=mock_provider,
        )

        report = engine.remediate([str(playbook)], apply=False)

        assert mock_provider.propose_unit_fixes.call_count >= 1
        assert len(report.ai_proposed) >= 1

    def test_falls_back_to_full_file_without_index(self, tmp_path: Path) -> None:
        """Verifies full-file propose_fixes is used when no NodeIndex.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text(PLAYBOOK_CONTENT)

        mock_provider = AsyncMock()
        mock_provider.propose_fixes = AsyncMock(
            return_value=(
                [
                    AIPatch(
                        rule_id="M001",
                        line_start=5,
                        line_end=5,
                        fixed_lines="      ansible.builtin.yum:",
                        explanation="FQCN",
                        confidence=0.95,
                    )
                ],
                [],
            )
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [
                {"rule_id": "M001", "file": str(playbook), "line": 5},
            ]

        reg = TransformRegistry()
        engine = RemediationEngine(
            reg,
            scan_fn,
            max_passes=1,
            ai_provider=mock_provider,
        )

        report = engine.remediate([str(playbook)], apply=False)

        assert mock_provider.propose_fixes.call_count >= 1
        assert len(report.ai_proposed) >= 1

    def test_orphans_fall_back_to_full_file(self, tmp_path: Path) -> None:
        """Verifies orphan violations trigger a full-file fallback call.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text(PLAYBOOK_CONTENT)

        payload = _make_payload(
            {"key": "task0", "type": "taskcall", "file": str(playbook), "line": [4, 7]},
        )
        node_index = NodeIndex(payload)

        mock_provider = AsyncMock()
        mock_provider.propose_unit_fixes = AsyncMock(return_value=(None, []))
        mock_provider.propose_fixes = AsyncMock(
            return_value=(
                [
                    AIPatch(
                        rule_id="L003",
                        line_start=1,
                        line_end=1,
                        fixed_lines="- name: Test play",
                        explanation="play-level fix",
                        confidence=0.9,
                    )
                ],
                [],
            )
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [
                {"rule_id": "L003", "file": str(playbook), "line": 1, "path": ""},
            ]

        reg = TransformRegistry()
        engine = RemediationEngine(
            reg,
            scan_fn,
            max_passes=1,
            node_index=node_index,
            ai_provider=mock_provider,
        )

        engine.remediate([str(playbook)], apply=False)

        assert mock_provider.propose_fixes.call_count >= 1
