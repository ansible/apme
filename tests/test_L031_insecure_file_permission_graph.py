"""Unit tests for GraphRule L031: insecure file permissions."""

from __future__ import annotations

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.L031_insecure_file_permission_graph import (
    InsecureFilePermissionGraphRule,
    _check_mode,
    _world_writable,
)


def _make_file_task(
    *,
    module: str = "ansible.builtin.file",
    module_options: YAMLDict | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook > play > task graph for a file task.

    Args:
        module: Module FQCN.
        module_options: Module arguments.
        file_path: Source file path.
        line_start: Starting line number.

    Returns:
        Tuple of (graph, task_node_id).
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path=file_path,
        line_start=line_start,
        module=module,
        module_options=module_options or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


class TestWorldWritable:
    """Tests for the _world_writable helper."""

    def test_777_is_world_writable(self) -> None:
        """Mode 0777 is world-writable."""
        assert _world_writable("0777") is True

    def test_644_is_not(self) -> None:
        """Mode 0644 is not world-writable."""
        assert _world_writable("0644") is False

    def test_755_is_not(self) -> None:
        """Mode 0755 is not world-writable."""
        assert _world_writable("0755") is False

    def test_646_is_world_writable(self) -> None:
        """Mode 0646 is world-writable (others write bit set)."""
        assert _world_writable("0646") is True

    def test_666_is_world_writable(self) -> None:
        """Mode 0666 is world-writable."""
        assert _world_writable("0666") is True

    def test_600_is_not(self) -> None:
        """Mode 0600 is not world-writable."""
        assert _world_writable("0600") is False


class TestCheckMode:
    """Tests for the _check_mode helper."""

    def test_none_is_safe(self) -> None:
        """None mode value is not insecure."""
        is_insecure, _ = _check_mode(None)
        assert is_insecure is False

    def test_string_0644_is_safe(self) -> None:
        """String '0644' is not insecure."""
        is_insecure, _ = _check_mode("0644")
        assert is_insecure is False

    def test_string_0777_is_insecure(self) -> None:
        """String '0777' is insecure."""
        is_insecure, reason = _check_mode("0777")
        assert is_insecure is True
        assert "permissive" in reason

    def test_string_0666_is_insecure(self) -> None:
        """String '0666' is insecure."""
        is_insecure, _ = _check_mode("0666")
        assert is_insecure is True

    def test_string_world_writable_not_in_set(self) -> None:
        """String '0646' is world-writable but not in known set."""
        is_insecure, reason = _check_mode("0646")
        assert is_insecure is True
        assert "world-writable" in reason

    def test_integer_mode_777(self) -> None:
        """Integer 511 (0o777) is insecure."""
        is_insecure, reason = _check_mode(0o777)
        assert is_insecure is True
        assert "permissive" in reason

    def test_integer_mode_644(self) -> None:
        """Integer 420 (0o644) is not insecure."""
        is_insecure, _ = _check_mode(0o644)
        assert is_insecure is False

    def test_boolean_mode_flagged(self) -> None:
        """Boolean mode value is flagged."""
        is_insecure, reason = _check_mode(True)
        assert is_insecure is True
        assert "boolean" in reason

    def test_templated_value_skipped(self) -> None:
        """Jinja-templated mode is skipped."""
        is_insecure, _ = _check_mode("{{ file_mode }}")
        assert is_insecure is False

    def test_non_octal_string_skipped(self) -> None:
        """Non-octal string like 'u+rwx' is skipped."""
        is_insecure, _ = _check_mode("u+rwx")
        assert is_insecure is False


class TestInsecureFilePermissionGraphRule:
    """Tests for L031 InsecureFilePermissionGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InsecureFilePermissionGraphRule:
        """Create a rule instance.

        Returns:
            An InsecureFilePermissionGraphRule.
        """
        return InsecureFilePermissionGraphRule()

    def test_metadata(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Rule metadata is set correctly.

        Args:
            rule: Rule instance under test.
        """
        assert rule.rule_id == "L031"
        assert rule.enabled is True

    def test_match_file_module_with_mode(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Matches file module task with mode parameter.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="ansible.builtin.file",
            module_options={"path": "/tmp/test", "mode": "0644"},
        )
        assert rule.match(g, nid) is True

    def test_no_match_without_mode(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Does not match when mode is absent.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="ansible.builtin.file",
            module_options={"path": "/tmp/test", "state": "directory"},
        )
        assert rule.match(g, nid) is False

    def test_no_match_non_file_module(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Does not match non-file modules.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="ansible.builtin.debug",
            module_options={"msg": "hello"},
        )
        assert rule.match(g, nid) is False

    def test_no_match_play_node(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Does not match PLAY nodes.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_insecure_0777_fires(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Mode '0777' triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "0777"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "permissive" in str(result.detail.get("message", ""))

    def test_insecure_0666_fires(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Mode '0666' triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "0666"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_world_writable_0646_fires(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Mode '0646' triggers (world-writable).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "0646"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "world-writable" in str(result.detail.get("message", ""))

    def test_safe_0644_passes(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Mode '0644' does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "0644"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_safe_0755_passes(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Mode '0755' does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "0755"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_boolean_mode_fires(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Boolean mode value triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": True},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "boolean" in str(result.detail.get("message", ""))

    def test_templated_mode_skipped(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Jinja-templated mode is skipped.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": "{{ file_mode }}"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_copy_module_works(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Works with ansible.builtin.copy module.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="ansible.builtin.copy",
            module_options={"src": "/tmp/a", "dest": "/tmp/b", "mode": "0777"},
        )
        assert rule.match(g, nid) is True
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_template_module_works(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Works with ansible.builtin.template module.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="ansible.builtin.template",
            module_options={"src": "t.j2", "dest": "/etc/conf", "mode": "0666"},
        )
        assert rule.match(g, nid) is True
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_short_module_name_works(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Works with short module names (e.g. 'file').

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module="file",
            module_options={"path": "/tmp/test", "mode": "0777"},
        )
        assert rule.match(g, nid) is True
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_handler_node_matched(self, rule: InsecureFilePermissionGraphRule) -> None:
        """HANDLER nodes are matched.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        pb = ContentNode(
            identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
            file_path="site.yml",
            scope=NodeScope.OWNED,
        )
        handler = ContentNode(
            identity=NodeIdentity(path="site.yml/handlers[0]", node_type=NodeType.HANDLER),
            file_path="site.yml",
            line_start=20,
            module="ansible.builtin.file",
            module_options={"path": "/tmp/test", "mode": "0777"},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(handler)
        g.add_edge(pb.node_id, handler.node_id, EdgeType.CONTAINS)
        assert rule.match(g, handler.node_id) is True
        result = rule.process(g, handler.node_id)
        assert result is not None
        assert result.verdict is True

    def test_process_missing_node_returns_none(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Processing a missing node returns None.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        result = rule.process(g, "nonexistent")
        assert result is None

    def test_numeric_integer_insecure(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Integer mode 0o777 (511) triggers.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": 0o777},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_numeric_integer_safe(self, rule: InsecureFilePermissionGraphRule) -> None:
        """Integer mode 0o644 (420) passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_file_task(
            module_options={"path": "/tmp/test", "mode": 0o644},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False
