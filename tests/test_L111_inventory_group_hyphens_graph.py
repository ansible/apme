"""Unit tests for GraphRule L111: inventory group names should not contain hyphens."""

from __future__ import annotations

import tempfile
from pathlib import Path

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L111_inventory_group_hyphens_graph import (
    InventoryGroupHyphensGraphRule,
    _find_inventory_files,
    _get_yaml_line_map,
    _looks_like_ini,
    _parse_ini_groups,
    _parse_yaml_groups,
)


class TestParseIniGroups:
    """Tests for _parse_ini_groups helper."""

    def test_simple_group(self) -> None:
        """Parse simple group name."""
        content = "[web-servers]\nhost1"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("web-servers", 1) in groups

    def test_group_with_children(self) -> None:
        """Parse group with :children suffix."""
        content = "[prod-env:children]\nweb-servers"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("prod-env", 1) in groups

    def test_group_with_vars(self) -> None:
        """Parse group with :vars suffix."""
        content = "[web-servers:vars]\nhttp_port=80"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("web-servers", 1) in groups

    def test_multiple_groups(self) -> None:
        """Parse multiple groups."""
        content = "[web-servers]\nhost1\n\n[db-servers]\nhost2"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert len(groups) == 2
        assert ("web-servers", 1) in groups
        assert ("db-servers", 4) in groups

    def test_reserved_groups_skipped(self) -> None:
        """Reserved groups (all, ungrouped) are skipped."""
        content = "[all]\nhost1\n\n[ungrouped]\nhost2\n\n[web-servers]\nhost3"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert len(groups) == 1
        assert ("web-servers", 7) in groups

    def test_underscore_groups_included(self) -> None:
        """Groups with underscores are still parsed."""
        content = "[web_servers]\nhost1"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("web_servers", 1) in groups


class TestParseYamlGroups:
    """Tests for _parse_yaml_groups helper."""

    def test_simple_children(self) -> None:
        """Parse simple children structure."""
        data: dict[str, object] = {"all": {"children": {"web-servers": {}, "db-servers": {}}}}
        groups = list(_parse_yaml_groups(data))
        group_names = [g[0] for g in groups]
        assert "web-servers" in group_names
        assert "db-servers" in group_names

    def test_nested_children(self) -> None:
        """Parse nested children structure."""
        data: dict[str, object] = {
            "all": {
                "children": {
                    "prod-env": {
                        "children": {
                            "web-servers": {},
                            "db-servers": {},
                        }
                    }
                }
            }
        }
        groups = list(_parse_yaml_groups(data))
        group_names = [g[0] for g in groups]
        assert "prod-env" in group_names
        assert "web-servers" in group_names
        assert "db-servers" in group_names

    def test_reserved_groups_skipped(self) -> None:
        """Reserved groups are skipped."""
        data: dict[str, object] = {"all": {"children": {"ungrouped": {}, "web-servers": {}}}}
        groups = list(_parse_yaml_groups(data))
        group_names = [g[0] for g in groups]
        assert "ungrouped" not in group_names
        assert "web-servers" in group_names


class TestLooksLikeIni:
    """Tests for _looks_like_ini helper."""

    def test_ini_format(self) -> None:
        """Detect INI format."""
        assert _looks_like_ini("[webservers]\nhost1")

    def test_yaml_format(self) -> None:
        """Reject YAML format."""
        assert not _looks_like_ini("all:\n  children:\n    webservers:")

    def test_ini_with_comments(self) -> None:
        """Detect INI with leading comments."""
        content = "# comment\n\n[webservers]"
        assert _looks_like_ini(content)


class TestGetYamlLineMap:
    """Tests for _get_yaml_line_map helper."""

    def test_simple_mapping(self) -> None:
        """Map simple YAML keys to lines."""
        content = "all:\n  children:\n    webservers:"
        line_map = _get_yaml_line_map(content)
        assert line_map.get("all") == 1
        assert line_map.get("all.children") == 2
        assert line_map.get("all.children.webservers") == 3


class TestFindInventoryFiles:
    """Tests for _find_inventory_files helper."""

    def test_finds_ini_file(self) -> None:
        """Find inventory.ini in playbook directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[webservers]\n")

            files = list(_find_inventory_files(str(playbook)))
            assert any(f.name == "inventory.ini" for f in files)

    def test_finds_yaml_file(self) -> None:
        """Find inventory.yml in playbook directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text("all:\n  children:\n")

            files = list(_find_inventory_files(str(playbook)))
            assert any(f.name == "inventory.yml" for f in files)

    def test_finds_hosts_file(self) -> None:
        """Find hosts file in playbook directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "hosts"
            inv_file.write_text("[webservers]\n")

            files = list(_find_inventory_files(str(playbook)))
            assert any(f.name == "hosts" for f in files)

    def test_searches_inventory_subdir(self) -> None:
        """Find inventory files in inventory/ subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_dir = Path(tmpdir) / "inventory"
            inv_dir.mkdir()
            inv_file = inv_dir / "hosts.yml"
            inv_file.write_text("all:\n  children:\n")

            files = list(_find_inventory_files(str(playbook)))
            assert any(f.name == "hosts.yml" for f in files)


class TestInventoryGroupHyphensGraphRule:
    """Tests for the L111 GraphRule."""

    def _make_graph_with_playbook(self, playbook_path: str) -> tuple[ContentGraph, str]:
        """Create a minimal graph with a PLAYBOOK node.

        Args:
            playbook_path: Path to the playbook file.

        Returns:
            Tuple of (graph, playbook_node_id).
        """
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path=playbook_path, node_type=NodeType.PLAYBOOK),
            file_path=playbook_path,
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        return g, pb.node_id

    def test_detects_hyphen_in_ini_group(self) -> None:
        """Rule detects hyphens in INI inventory group names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\nhost1\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            assert rule.match(graph, pb_id)
            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "web-servers" in str(result.detail)

    def test_detects_hyphen_in_yaml_group(self) -> None:
        """Rule detects hyphens in YAML inventory group names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text("all:\n  children:\n    web-servers:\n      hosts:\n        host1:\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            assert rule.match(graph, pb_id)
            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "web-servers" in str(result.detail)

    def test_passes_underscore_groups(self) -> None:
        """Rule passes when groups use underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web_servers]\nhost1\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is False

    def test_passes_no_inventory(self) -> None:
        """Rule passes when no inventory files found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is False

    def test_multiple_violations(self) -> None:
        """Rule reports multiple violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\n[db-servers]\n[app_servers]\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert isinstance(result.detail, dict)
            violations = result.detail.get("violations", [])
            assert isinstance(violations, list)
            assert len(violations) == 2

    def test_suggests_underscore_replacement(self) -> None:
        """Rule suggests underscore replacement in message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert isinstance(result.detail, dict)
            violations = result.detail.get("violations", [])
            assert isinstance(violations, list)
            assert len(violations) == 1
            first_violation = violations[0]
            assert isinstance(first_violation, dict)
            assert first_violation.get("suggested") == "web_servers"

    def test_no_match_for_non_playbook(self) -> None:
        """Rule does not match non-PLAYBOOK nodes."""
        g = ContentGraph()
        task = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            file_path="test.yml",
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        rule = InventoryGroupHyphensGraphRule()

        assert not rule.match(g, task.node_id)

    def test_scanner_integration(self) -> None:
        """Rule integrates correctly with graph scanner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\n")

            graph, _ = self._make_graph_with_playbook(str(playbook))
            rules: list[GraphRule] = [InventoryGroupHyphensGraphRule()]

            report = scan(graph, rules, owned_only=True)

            violations = [r for nr in report.node_results for r in nr.rule_results if r.verdict]
            assert len(violations) == 1
            assert violations[0].rule is not None
            assert violations[0].rule.rule_id == "L111"

    def test_children_suffix_detected(self) -> None:
        """Rule detects hyphens in groups with :children suffix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[prod-env:children]\nweb-servers\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "prod-env" in str(result.detail)

    def test_vars_suffix_detected(self) -> None:
        """Rule detects hyphens in groups with :vars suffix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers:vars]\nhttp_port=80\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "web-servers" in str(result.detail)
