"""Unit tests for GraphRule M047: inventory plugin disable_lookups has no effect."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.M047_disable_lookups_no_effect_graph import (
    DisableLookupsNoEffectGraphRule,
    _find_inventory_files,
    _top_level_disable_lookups_line,
)


class TestFindInventoryFiles:
    """Tests for YAML inventory discovery."""

    def test_finds_constructed_yml_in_inventory_dir(self, tmp_path: Path) -> None:
        """YAML plugin configs under inventory/ are discovered.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbooks = tmp_path / "playbooks"
        playbooks.mkdir()
        playbook = playbooks / "site.yml"
        playbook.write_text("- hosts: all\n")
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        inv = inv_dir / "constructed.yml"
        inv.write_text("plugin: ansible.builtin.constructed\n")
        files = list(_find_inventory_files(str(playbook)))
        assert inv in files

    def test_finds_inventory_yml(self, tmp_path: Path) -> None:
        """inventory.yml next to the playbook is discovered.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        inv = tmp_path / "inventory.yml"
        inv.write_text("plugin: ansible.builtin.constructed\n")
        files = list(_find_inventory_files(str(playbook)))
        assert inv in files

    def test_ignores_ini_inventory(self, tmp_path: Path) -> None:
        """INI inventory cannot express plugin args and is skipped.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "hosts.ini").write_text("[web]\nhost1\n")
        files = list(_find_inventory_files(str(playbook)))
        assert files == []

    def test_finds_extensionless_inventory_in_subdir(self, tmp_path: Path) -> None:
        """Extensionless YAML inventory under inventory/ is discovered.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        inv = inv_dir / "hosts"
        inv.write_text("plugin: ansible.builtin.constructed\n")
        files = list(_find_inventory_files(str(playbook)))
        assert inv in files

    def test_unreadable_inventory_subdir_skipped(self, tmp_path: Path) -> None:
        """Unreadable inventory/ dirs are skipped; other subdirs still scanned.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        bad_inv = tmp_path / "inventory"
        bad_inv.mkdir()
        (bad_inv / "locked.yml").write_text("plugin: constructed\n")
        good_inv = tmp_path / "inventories"
        good_inv.mkdir()
        good_file = good_inv / "constructed.yml"
        good_file.write_text("plugin: constructed\n")

        real_iterdir = Path.iterdir

        def _iterdir_maybe_fail(self: Path) -> object:
            if self == bad_inv:
                raise OSError(13, "Permission denied")
            return real_iterdir(self)

        with patch.object(Path, "iterdir", _iterdir_maybe_fail):
            files = list(_find_inventory_files(str(playbook)))
        assert good_file in files
        assert bad_inv / "locked.yml" not in files


class TestTopLevelLine:
    """Tests for violation line location."""

    def test_locates_key(self) -> None:
        """Top-level key line is reported, comments ignored."""
        content = "# disable_lookups: true\nplugin: constructed\ndisable_lookups: true\n"
        assert _top_level_disable_lookups_line(content) == 3

    def test_missing_returns_none(self) -> None:
        """No key yields None."""
        assert _top_level_disable_lookups_line("plugin: constructed\n") is None


class TestDisableLookupsNoEffectGraphRule:
    """Tests for the M047 GraphRule."""

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

    def test_match_playbook_only(self, tmp_path: Path) -> None:
        """Rule matches PLAYBOOK nodes and nothing else.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        assert rule.match(g, pb_id)
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path=str(playbook),
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        assert not rule.match(g, task.node_id)

    def test_violation_on_disable_lookups(self, tmp_path: Path) -> None:
        """Top-level disable_lookups in YAML inventory fires M047.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: ansible.builtin.constructed\ndisable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        violations = cast(list[dict[str, object]], result.detail["violations"])
        assert len(violations) == 1

    def test_violation_in_extensionless_subdir_inventory(self, tmp_path: Path) -> None:
        """disable_lookups in an extensionless inventory/ file fires M047.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        (inv_dir / "hosts").write_text("plugin: ansible.builtin.constructed\ndisable_lookups: true\n")
        (inv_dir / "unrelated.ini").write_text("[web]\nhost1\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        violations = cast(list[dict[str, object]], result.detail["violations"])
        assert len(violations) == 1

    def test_pass_without_key(self, tmp_path: Path) -> None:
        """Inventory without the key is clean.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: ansible.builtin.constructed\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_pass_without_inventory(self, tmp_path: Path) -> None:
        """No inventory files nearby is clean.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_nested_key_ignored(self, tmp_path: Path) -> None:
        """A nested disable_lookups (e.g. group vars) is not the plugin arg.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("all:\n  vars:\n    disable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_quoted_key_reports_quoted_line(self, tmp_path: Path) -> None:
        """A quoted top-level key fires with its own line number.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text('plugin: constructed\n"disable_lookups": true\n')
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        violations = cast(list[dict[str, object]], result.detail["violations"])
        assert violations[0]["line"] == 2

    def test_commented_key_is_clean(self, tmp_path: Path) -> None:
        """A commented-out key does not fire.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: constructed\n# disable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_invalid_yaml_skipped(self, tmp_path: Path) -> None:
        """Unparseable inventory is skipped, not fatal.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: [unclosed\n  bad: : :\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_explicit_false_still_flagged(self, tmp_path: Path) -> None:
        """The key is a no-op at any value, including false.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: constructed\ndisable_lookups: false\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True

    def test_extensionless_inventory_scanned(self, tmp_path: Path) -> None:
        """A bare `inventory` YAML file is scanned.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory").write_text("plugin: constructed\ndisable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True

    def test_inventory_group_named_disable_lookups_is_clean(self, tmp_path: Path) -> None:
        """Static inventory group named disable_lookups is not a plugin arg.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("disable_lookups:\n  hosts:\n    host1:\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_constructed_inventory_subdir_fires(self, tmp_path: Path) -> None:
        """Plugin config under inventory/constructed.yml fires M047.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbooks = tmp_path / "playbooks"
        playbooks.mkdir()
        playbook = playbooks / "site.yml"
        playbook.write_text("- hosts: all\n")
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        (inv_dir / "constructed.yml").write_text("plugin: ansible.builtin.constructed\ndisable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True

    def test_shared_inventory_deduped_across_playbook_dirs(self, tmp_path: Path) -> None:
        """The same root inventory file is reported only once per scan.

        Args:
            tmp_path: Pytest temporary directory.
        """
        playbooks = tmp_path / "playbooks"
        plays = tmp_path / "plays"
        playbooks.mkdir()
        plays.mkdir()
        (playbooks / "a.yml").write_text("- hosts: all\n")
        (plays / "b.yml").write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: constructed\ndisable_lookups: true\n")
        g = ContentGraph()
        ids: list[str] = []
        for pb_path in (playbooks / "a.yml", plays / "b.yml"):
            pb = ContentNode(
                identity=NodeIdentity(path=str(pb_path), node_type=NodeType.PLAYBOOK),
                file_path=str(pb_path),
                scope=NodeScope.OWNED,
            )
            g.add_node(pb)
            ids.append(pb.node_id)
        rule = DisableLookupsNoEffectGraphRule()
        first_result = rule.process(g, ids[0])
        second_result = rule.process(g, ids[1])
        assert first_result is not None and first_result.verdict is True
        assert second_result is not None and second_result.verdict is False

    def test_second_playbook_same_dir_skipped(self, tmp_path: Path) -> None:
        """Per-file dedupe: a second playbook sharing inventory reports nothing.

        Args:
            tmp_path: Pytest temporary directory.
        """
        from apme_engine.graph.content_graph import (
            ContentGraph,
            ContentNode,
            NodeIdentity,
            NodeScope,
            NodeType,
        )

        rule = DisableLookupsNoEffectGraphRule()
        first = tmp_path / "site.yml"
        first.write_text("- hosts: all\n")
        second = tmp_path / "other.yml"
        second.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: constructed\ndisable_lookups: true\n")
        g = ContentGraph()
        ids: list[str] = []
        for pb_path in (first, second):
            pb = ContentNode(
                identity=NodeIdentity(path=str(pb_path), node_type=NodeType.PLAYBOOK),
                file_path=str(pb_path),
                scope=NodeScope.OWNED,
            )
            g.add_node(pb)
            ids.append(pb.node_id)
        first_result = rule.process(g, ids[0])
        assert first_result is not None
        assert first_result.verdict is True
        second_result = rule.process(g, ids[1])
        assert second_result is not None
        assert second_result.verdict is False

    def test_non_constructable_plugin_with_disable_lookups_is_clean(self, tmp_path: Path) -> None:
        """Only Constructable inventory plugins are scanned for stale keys.

        Args:
            tmp_path: Pytest temporary directory.
        """
        rule = DisableLookupsNoEffectGraphRule()
        playbook = tmp_path / "site.yml"
        playbook.write_text("- hosts: all\n")
        (tmp_path / "inventory.yml").write_text("plugin: ansible.builtin.script\ndisable_lookups: true\n")
        g, pb_id = self._make_graph_with_playbook(str(playbook))
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False
