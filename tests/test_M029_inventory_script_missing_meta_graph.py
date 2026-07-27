"""Unit tests for GraphRule M029: inventory script missing _meta."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.M029_inventory_script_missing__meta_graph import (
    InventoryScriptMissingMetaGraphRule,
    _has_meta_reference,
    _looks_like_inventory_script,
)


def _make_playbook(
    *,
    file_path: str = "site.yml",
) -> tuple[ContentGraph, str]:
    """Build a minimal graph with a single PLAYBOOK node.

    Args:
        file_path: Source file path for the playbook.

    Returns:
        Tuple of (graph, playbook_node_id).
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path=file_path, node_type=NodeType.PLAYBOOK),
        file_path=file_path,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    return g, pb.node_id


class TestLooksLikeInventoryScript:
    """Tests for _looks_like_inventory_script helper."""

    def test_with_list_flag(self) -> None:
        """Script with --list detected."""
        source = 'parser.add_argument("--list", action="store_true")\n'
        assert _looks_like_inventory_script(source) is True

    def test_with_host_flag(self) -> None:
        """Script with --host detected."""
        source = 'parser.add_argument("--host")\n'
        assert _looks_like_inventory_script(source) is True

    def test_with_argparse(self) -> None:
        """Script using argparse detected."""
        source = "import argparse\nparser = argparse.ArgumentParser()\n"
        assert _looks_like_inventory_script(source) is True

    def test_regular_python(self) -> None:
        """Regular Python file not detected."""
        source = "def main():\n    print('hello')\n"
        assert _looks_like_inventory_script(source) is False


class TestHasMetaReference:
    """Tests for _has_meta_reference helper."""

    def test_with_meta(self) -> None:
        """Source containing _meta detected."""
        assert _has_meta_reference('result["_meta"] = {"hostvars": {}}') is True

    def test_without_meta(self) -> None:
        """Source without _meta not detected."""
        assert _has_meta_reference("result = {'all': {'hosts': []}}") is False


class TestInventoryScriptMissingMetaGraphRule:
    """Tests for M029 InventoryScriptMissingMetaGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InventoryScriptMissingMetaGraphRule:
        """Create a fresh rule instance.

        Returns:
            An InventoryScriptMissingMetaGraphRule.
        """
        return InventoryScriptMissingMetaGraphRule()

    def test_metadata(self, rule: InventoryScriptMissingMetaGraphRule) -> None:
        """Rule metadata is set correctly.

        Args:
            rule: Rule instance under test.
        """
        assert rule.rule_id == "M029"
        assert rule.enabled is True

    def test_match_playbook_node(self, rule: InventoryScriptMissingMetaGraphRule) -> None:
        """Matches PLAYBOOK nodes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_playbook()
        assert rule.match(g, nid) is True

    def test_no_match_task_node(self, rule: InventoryScriptMissingMetaGraphRule) -> None:
        """Does not match TASK nodes.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            module="debug",
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        assert rule.match(g, task.node_id) is False

    def test_no_inventory_dir_passes(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """No inventory directory means no violation.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_inventory_script_missing_meta_fires(
        self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path
    ) -> None:
        """Inventory script without _meta triggers violation.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        script = inv_dir / "ec2.py"
        script.write_text(
            "#!/usr/bin/env python\n"
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            'parser.add_argument("--list", action="store_true")\n'
            'parser.add_argument("--host")\n'
            "args = parser.parse_args()\n"
            "if args.list:\n"
            '    print(\'{"all": {"hosts": ["h1"]}}\')\n'
        )
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "ec2.py" in str(result.detail.get("message", ""))

    def test_inventory_script_with_meta_passes(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """Inventory script with _meta does not trigger.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        script = inv_dir / "ec2.py"
        script.write_text(
            "#!/usr/bin/env python\n"
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            'parser.add_argument("--list", action="store_true")\n'
            "args = parser.parse_args()\n"
            'result = {"all": {"hosts": ["h1"]}, "_meta": {"hostvars": {}}}\n'
            "print(json.dumps(result))\n"
        )
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_non_inventory_python_ignored(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """Python files that don't look like inventory scripts are ignored.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        script = inv_dir / "helper.py"
        script.write_text("def utility():\n    return 42\n")
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_init_py_ignored(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """__init__.py files are ignored.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        init = inv_dir / "__init__.py"
        init.write_text("")
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_inventories_dir_name(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """Also checks 'inventories/' directory name.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventories"
        inv_dir.mkdir()
        script = inv_dir / "gce.py"
        script.write_text(
            'import argparse\nparser.add_argument("--list")\nprint(\'{"webservers": {"hosts": ["w1"]}}\')\n'
        )
        pb_file = tmp_path / "site.yml"
        pb_file.write_text("---\n- hosts: all\n")
        g, nid = _make_playbook(file_path=str(pb_file))
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_dedup_same_directory(self, rule: InventoryScriptMissingMetaGraphRule, tmp_path: Path) -> None:
        """Second playbook in same directory is deduplicated.

        Args:
            rule: Rule instance under test.
            tmp_path: Pytest temp directory.
        """
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        script = inv_dir / "ec2.py"
        script.write_text('import argparse\nparser.add_argument("--list")\n')
        pb1 = tmp_path / "site.yml"
        pb1.write_text("---\n")
        pb2 = tmp_path / "deploy.yml"
        pb2.write_text("---\n")

        g = ContentGraph()
        pb1_node = ContentNode(
            identity=NodeIdentity(path=str(pb1), node_type=NodeType.PLAYBOOK),
            file_path=str(pb1),
            scope=NodeScope.OWNED,
        )
        pb2_node = ContentNode(
            identity=NodeIdentity(path=str(pb2), node_type=NodeType.PLAYBOOK),
            file_path=str(pb2),
            scope=NodeScope.OWNED,
        )
        g.add_node(pb1_node)
        g.add_node(pb2_node)

        r1 = rule.process(g, pb1_node.node_id)
        assert r1 is not None
        assert r1.verdict is True

        r2 = rule.process(g, pb2_node.node_id)
        assert r2 is not None
        assert r2.verdict is False

    def test_process_missing_node_returns_none(self, rule: InventoryScriptMissingMetaGraphRule) -> None:
        """Processing a missing node returns None.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        result = rule.process(g, "nonexistent")
        assert result is None
