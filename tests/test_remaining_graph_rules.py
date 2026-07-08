"""Unit and integration tests for Phase 2J+K GraphRules.

Covers L056, R401, collection metadata rules (L087, L088, L096, L103–L105),
and plugin/schema rules (L089, L090, L095).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.engine.graph_builder import GraphBuilder
from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rules.L056_sanity_graph import SanityGraphRule
from apme_engine.graph.rules.L074_no_dashes_in_role_name_graph import NoDashesInRoleNameGraphRule
from apme_engine.graph.rules.L080_internal_var_prefix_graph import InternalVarPrefixGraphRule
from apme_engine.graph.rules.L081_numbered_names_graph import NumberedNamesGraphRule
from apme_engine.graph.rules.L083_hardcoded_group_graph import HardcodedGroupGraphRule
from apme_engine.graph.rules.L085_role_path_include_graph import RolePathIncludeGraphRule
from apme_engine.graph.rules.L087_collection_license_graph import CollectionLicenseGraphRule
from apme_engine.graph.rules.L088_collection_readme_graph import CollectionReadmeGraphRule
from apme_engine.graph.rules.L089_plugin_type_hints_graph import PluginTypeHintsGraphRule
from apme_engine.graph.rules.L090_plugin_file_size_graph import PluginFileSizeGraphRule
from apme_engine.graph.rules.L095_schema_validation_graph import SchemaValidationGraphRule
from apme_engine.graph.rules.L096_meta_runtime_graph import MetaRuntimeGraphRule
from apme_engine.graph.rules.L103_galaxy_changelog_graph import GalaxyChangelogGraphRule
from apme_engine.graph.rules.L104_galaxy_runtime_graph import GalaxyRuntimeGraphRule
from apme_engine.graph.rules.L105_galaxy_repository_graph import GalaxyRepositoryGraphRule
from apme_engine.graph.rules.M030_broken_conditional_expressions_graph import (
    HAS_JINJA,
    BrokenConditionalExpressionsGraphRule,
)
from apme_engine.graph.rules.R401_list_all_inbound_src_graph import ListAllInboundSrcGraphRule
from apme_engine.graph.scanner import scan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Module name as authored in YAML (short or FQCN).
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
            module="ansible.builtin.get_url",
            module_options={"url": "https://example.com/a.tar.gz", "dest": "/tmp/"},
            scope=NodeScope.OWNED,
        )
        t2 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="ansible.builtin.git",
            module_options={"repo": "https://github.com/org/repo.git", "dest": "/opt/code"},
            scope=NodeScope.OWNED,
        )
        t3 = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[2]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=15,
            module="ansible.builtin.debug",
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
        g, _ = _make_task(module="ansible.builtin.debug")
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
        g, nid = _make_task(module="ansible.builtin.get_url")
        assert not rule.match(g, nid)


def _make_collection(
    *,
    path: str = "mycollection/galaxy.yml",
    file_path: str = "mycollection/galaxy.yml",
    line_start: int = 1,
    collection_files: list[str] | None = None,
    collection_metadata: YAMLDict | None = None,
    collection_meta_runtime: YAMLDict | None = None,
) -> tuple[ContentGraph, str]:
    """Build a graph with a single owned COLLECTION node.

    Args:
        path: Node identity path.
        file_path: galaxy.yml path for location metadata.
        line_start: Starting line in ``file_path``.
        collection_files: Relative paths inside the collection.
        collection_metadata: Parsed galaxy.yml mapping.
        collection_meta_runtime: Parsed meta/runtime.yml mapping.

    Returns:
        Tuple of ``(graph, collection_node_id)``.
    """
    g = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.COLLECTION),
        file_path=file_path,
        line_start=line_start,
        collection_files=list(collection_files or []),
        collection_metadata=dict(collection_metadata or {}),
        collection_meta_runtime=dict(collection_meta_runtime or {}),
        scope=NodeScope.OWNED,
    )
    g.add_node(node)
    return g, node.node_id


# ===========================================================================
# Collection graph rules (L087, L088, L096, L103–L105)
# ===========================================================================


class TestCollectionLicenseGraphRule:
    """Tests for L087 CollectionLicenseGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> CollectionLicenseGraphRule:
        """Create a rule instance.

        Returns:
            A CollectionLicenseGraphRule.
        """
        return CollectionLicenseGraphRule()

    def test_match_requires_collection_with_files(self, rule: CollectionLicenseGraphRule) -> None:
        """Match only when ``collection_files`` is non-empty.

        Args:
            rule: Rule instance under test.
        """
        g_empty, nid_empty = _make_collection(collection_files=[])
        assert not rule.match(g_empty, nid_empty)

        g_ok, nid_ok = _make_collection(collection_files=["README.md"])
        assert rule.match(g_ok, nid_ok)

    def test_violation_without_license(self, rule: CollectionLicenseGraphRule) -> None:
        """No LICENSE* or COPYING* basename → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["foo.txt", "roles/x/tasks/main.yml"])
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None

    def test_pass_with_license(self, rule: CollectionLicenseGraphRule) -> None:
        """Root-level LICENSE or COPYING passes (case-insensitive).

        Args:
            rule: Rule instance under test.
        """
        for name in ("LICENSE", "license.md", "COPYING", "copying.txt"):
            g, nid = _make_collection(collection_files=[name])
            result = rule.process(g, nid)
            assert result is not None
            assert result.verdict is False

    def test_nested_license_does_not_satisfy(self, rule: CollectionLicenseGraphRule) -> None:
        """``docs/LICENSE`` should not satisfy the root-level requirement.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["docs/LICENSE", "galaxy.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_task_node_process_none(self, rule: CollectionLicenseGraphRule) -> None:
        """``process`` on non-collection node returns None.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        assert rule.process(g, nid) is None


class TestCollectionReadmeGraphRule:
    """Tests for L088 CollectionReadmeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> CollectionReadmeGraphRule:
        """Create a rule instance.

        Returns:
            A CollectionReadmeGraphRule.
        """
        return CollectionReadmeGraphRule()

    def test_violation_without_readme(self, rule: CollectionReadmeGraphRule) -> None:
        """No README* → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["galaxy.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_with_readme(self, rule: CollectionReadmeGraphRule) -> None:
        """Root-level README* passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["docs/x.txt", "README.md"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_nested_readme_does_not_satisfy(self, rule: CollectionReadmeGraphRule) -> None:
        """``docs/README.md`` should not satisfy the root-level requirement.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["docs/README.md", "galaxy.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


class TestMetaRuntimeGraphRule:
    """Tests for L096 MetaRuntimeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> MetaRuntimeGraphRule:
        """Create a rule instance.

        Returns:
            A MetaRuntimeGraphRule.
        """
        return MetaRuntimeGraphRule()

    def test_match_requires_runtime_dict(self, rule: MetaRuntimeGraphRule) -> None:
        """Match when ``collection_meta_runtime`` is non-empty.

        Args:
            rule: Rule instance under test.
        """
        g0, n0 = _make_collection(collection_meta_runtime={})
        assert not rule.match(g0, n0)
        g1, n1 = _make_collection(collection_meta_runtime={"foo": 1})
        assert rule.match(g1, n1)

    def test_violation_missing_requires_ansible(self, rule: MetaRuntimeGraphRule) -> None:
        """Missing ``requires_ansible`` key → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_meta_runtime={"collections": {}})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_with_requires_ansible(self, rule: MetaRuntimeGraphRule) -> None:
        """Present ``requires_ansible`` → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_meta_runtime={"requires_ansible": ">=2.14"})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False


class TestGalaxyChangelogGraphRule:
    """Tests for L103 GalaxyChangelogGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> GalaxyChangelogGraphRule:
        """Create a rule instance.

        Returns:
            A GalaxyChangelogGraphRule.
        """
        return GalaxyChangelogGraphRule()

    def test_violation_without_changelog(self, rule: GalaxyChangelogGraphRule) -> None:
        """No CHANGELOG* → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["galaxy.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_with_changelog(self, rule: GalaxyChangelogGraphRule) -> None:
        """Root-level CHANGELOG.rst passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["CHANGELOG.rst"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_nested_changelog_does_not_satisfy(self, rule: GalaxyChangelogGraphRule) -> None:
        """``docs/CHANGELOG.md`` should not satisfy the root-level requirement.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["docs/CHANGELOG.md", "galaxy.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


class TestGalaxyRuntimeGraphRule:
    """Tests for L104 GalaxyRuntimeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> GalaxyRuntimeGraphRule:
        """Create a rule instance.

        Returns:
            A GalaxyRuntimeGraphRule.
        """
        return GalaxyRuntimeGraphRule()

    def test_pass_meta_runtime_yml_path(self, rule: GalaxyRuntimeGraphRule) -> None:
        """Listed ``meta/runtime.yml`` passes.

        Args:
            rule: Rule instance under test.
        """
        for entry in ("meta/runtime.yml", "meta/runtime.yaml", r"meta\runtime.yml"):
            g, nid = _make_collection(collection_files=[entry])
            result = rule.process(g, nid)
            assert result is not None
            assert result.verdict is False, entry

    def test_nested_meta_runtime_does_not_satisfy(self, rule: GalaxyRuntimeGraphRule) -> None:
        """Nested ``vendor/ns/col/meta/runtime.yml`` is not the collection's own.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["vendor/ns/col/meta/runtime.yml"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_violation_missing_runtime(self, rule: GalaxyRuntimeGraphRule) -> None:
        """No runtime file → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_files=["galaxy.yml", "plugins/modules/x.py"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


class TestGalaxyRepositoryGraphRule:
    """Tests for L105 GalaxyRepositoryGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> GalaxyRepositoryGraphRule:
        """Create a rule instance.

        Returns:
            A GalaxyRepositoryGraphRule.
        """
        return GalaxyRepositoryGraphRule()

    def test_match_requires_metadata(self, rule: GalaxyRepositoryGraphRule) -> None:
        """Match when ``collection_metadata`` is non-empty.

        Args:
            rule: Rule instance under test.
        """
        g0, n0 = _make_collection(collection_metadata={})
        assert not rule.match(g0, n0)
        g1, n1 = _make_collection(collection_metadata={"namespace": "x"})
        assert rule.match(g1, n1)

    def test_violation_missing_repository(self, rule: GalaxyRepositoryGraphRule) -> None:
        """No repository key → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_metadata={"namespace": "ns", "name": "n"})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_violation_empty_repository(self, rule: GalaxyRepositoryGraphRule) -> None:
        """Empty string repository → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(collection_metadata={"repository": "  "})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_with_repository(self, rule: GalaxyRepositoryGraphRule) -> None:
        """Non-empty repository (flat galaxy.yml) → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={"repository": "https://github.com/org/repo"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_manifest_json_repository(self, rule: GalaxyRepositoryGraphRule) -> None:
        """Non-empty repository inside MANIFEST.json ``collection_info`` → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={
                "collection_info": {
                    "namespace": "ns",
                    "name": "col",
                    "repository": "https://github.com/ns/col",
                },
            },
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_violation_manifest_json_missing_repository(self, rule: GalaxyRepositoryGraphRule) -> None:
        """MANIFEST.json ``collection_info`` without ``repository`` → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={
                "collection_info": {"namespace": "ns", "name": "col"},
            },
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True


# ===========================================================================
# Helpers — MODULE nodes
# ===========================================================================


def _make_module(
    *,
    path: str = "plugins/modules/my_module.py",
    file_path: str = "plugins/modules/my_module.py",
    name: str = "ns.col.my_module",
    module_line_count: int = 100,
    module_functions_without_return_type: list[str] | None = None,
) -> tuple[ContentGraph, str]:
    """Build a graph with a single owned MODULE node.

    Args:
        path: Node identity path.
        file_path: Source file path.
        name: Module FQCN.
        module_line_count: Line count of the plugin file.
        module_functions_without_return_type: Functions missing return types.

    Returns:
        Tuple of ``(graph, module_node_id)``.
    """
    g = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.MODULE),
        file_path=file_path,
        name=name,
        module_line_count=module_line_count,
        module_functions_without_return_type=list(module_functions_without_return_type or []),
        scope=NodeScope.OWNED,
    )
    g.add_node(node)
    return g, node.node_id


# ===========================================================================
# L089 — PluginTypeHints
# ===========================================================================


class TestPluginTypeHintsGraphRule:
    """Tests for L089 PluginTypeHintsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> PluginTypeHintsGraphRule:
        """Create a rule instance.

        Returns:
            A PluginTypeHintsGraphRule.
        """
        return PluginTypeHintsGraphRule()

    def test_match_requires_module_with_missing_hints(self, rule: PluginTypeHintsGraphRule) -> None:
        """Match only MODULE nodes with functions missing return types.

        Args:
            rule: Rule instance under test.
        """
        g_ok, nid_ok = _make_module(module_functions_without_return_type=[])
        assert not rule.match(g_ok, nid_ok)

        g_bad, nid_bad = _make_module(module_functions_without_return_type=["run"])
        assert rule.match(g_bad, nid_bad)

    def test_task_not_matched(self, rule: PluginTypeHintsGraphRule) -> None:
        """TASK nodes are not matched.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        assert not rule.match(g, nid)

    def test_violation_missing_hints(self, rule: PluginTypeHintsGraphRule) -> None:
        """Functions without return type hints → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_module(module_functions_without_return_type=["run", "execute"])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        funcs = result.detail["functions"]
        assert isinstance(funcs, list)
        assert "run" in funcs
        assert "execute" in funcs

    def test_pass_all_hints_present(self, rule: PluginTypeHintsGraphRule) -> None:
        """All functions have return type hints → pass (verdict False).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_module(module_functions_without_return_type=[])
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False


# ===========================================================================
# L090 — PluginFileSize
# ===========================================================================


class TestPluginFileSizeGraphRule:
    """Tests for L090 PluginFileSizeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> PluginFileSizeGraphRule:
        """Create a rule instance.

        Returns:
            A PluginFileSizeGraphRule.
        """
        return PluginFileSizeGraphRule()

    def test_match_requires_module_with_lines(self, rule: PluginFileSizeGraphRule) -> None:
        """Match only MODULE nodes with positive line count.

        Args:
            rule: Rule instance under test.
        """
        g0, nid0 = _make_module(module_line_count=0)
        assert not rule.match(g0, nid0)

        g1, nid1 = _make_module(module_line_count=100)
        assert rule.match(g1, nid1)

    def test_violation_large_file(self, rule: PluginFileSizeGraphRule) -> None:
        """File exceeding 500 lines → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_module(module_line_count=750)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["line_count"] == 750

    def test_pass_small_file(self, rule: PluginFileSizeGraphRule) -> None:
        """File under 500 lines → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_module(module_line_count=200)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_at_threshold(self, rule: PluginFileSizeGraphRule) -> None:
        """File at exactly 500 lines → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_module(module_line_count=500)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_task_not_matched(self, rule: PluginFileSizeGraphRule) -> None:
        """TASK nodes are not matched.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        assert not rule.match(g, nid)


# ===========================================================================
# L095 — SchemaValidation
# ===========================================================================


def _make_play(
    *,
    options: YAMLDict | None = None,
    file_path: str = "site.yml",
    line_start: int = 1,
) -> tuple[ContentGraph, str]:
    """Build a graph with a PLAYBOOK → PLAY, returning the play node id.

    Args:
        options: Play options dict.
        file_path: Source file path.
        line_start: Starting line number.

    Returns:
        Tuple of ``(graph, play_node_id)``.
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity(path=file_path, node_type=NodeType.PLAYBOOK),
        file_path=file_path,
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity(path=f"{file_path}/plays[0]", node_type=NodeType.PLAY),
        file_path=file_path,
        line_start=line_start,
        options=dict(options or {}),
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    return g, play.node_id


class TestSchemaValidationGraphRule:
    """Tests for L095 SchemaValidationGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> SchemaValidationGraphRule:
        """Create a rule instance.

        Returns:
            A SchemaValidationGraphRule.
        """
        return SchemaValidationGraphRule()

    def test_match_play_with_options(self, rule: SchemaValidationGraphRule) -> None:
        """Match PLAY nodes with non-empty options.

        Args:
            rule: Rule instance under test.
        """
        g0, nid0 = _make_play(options={})
        assert not rule.match(g0, nid0)

        g1, nid1 = _make_play(options={"hosts": "all"})
        assert rule.match(g1, nid1)

    def test_match_collection_with_metadata(self, rule: SchemaValidationGraphRule) -> None:
        """Match COLLECTION nodes with non-empty metadata.

        Args:
            rule: Rule instance under test.
        """
        g0, nid0 = _make_collection(collection_metadata={})
        assert not rule.match(g0, nid0)

        g1, nid1 = _make_collection(collection_metadata={"namespace": "ns"})
        assert rule.match(g1, nid1)

    def test_task_not_matched(self, rule: SchemaValidationGraphRule) -> None:
        """TASK nodes are not matched.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        assert not rule.match(g, nid)

    def test_play_pass_known_keywords(self, rule: SchemaValidationGraphRule) -> None:
        """All recognized play keywords → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_play(options={"hosts": "all", "gather_facts": False, "serial": 1})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_play_violation_unknown_key(self, rule: SchemaValidationGraphRule) -> None:
        """Unknown play keyword → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_play(options={"hosts": "all", "bogus_key": True})
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        unknown = result.detail["unknown_keys"]
        assert isinstance(unknown, list)
        assert "bogus_key" in unknown

    def test_collection_pass_all_required(self, rule: SchemaValidationGraphRule) -> None:
        """galaxy.yml with all required keys → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={"namespace": "ns", "name": "col", "version": "1.0.0"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_collection_violation_missing_namespace(self, rule: SchemaValidationGraphRule) -> None:
        """galaxy.yml missing namespace → violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={"name": "col", "version": "1.0.0"},
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        missing = result.detail["missing_keys"]
        assert isinstance(missing, list)
        assert "namespace" in missing

    def test_collection_manifest_json(self, rule: SchemaValidationGraphRule) -> None:
        """MANIFEST.json with all required keys under collection_info → pass.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_collection(
            collection_metadata={
                "collection_info": {"namespace": "ns", "name": "col", "version": "1.0.0"},
            },
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False


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
            module="ansible.builtin.get_url",
            module_options={"url": "https://example.com/x", "dest": "/tmp/"},
        )
        report = scan(g, [ListAllInboundSrcGraphRule()])
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1


# ===========================================================================
# Source-tree collection loader → graph rule integration tests
# ===========================================================================


def _build_graph_from_collection(col_root: Path) -> ContentGraph:
    """Load a collection via model_loader and build a ContentGraph.

    Args:
        col_root: Path to the collection root on disk.

    Returns:
        ContentGraph with the collection node populated from disk.
    """
    from apme_engine.engine.model_loader import load_collection

    coll = load_collection(str(col_root), basedir=str(col_root.parent.parent), load_children=False)
    defs: dict[str, object] = {
        "root": {
            "definitions": {
                "collections": [coll],
            },
        },
    }
    builder = GraphBuilder(defs, {})
    return builder.build()


def _make_source_collection(tmp_path: Path, *, with_license: bool = True, with_changelog: bool = True) -> Path:
    """Create a minimal source-tree collection layout.

    Args:
        tmp_path: Pytest temporary directory fixture.
        with_license: Whether to include a LICENSE file.
        with_changelog: Whether to include a CHANGELOG file.

    Returns:
        Path to the collection root directory.
    """
    col_root = tmp_path / "testns" / "testcol"
    col_root.mkdir(parents=True)
    (col_root / "galaxy.yml").write_text(
        "namespace: testns\nname: testcol\nversion: 1.0.0\nrepository: https://github.com/example/testcol\n"
    )
    (col_root / "README.md").write_text("# Test Collection\n")
    if with_license:
        (col_root / "LICENSE").write_text("MIT\n")
    if with_changelog:
        (col_root / "CHANGELOG.md").write_text("# Changelog\n")
    (col_root / "meta").mkdir()
    (col_root / "meta" / "runtime.yml").write_text("requires_ansible: '>=2.14'\n")
    return col_root


class TestSourceTreeCollectionRules:
    """Verify collection rules fire on graphs built from source-tree collections."""

    def test_l087_fires_without_license(self, tmp_path: Path) -> None:
        """L087 fires when LICENSE is missing from a source-tree collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path, with_license=False)
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [CollectionLicenseGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations, "L087 should fire when LICENSE is missing"

    def test_l087_passes_with_license(self, tmp_path: Path) -> None:
        """L087 passes when LICENSE exists in a source-tree collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path, with_license=True)
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [CollectionLicenseGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert not violations, "L087 should pass when LICENSE is present"

    def test_l088_fires_without_readme(self, tmp_path: Path) -> None:
        """L088 fires when README is missing from a source-tree collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        (col_root / "README.md").unlink()
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [CollectionReadmeGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations, "L088 should fire when README is missing"

    def test_l103_fires_without_changelog(self, tmp_path: Path) -> None:
        """L103 fires when CHANGELOG is missing from a source-tree collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path, with_changelog=False)
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [GalaxyChangelogGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations, "L103 should fire when CHANGELOG is missing"

    def test_l103_passes_with_changelog(self, tmp_path: Path) -> None:
        """L103 passes when CHANGELOG exists in a source-tree collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path, with_changelog=True)
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [GalaxyChangelogGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert not violations, "L103 should pass when CHANGELOG is present"

    def test_l105_passes_with_repository(self, tmp_path: Path) -> None:
        """L105 passes when galaxy.yml has a repository key.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [GalaxyRepositoryGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert not violations, "L105 should pass when repository key is present"

    def test_l105_fires_without_repository(self, tmp_path: Path) -> None:
        """L105 fires when galaxy.yml lacks a repository key.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        (col_root / "galaxy.yml").write_text("namespace: testns\nname: testcol\nversion: 1.0.0\n")
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [GalaxyRepositoryGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations, "L105 should fire when repository key is missing"

    def test_l095_collection_schema(self, tmp_path: Path) -> None:
        """L095 validates required galaxy.yml keys from source-tree metadata.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_root = _make_source_collection(tmp_path)
        (col_root / "galaxy.yml").write_text("name: testcol\nversion: 1.0.0\n")
        graph = _build_graph_from_collection(col_root)
        report = scan(graph, [SchemaValidationGraphRule()], owned_only=False)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert violations, "L095 should fire when namespace is missing from galaxy.yml"


# ===========================================================================
# L074 — NoDashesInRoleName
# ===========================================================================


def _make_role(
    *,
    name: str = "my_role",
    role_fqcn: str = "",
    file_path: str = "roles/my_role",
) -> tuple[ContentGraph, str]:
    """Build a graph with a single ROLE node.

    Args:
        name: Role display name.
        role_fqcn: Fully-qualified collection name for the role.
        file_path: File path for location metadata.

    Returns:
        Tuple of ``(graph, role_node_id)``.
    """
    g = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path=file_path, node_type=NodeType.ROLE),
        file_path=file_path,
        name=name,
        role_fqcn=role_fqcn,
        scope=NodeScope.OWNED,
    )
    g.add_node(node)
    return g, node.node_id


class TestNoDashesInRoleNameGraphRule:
    """Tests for L074 NoDashesInRoleNameGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoDashesInRoleNameGraphRule:
        """Create a rule instance.

        Returns:
            A NoDashesInRoleNameGraphRule.
        """
        return NoDashesInRoleNameGraphRule()

    def test_violation_dash_in_name(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """Role name with dashes triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_role(name="my-web-role")
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["role_name"] == "my-web-role"

    def test_violation_dash_in_fqcn(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """Role FQCN with dashes triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_role(role_fqcn="ns.col.my-role")
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_underscore_name(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """Role name with underscores passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_role(name="my_web_role")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_task_not_matched(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """TASK nodes are not matched by this rule.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        assert not rule.match(g, nid)


# ===========================================================================
# L080 — InternalVarPrefix
# ===========================================================================


class TestInternalVarPrefixGraphRule:
    """Tests for L080 InternalVarPrefixGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InternalVarPrefixGraphRule:
        """Create a rule instance.

        Returns:
            An InternalVarPrefixGraphRule.
        """
        return InternalVarPrefixGraphRule()

    def test_violation_unprefixed_var_in_role(self, rule: InternalVarPrefixGraphRule) -> None:
        """set_fact with unprefixed key in role triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"temp_value": "something"},
            file_path="roles/myrole/tasks/main.yml",
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        variables = result.detail["variables"]
        assert isinstance(variables, list)
        assert "temp_value" in variables

    def test_pass_double_underscore_prefixed_var(self, rule: InternalVarPrefixGraphRule) -> None:
        """set_fact with double-underscore-prefixed key passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"__temp_value": "something"},
            file_path="roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_single_underscore_prefixed_var(self, rule: InternalVarPrefixGraphRule) -> None:
        """set_fact with single-underscore-prefixed key also passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"_temp_value": "something"},
            file_path="roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_cacheable_key_ignored(self, rule: InternalVarPrefixGraphRule) -> None:
        """The ``cacheable`` key is always allowed.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"cacheable": True},
            file_path="roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_not_matched_outside_role(self, rule: InternalVarPrefixGraphRule) -> None:
        """set_fact outside roles/ is not matched.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.set_fact",
            module_options={"temp_value": "something"},
            file_path="playbooks/site.yml",
        )
        assert not rule.match(g, nid)


# ===========================================================================
# L081 — NumberedNames
# ===========================================================================


class TestNumberedNamesGraphRule:
    """Tests for L081 NumberedNamesGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NumberedNamesGraphRule:
        """Create a rule instance.

        Returns:
            A NumberedNamesGraphRule.
        """
        return NumberedNamesGraphRule()

    def test_violation_numbered_file(self, rule: NumberedNamesGraphRule) -> None:
        """File named ``01_setup.yml`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="roles/myrole/tasks/01_setup.yml")
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["filename"] == "01_setup.yml"

    def test_violation_dash_separator(self, rule: NumberedNamesGraphRule) -> None:
        """File named ``02-main.yml`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="playbooks/02-main.yml")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True

    def test_pass_descriptive_name(self, rule: NumberedNamesGraphRule) -> None:
        """Descriptive file name without leading digits passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="playbooks/setup.yml")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_version_prefix(self, rule: NumberedNamesGraphRule) -> None:
        """Name like ``v1_setup.yml`` does not start with digit+separator.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="playbooks/v1_setup.yml")
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False


# ===========================================================================
# L083 — HardcodedGroup
# ===========================================================================


class TestHardcodedGroupGraphRule:
    """Tests for L083 HardcodedGroupGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> HardcodedGroupGraphRule:
        """Create a rule instance.

        Returns:
            A HardcodedGroupGraphRule.
        """
        return HardcodedGroupGraphRule()

    def test_violation_hardcoded_group_in_role(self, rule: HardcodedGroupGraphRule) -> None:
        """Hardcoded ``groups['db_servers']`` in a role triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="roles/myrole/tasks/main.yml")
        node = g.get_node(nid)
        assert node is not None
        node.yaml_lines = "- name: Check group\n  when: inventory_hostname in groups['db_servers']\n"
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        groups = result.detail["found_groups"]
        assert isinstance(groups, list)
        assert "db_servers" in groups

    def test_pass_variable_group(self, rule: HardcodedGroupGraphRule) -> None:
        """Variable reference ``groups[target_group]`` passes (no quotes).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="roles/myrole/tasks/main.yml")
        node = g.get_node(nid)
        assert node is not None
        node.yaml_lines = "- name: Check group\n  when: inventory_hostname in groups[target_group]\n"
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_outside_role(self, rule: HardcodedGroupGraphRule) -> None:
        """Hardcoded groups outside roles/ passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="playbooks/site.yml")
        node = g.get_node(nid)
        assert node is not None
        node.yaml_lines = "- name: Check group\n  when: groups['db_servers']\n"
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_all_ungrouped_filtered(self, rule: HardcodedGroupGraphRule) -> None:
        """``groups['all']`` and ``groups['ungrouped']`` are filtered out.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(file_path="roles/myrole/tasks/main.yml")
        node = g.get_node(nid)
        assert node is not None
        node.yaml_lines = "- name: Check\n  when: groups['all'] and groups['ungrouped']\n"
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False


# ===========================================================================
# L085 — RolePathInclude
# ===========================================================================


class TestRolePathIncludeGraphRule:
    """Tests for L085 RolePathIncludeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> RolePathIncludeGraphRule:
        """Create a rule instance.

        Returns:
            A RolePathIncludeGraphRule.
        """
        return RolePathIncludeGraphRule()

    def test_violation_jinja_without_role_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Include path with Jinja but no ``role_path`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.include_vars",
            module_options={"file": "{{ platform }}/vars.yml"},
            file_path="roles/myrole/tasks/main.yml",
        )
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["include_path"] == "{{ platform }}/vars.yml"

    def test_pass_with_role_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Include path containing ``role_path`` passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.include_vars",
            module_options={"file": "{{ role_path }}/vars/{{ platform }}.yml"},
            file_path="roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_pass_static_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Static include path (no Jinja) passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options={"file": "tasks/setup.yml"},
            file_path="roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_not_matched_outside_role(self, rule: RolePathIncludeGraphRule) -> None:
        """include_vars outside roles/ is not matched.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task(
            module="ansible.builtin.include_vars",
            module_options={"file": "{{ platform }}/vars.yml"},
            file_path="playbooks/site.yml",
        )
        assert not rule.match(g, nid)


# ===========================================================================
# M030 — BrokenConditionalExpressions
# ===========================================================================


class TestBrokenConditionalExpressionsGraphRule:
    """Tests for M030 BrokenConditionalExpressionsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> BrokenConditionalExpressionsGraphRule:
        """Create a rule instance.

        Returns:
            A BrokenConditionalExpressionsGraphRule.
        """
        return BrokenConditionalExpressionsGraphRule()

    @pytest.mark.skipif(not HAS_JINJA, reason="jinja2 not installed")  # type: ignore[untyped-decorator]
    def test_violation_broken_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Unmatched parenthesis in ``when`` triggers violation.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        node = g.get_node(nid)
        assert node is not None
        node.when_expr = "result.rc == 0 and ("
        assert rule.match(g, nid)
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "broken_conditions" in result.detail

    @pytest.mark.skipif(not HAS_JINJA, reason="jinja2 not installed")  # type: ignore[untyped-decorator]
    def test_pass_valid_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Valid Jinja2 ``when`` expression passes.

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        node = g.get_node(nid)
        assert node is not None
        node.when_expr = "result.rc == 0"
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    @pytest.mark.skipif(not HAS_JINJA, reason="jinja2 not installed")  # type: ignore[untyped-decorator]
    def test_pass_no_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Task without ``when`` passes (empty scoped list).

        Args:
            rule: Rule instance under test.
        """
        g, nid = _make_task()
        result = rule.process(g, nid)
        assert result is not None
        assert result.verdict is False

    def test_not_matched_without_jinja(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Without Jinja2, ``match`` always returns False.

        Args:
            rule: Rule instance under test.
        """
        if HAS_JINJA:
            pytest.skip("jinja2 is installed; cannot test HAS_JINJA=False path")
        g, nid = _make_task()
        assert not rule.match(g, nid)
