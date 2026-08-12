"""Unit tests for GraphRule L111: inventory group names should not contain hyphens."""

from __future__ import annotations

import tempfile
from pathlib import Path

from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.rules.L111_inventory_group_hyphens_graph import (
    InventoryGroupHyphensGraphRule,
    _find_inventory_files,
    _get_yaml_line_map,
    _looks_like_ini,
    _looks_like_yaml,
    _parse_ini_groups,
    _parse_yaml_groups,
    _parse_yaml_inventory,
)
from apme_engine.graph.scanner import graph_report_to_violations, rescan_dirty, scan


class TestParseIniGroups:
    """Tests for _parse_ini_groups helper."""

    def test_simple_group(self) -> None:
        """Parse simple group name."""
        content = "[web-servers]\nhost1"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("web-servers", 1) in groups

    def test_group_with_children(self) -> None:
        """Parse group with :children suffix and listed child groups."""
        content = "[prod-env:children]\nweb-servers"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("prod-env", 1) in groups
        assert ("web-servers", 2) in groups

    def test_children_section_hyphenated_child_groups(self) -> None:
        """Parse hyphenated child groups declared under a :children section."""
        content = "[db-servers:children]\nprimary-db\nreplica-db"
        groups = list(_parse_ini_groups(content, "test.ini"))
        assert ("db-servers", 1) in groups
        assert ("primary-db", 2) in groups
        assert ("replica-db", 3) in groups

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

    def _make_graph_with_playbooks(self, playbook_paths: list[str]) -> tuple[ContentGraph, list[str]]:
        """Create a graph with multiple PLAYBOOK nodes.

        Args:
            playbook_paths: Paths to playbook files.

        Returns:
            Tuple of (graph, playbook node IDs).
        """
        g = ContentGraph()
        node_ids: list[str] = []
        for playbook_path in playbook_paths:
            pb = ContentNode(
                identity=NodeIdentity(path=playbook_path, node_type=NodeType.PLAYBOOK),
                file_path=playbook_path,
                scope=NodeScope.OWNED,
            )
            g.add_node(pb)
            node_ids.append(pb.node_id)
        return g, node_ids

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
            assert "web-servers" in str(result.detail)

    def test_children_section_child_group_detected(self) -> None:
        """Rule detects hyphenated child groups listed under :children sections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[db-servers:children]\nprimary-db\nreplica-db\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "primary-db" in str(result.detail)
            assert "replica-db" in str(result.detail)

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

    def test_extensionless_yaml_inventory(self) -> None:
        """Rule detects hyphens in extensionless YAML inventory files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            # No extension, but YAML content
            inv_file = Path(tmpdir) / "inventory"
            inv_file.write_text("all:\n  children:\n    web-servers:\n      hosts:\n        host1:\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "web-servers" in str(result.detail)

    def test_extensionless_hosts_yaml_inventory(self) -> None:
        """Rule detects hyphens in extensionless 'hosts' file with YAML content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "hosts"
            inv_file.write_text("all:\n  children:\n    db-servers:\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()

            result = rule.process(graph, pb_id)

            assert result is not None
            assert result.verdict is True
            assert "db-servers" in str(result.detail)

    def test_skips_second_playbook_in_same_directory(self) -> None:
        """Only the first playbook in a directory triggers inventory scanning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook1 = Path(tmpdir) / "playbook1.yml"
            playbook2 = Path(tmpdir) / "playbook2.yml"
            playbook1.write_text("---\n- hosts: all\n")
            playbook2.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\nhost1\n")

            graph, node_ids = self._make_graph_with_playbooks([str(playbook1), str(playbook2)])
            rule = InventoryGroupHyphensGraphRule()

            assert rule.match(graph, node_ids[0])
            assert rule.match(graph, node_ids[1])

            first_result = rule.process(graph, node_ids[0])
            second_result = rule.process(graph, node_ids[1])

            assert first_result is not None
            assert first_result.verdict is True
            assert second_result is not None
            assert second_result.verdict is False

    def test_rescan_dirty_re_evaluates_same_directory(self) -> None:
        """rescan_dirty resets per-scan state so dirty playbooks are re-evaluated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook = Path(tmpdir) / "playbook.yml"
            playbook.write_text("---\n- hosts: all\n")
            inv_file = Path(tmpdir) / "inventory.ini"
            inv_file.write_text("[web-servers]\nhost1\n")

            graph, pb_id = self._make_graph_with_playbook(str(playbook))
            rule = InventoryGroupHyphensGraphRule()
            rules: list[GraphRule] = [rule]

            first_report = rescan_dirty(graph, rules, frozenset({pb_id}))
            first_violations = graph_report_to_violations(first_report)
            assert len(first_violations) == 1

            second_report = rescan_dirty(graph, rules, frozenset({pb_id}))
            second_violations = graph_report_to_violations(second_report)
            assert len(second_violations) == 1

    def test_fixture_inventory_files(self) -> None:
        """Fixture inventory files produce expected hyphen violations."""
        fixture_dir = Path(__file__).parent / "fixtures" / "L111-inventory-hyphens"
        playbook = fixture_dir / "playbook.yml"

        graph, pb_id = self._make_graph_with_playbook(str(playbook))
        rule = InventoryGroupHyphensGraphRule()

        result = rule.process(graph, pb_id)

        assert result is not None
        assert result.verdict is True
        assert isinstance(result.detail, dict)
        violations = result.detail.get("violations", [])
        assert isinstance(violations, list)
        group_names = {v["group_name"] for v in violations if isinstance(v, dict)}
        assert "web-servers" in group_names
        assert "db-servers" in group_names
        assert "production-env" in group_names


class TestLooksLikeYaml:
    """Tests for _looks_like_yaml helper."""

    def test_yaml_with_all_key(self) -> None:
        """Detect YAML with 'all:' key."""
        content = "all:\n  children:\n    webservers:\n"
        assert _looks_like_yaml(content) is True

    def test_yaml_with_children_key(self) -> None:
        """Detect YAML with 'children:' key."""
        content = "children:\n  webservers:\n"
        assert _looks_like_yaml(content) is True

    def test_yaml_with_hosts_key(self) -> None:
        """Detect YAML with 'hosts:' key."""
        content = "hosts:\n  host1:\n"
        assert _looks_like_yaml(content) is True

    def test_yaml_with_colon_value(self) -> None:
        """Detect YAML with key: value pattern."""
        content = "server_name: web-01\n"
        assert _looks_like_yaml(content) is True

    def test_ini_not_yaml(self) -> None:
        """INI content is not YAML."""
        content = "[webservers]\nhost1\n"
        assert _looks_like_yaml(content) is False

    def test_empty_not_yaml(self) -> None:
        """Empty content is not YAML."""
        content = ""
        assert _looks_like_yaml(content) is False

    def test_comments_only_not_yaml(self) -> None:
        """Comments only is not YAML."""
        content = "# Just a comment\n# Another comment\n"
        assert _looks_like_yaml(content) is False


class TestParseYamlInventory:
    """Tests for _parse_yaml_inventory helper."""

    def test_parses_hyphens(self) -> None:
        """Parse groups with hyphens from YAML."""
        content = "all:\n  children:\n    web-servers:\n    db-servers:\n"
        result = _parse_yaml_inventory(content)
        group_names = [g[0] for g in result]
        assert "web-servers" in group_names
        assert "db-servers" in group_names

    def test_deduplicates(self) -> None:
        """Deduplicate groups that appear multiple times via cross-refs."""
        content = "all:\n  children:\n    web-servers:\n      children:\n        web-servers:\n"
        result = _parse_yaml_inventory(content)
        group_names = [g[0] for g in result]
        assert group_names.count("web-servers") == 1

    def test_handles_invalid_yaml(self) -> None:
        """Return empty list for invalid YAML."""
        content = "::invalid::\nyaml: [{"
        result = _parse_yaml_inventory(content)
        assert result == []

    def test_no_hyphens_returns_empty(self) -> None:
        """Return empty when no hyphens in groups."""
        content = "all:\n  children:\n    web_servers:\n"
        result = _parse_yaml_inventory(content)
        assert result == []
