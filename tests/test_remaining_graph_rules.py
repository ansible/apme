"""Unit and integration tests for Phase 2J+K GraphRules.

Covers L056, R401, and 9 collection/plugin stub rules
(L087-L090, L095-L096, L103-L105).
"""

from __future__ import annotations

import pytest

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L056_sanity_graph import SanityGraphRule
from apme_engine.validators.native.rules.L087_collection_license_graph import CollectionLicenseGraphRule
from apme_engine.validators.native.rules.L088_collection_readme_graph import CollectionReadmeGraphRule
from apme_engine.validators.native.rules.L089_plugin_type_hints_graph import PluginTypeHintsGraphRule
from apme_engine.validators.native.rules.L090_plugin_file_size_graph import PluginFileSizeGraphRule
from apme_engine.validators.native.rules.L095_schema_validation_graph import SchemaValidationGraphRule
from apme_engine.validators.native.rules.L096_meta_runtime_graph import MetaRuntimeGraphRule
from apme_engine.validators.native.rules.L103_galaxy_changelog_graph import GalaxyChangelogGraphRule
from apme_engine.validators.native.rules.L104_galaxy_runtime_graph import GalaxyRuntimeGraphRule
from apme_engine.validators.native.rules.L105_galaxy_repository_graph import GalaxyRepositoryGraphRule
from apme_engine.validators.native.rules.R401_list_all_inbound_src_graph import ListAllInboundSrcGraphRule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    module: str = "debug",
    resolved_module: str = "",
    module_options: YAMLDict | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Declared module name.
        resolved_module: Resolved FQCN.
        module_options: Module argument mapping.
        file_path: Source file path.
        line_start: Starting line number.
        path: YAML path identity.

    Returns:
        Tuple of ``(graph, task_node_id)``.
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
        identity=NodeIdentity(path=path, node_type=NodeType.TASK),
        file_path=file_path,
        line_start=line_start,
        module=module,
        resolved_module_name=resolved_module,
        module_options=module_options or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


def _playbook_node_id(g: ContentGraph) -> str:
    """Return the node_id of the first PLAYBOOK node.

    Args:
        g: ContentGraph to search.

    Returns:
        Node ID string.

    Raises:
        ValueError: If no PLAYBOOK node exists in the graph.
    """
    for node in g.nodes(NodeType.PLAYBOOK):
        return node.node_id
    raise ValueError("No PLAYBOOK node found")


# ===========================================================================
# L056 — Sanity
# ===========================================================================


class TestL056SanityGraphRule:
    """Tests for L056 SanityGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> SanityGraphRule:
        """Create a rule instance.

        Returns:
            A SanityGraphRule.
        """
        return SanityGraphRule()

    def test_git_path_triggers(self, rule: SanityGraphRule) -> None:
        """File path containing ``.git/`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="project/.git/hooks/pre-commit")
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["path"] == "project/.git/hooks/pre-commit"

    def test_pycache_path_triggers(self, rule: SanityGraphRule) -> None:
        """File path containing ``__pycache__`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="roles/myrole/__pycache__/module.cpython-311.pyc")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_normal_path_passes(self, rule: SanityGraphRule) -> None:
        """Normal playbook path does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="playbooks/site.yml")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_ansible_dir_triggers(self, rule: SanityGraphRule) -> None:
        """Path containing ``/.ansible/`` triggers.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="/home/user/.ansible/tmp/task.yml")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_role_node_matches(self, rule: SanityGraphRule) -> None:
        """ROLE nodes are matched by this rule.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        role = ContentNode(
            identity=NodeIdentity(path="roles/test", node_type=NodeType.ROLE),
            file_path="roles/test",
            scope=NodeScope.OWNED,
        )
        g.add_node(role)
        assert rule.match(g, role.node_id)

    def test_play_node_not_matched(self, rule: SanityGraphRule) -> None:
        """PLAY nodes are not matched.

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
        assert not rule.match(g, play.node_id)


# ===========================================================================
# R401 — ListAllInboundSrc
# ===========================================================================


class TestR401ListAllInboundSrcGraphRule:
    """Tests for R401 ListAllInboundSrcGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ListAllInboundSrcGraphRule:
        """Create a rule instance.

        Returns:
            A ListAllInboundSrcGraphRule.
        """
        return ListAllInboundSrcGraphRule()

    def test_collects_inbound_sources(self, rule: ListAllInboundSrcGraphRule) -> None:
        """Playbook with inbound tasks collects their source URLs.

        Args:
            rule: Rule instance under test.
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
        t1 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=5,
            module="get_url",
            resolved_module_name="ansible.builtin.get_url",
            module_options={"url": "https://example.com/a.tar.gz", "dest": "/tmp/"},
            scope=NodeScope.OWNED,
        )
        t2 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="git",
            resolved_module_name="ansible.builtin.git",
            module_options={"repo": "https://github.com/org/repo.git", "dest": "/opt/code"},
            scope=NodeScope.OWNED,
        )
        t3 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[2]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=15,
            module="debug",
            resolved_module_name="ansible.builtin.debug",
            module_options={"msg": "hello"},
            scope=NodeScope.OWNED,
        )
        g.add_node(pb)
        g.add_node(play)
        g.add_node(t1)
        g.add_node(t2)
        g.add_node(t3)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, t3.node_id, EdgeType.CONTAINS)

        pb_id = pb.node_id
        assert rule.match(g, pb_id)
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        src_list = result.detail["inbound_src"]
        assert isinstance(src_list, list)
        assert len(src_list) == 2
        assert "https://example.com/a.tar.gz" in src_list
        assert "https://github.com/org/repo.git" in src_list

    def test_no_inbound_passes(self, rule: ListAllInboundSrcGraphRule) -> None:
        """Playbook with no inbound tasks does not trigger.

        Args:
            rule: Rule instance under test.
        """
        g, _ = _make_task(module="debug", resolved_module="ansible.builtin.debug")
        pb_id = _playbook_node_id(g)
        assert rule.match(g, pb_id)
        result = rule.process(g, pb_id)
        assert result is not None
        assert result.verdict is False

    def test_task_not_matched(self, rule: ListAllInboundSrcGraphRule) -> None:
        """TASK nodes are not matched (only PLAYBOOK).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(module="get_url", resolved_module="ansible.builtin.get_url")
        assert not rule.match(g, nid)


# ===========================================================================
# Collection/plugin stub rules — match always False
# ===========================================================================


_STUB_RULES: list[tuple[str, type[GraphRule]]] = [
    ("L087", CollectionLicenseGraphRule),
    ("L088", CollectionReadmeGraphRule),
    ("L089", PluginTypeHintsGraphRule),
    ("L090", PluginFileSizeGraphRule),
    ("L095", SchemaValidationGraphRule),
    ("L096", MetaRuntimeGraphRule),
    ("L103", GalaxyChangelogGraphRule),
    ("L104", GalaxyRuntimeGraphRule),
    ("L105", GalaxyRepositoryGraphRule),
]


class TestCollectionPluginStubRules:
    """Verify all collection/plugin stub rules have match=False."""

    @pytest.mark.parametrize("rule_id,rule_cls", _STUB_RULES)  # type: ignore[untyped-decorator]
    def test_match_always_false(self, rule_id: str, rule_cls: type[GraphRule]) -> None:
        """Stub rule ``match()`` always returns False for any node type.

        Args:
            rule_id: Rule identifier string.
            rule_cls: GraphRule subclass to instantiate.
        """
        rule = rule_cls()
        assert rule.rule_id == rule_id

        g, task_nid = _make_task()
        assert not rule.match(g, task_nid)

        pb_id = _playbook_node_id(g)
        assert not rule.match(g, pb_id)

    @pytest.mark.parametrize("rule_id,rule_cls", _STUB_RULES)  # type: ignore[untyped-decorator]
    def test_process_returns_none(self, rule_id: str, rule_cls: type[GraphRule]) -> None:
        """Stub rule ``process()`` returns None.

        Args:
            rule_id: Rule identifier string.
            rule_cls: GraphRule subclass to instantiate.
        """
        rule = rule_cls()
        g, task_nid = _make_task()
        assert rule.process(g, task_nid) is None

    @pytest.mark.parametrize("rule_id,rule_cls", _STUB_RULES)  # type: ignore[untyped-decorator]
    def test_scanner_produces_no_violations(self, rule_id: str, rule_cls: type[GraphRule]) -> None:
        """Scanner produces zero violations for stub rules.

        Args:
            rule_id: Rule identifier string.
            rule_cls: GraphRule subclass to instantiate.
        """
        rule = rule_cls()
        g, _ = _make_task()
        report = scan(g, [rule])
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 0


# ===========================================================================
# Scanner integration tests
# ===========================================================================


class TestPhase2JKScanner:
    """Integration tests for L056 and R401 through the graph scanner."""

    def test_l056_via_scanner(self) -> None:
        """L056 fires for ``.git/`` path through scanner."""
        g, _ = _make_task(file_path="project/.git/config")
        report = scan(g, [SanityGraphRule()])
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1

    def test_r401_via_scanner(self) -> None:
        """R401 fires for playbook with inbound tasks."""
        g, _ = _make_task(
            module="get_url",
            resolved_module="ansible.builtin.get_url",
            module_options={"url": "https://example.com/x", "dest": "/tmp/"},
        )
        report = scan(g, [ListAllInboundSrcGraphRule()])
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
