"""Tests for NodeIndex, enrich_violations, and build_reverse_index."""

import textwrap
from pathlib import Path

from apme_engine.engine.models import ViolationDict
from apme_engine.engine.node_index import NodeIndex
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.enrich import build_reverse_index, enrich_violations
from apme_engine.remediation.registry import TransformRegistry, TransformResult
from apme_engine.remediation.transforms.L021_missing_mode import fix_missing_mode


def _make_payload(*node_dicts: dict) -> dict:
    """Build a minimal hierarchy payload from node dicts.

    Args:
        *node_dicts: Dicts with at least key, type, file, line.

    Returns:
        Dict shaped like ARIScanner.build_hierarchy_payload() output.
    """
    return {"hierarchy": [{"nodes": list(node_dicts)}]}


# ---------------------------------------------------------------------------
# NodeIndex
# ---------------------------------------------------------------------------


class TestNodeIndex:
    """Tests for NodeIndex construction, lookup, parent_key, ancestors."""

    def test_get_returns_node(self) -> None:
        """Verifies get() returns the node dict for a known key."""
        payload = _make_payload(
            {"key": "task pb:site.yml#play:0#task:1", "type": "taskcall", "file": "/a/site.yml", "line": [5, 8]},
        )
        idx = NodeIndex(payload)
        node = idx.get("task pb:site.yml#play:0#task:1")
        assert node is not None
        assert node["type"] == "taskcall"

    def test_get_returns_none_for_missing(self) -> None:
        """Verifies get() returns None for an unknown key."""
        idx = NodeIndex({"hierarchy": []})
        assert idx.get("missing") is None

    def test_contains(self) -> None:
        """Verifies __contains__ returns True for known, False for unknown."""
        payload = _make_payload({"key": "play pb:a.yml#play:0", "type": "playcall", "file": "/a.yml", "line": [1, 1]})
        idx = NodeIndex(payload)
        assert "play pb:a.yml#play:0" in idx
        assert "missing" not in idx

    def test_len(self) -> None:
        """Verifies __len__ returns the number of indexed nodes."""
        payload = _make_payload(
            {"key": "k1", "type": "t", "file": "/a.yml", "line": [1, 1]},
            {"key": "k2", "type": "t", "file": "/b.yml", "line": [2, 2]},
        )
        idx = NodeIndex(payload)
        assert len(idx) == 2

    def test_find_by_file_line(self) -> None:
        """Verifies find_by_file_line returns the correct node."""
        payload = _make_payload(
            {"key": "task1", "type": "taskcall", "file": "/a/site.yml", "line": [5, 8]},
            {"key": "task2", "type": "taskcall", "file": "/a/site.yml", "line": [10, 12]},
        )
        idx = NodeIndex(payload)
        assert idx.find_by_file_line("/a/site.yml", 5) is not None
        assert idx.find_by_file_line("/a/site.yml", 5)["key"] == "task1"
        assert idx.find_by_file_line("/a/site.yml", 10)["key"] == "task2"
        assert idx.find_by_file_line("/a/site.yml", 99) is None

    def test_parent_key(self) -> None:
        """Verifies parent_key strips the last segment."""
        assert NodeIndex.parent_key("task pb:site.yml#play:0#task:1") == "task pb:site.yml#play:0"
        assert NodeIndex.parent_key("task pb:site.yml#play:0") == "task pb:site.yml"
        assert NodeIndex.parent_key("task pb:site.yml") is None
        assert NodeIndex.parent_key("") is None

    def test_ancestors_returns_parent_chain(self) -> None:
        """Verifies ancestors returns parent nodes from nearest to root."""
        payload = _make_payload(
            {"key": "root", "type": "playbook", "file": "/a.yml", "line": None},
            {"key": "root#play:0", "type": "playcall", "file": "/a.yml", "line": [1, 1]},
            {"key": "root#play:0#task:1", "type": "taskcall", "file": "/a.yml", "line": [5, 8]},
        )
        idx = NodeIndex(payload)
        ancestors = idx.ancestors("root#play:0#task:1")
        assert len(ancestors) == 2
        assert ancestors[0]["key"] == "root#play:0"
        assert ancestors[1]["key"] == "root"

    def test_ancestors_returns_empty_for_root(self) -> None:
        """Verifies ancestors returns empty for a root node."""
        payload = _make_payload({"key": "root", "type": "playbook", "file": "/a.yml", "line": None})
        idx = NodeIndex(payload)
        assert idx.ancestors("root") == []

    def test_empty_payload(self) -> None:
        """Verifies empty payload produces empty index."""
        idx = NodeIndex({"hierarchy": []})
        assert len(idx) == 0
        assert idx.get("anything") is None

    def test_multiple_trees(self) -> None:
        """Verifies nodes from multiple trees are all indexed."""
        payload = {
            "hierarchy": [
                {"nodes": [{"key": "a", "type": "t", "file": "/a.yml", "line": [1, 1]}]},
                {"nodes": [{"key": "b", "type": "t", "file": "/b.yml", "line": [1, 1]}]},
            ],
        }
        idx = NodeIndex(payload)
        assert len(idx) == 2
        assert "a" in idx
        assert "b" in idx


# ---------------------------------------------------------------------------
# enrich_violations
# ---------------------------------------------------------------------------


class TestEnrichViolations:
    """Tests for enrich_violations function."""

    def test_keeps_valid_path(self) -> None:
        """Verifies existing valid path is left as-is."""
        payload = _make_payload({"key": "k1", "type": "t", "file": "/a.yml", "line": [5, 5]})
        idx = NodeIndex(payload)
        violations: list[ViolationDict] = [{"rule_id": "L007", "file": "/a.yml", "line": 5, "path": "k1"}]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == "k1"

    def test_fills_missing_path_by_file_line(self) -> None:
        """Verifies empty path is filled from file+line lookup."""
        payload = _make_payload({"key": "task1", "type": "taskcall", "file": "/a.yml", "line": [5, 8]})
        idx = NodeIndex(payload)
        violations: list[ViolationDict] = [{"rule_id": "L007", "file": "/a.yml", "line": 5, "path": ""}]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == "task1"

    def test_fills_when_path_absent(self) -> None:
        """Verifies path is added when not present at all."""
        payload = _make_payload({"key": "task1", "type": "taskcall", "file": "/a.yml", "line": [5, 8]})
        idx = NodeIndex(payload)
        violations: list[ViolationDict] = [{"rule_id": "L007", "file": "/a.yml", "line": 5}]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == "task1"

    def test_leaves_empty_when_no_match(self) -> None:
        """Verifies path stays empty when no file+line match exists."""
        idx = NodeIndex({"hierarchy": []})
        violations: list[ViolationDict] = [{"rule_id": "L007", "file": "/a.yml", "line": 5, "path": ""}]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == ""

    def test_replaces_invalid_path(self) -> None:
        """Verifies invalid (not-in-index) path is replaced by file+line match."""
        payload = _make_payload({"key": "task1", "type": "taskcall", "file": "/a.yml", "line": [5, 8]})
        idx = NodeIndex(payload)
        violations: list[ViolationDict] = [
            {"rule_id": "L007", "file": "/a.yml", "line": 5, "path": "bogus_key"},
        ]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == "task1"

    def test_no_change_without_file(self) -> None:
        """Verifies violations without file are not enriched."""
        payload = _make_payload({"key": "k1", "type": "t", "file": "/a.yml", "line": [5, 5]})
        idx = NodeIndex(payload)
        violations: list[ViolationDict] = [{"rule_id": "L007", "line": 5, "path": ""}]
        enrich_violations(violations, idx)
        assert violations[0]["path"] == ""


# ---------------------------------------------------------------------------
# build_reverse_index
# ---------------------------------------------------------------------------


class TestBuildReverseIndex:
    """Tests for build_reverse_index function."""

    def test_groups_by_path(self) -> None:
        """Verifies violations are grouped by their path."""
        violations: list[ViolationDict] = [
            {"rule_id": "L007", "path": "k1"},
            {"rule_id": "L008", "path": "k1"},
            {"rule_id": "L009", "path": "k2"},
        ]
        rev = build_reverse_index(violations)
        assert len(rev["k1"]) == 2
        assert len(rev["k2"]) == 1

    def test_empty_path_collected(self) -> None:
        """Verifies violations without path are grouped under empty string."""
        violations: list[ViolationDict] = [
            {"rule_id": "L007", "path": ""},
            {"rule_id": "L008"},
        ]
        rev = build_reverse_index(violations)
        assert len(rev[""]) == 2

    def test_empty_list(self) -> None:
        """Verifies empty violation list returns empty dict."""
        rev = build_reverse_index([])
        assert rev == {}


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


class TestEngineWithNodeIndex:
    """Tests that RemediationEngine works correctly with a NodeIndex."""

    def test_engine_accepts_node_index(self, tmp_path: Path) -> None:
        """Verifies engine runs normally when node_index is provided.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text(
            textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )

        payload = _make_payload(
            {"key": "task pb:play.yml#play:0#task:0", "type": "taskcall", "file": str(playbook), "line": [1, 4]},
        )
        node_index = NodeIndex(payload)

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1, "path": ""}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5, node_index=node_index)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1
        assert "mode:" in playbook.read_text()

    def test_engine_works_without_node_index(self, tmp_path: Path) -> None:
        """Verifies engine runs normally when node_index is None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text(
            textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1

    def test_enrichment_sets_path_during_remediation(self, tmp_path: Path) -> None:
        """Verifies violations get path enrichment during remediation.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  ansible.builtin.debug:\n    msg: hi\n")

        node_key = "task pb:play.yml#play:0#task:0"
        payload = _make_payload(
            {"key": node_key, "type": "taskcall", "file": str(playbook), "line": [1, 3]},
        )
        node_index = NodeIndex(payload)

        captured_violations: list[ViolationDict] = []

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [{"rule_id": "UNKNOWN", "file": str(playbook), "line": 1, "path": ""}]

        def noop_transform(content: str, violation: ViolationDict) -> TransformResult:
            captured_violations.append(dict(violation))
            return TransformResult(content, False)

        reg = TransformRegistry()
        reg.register("UNKNOWN", noop_transform)
        engine = RemediationEngine(reg, scan_fn, max_passes=1, node_index=node_index)

        engine.remediate([str(playbook)], apply=False)
        assert len(captured_violations) >= 1
        assert captured_violations[0].get("path") == node_key
