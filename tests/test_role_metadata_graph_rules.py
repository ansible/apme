"""Unit tests for role-metadata graph rules (L027, L052, L053, L054, L055, L077, L079)."""

from __future__ import annotations

import pytest

from apme_engine.engine.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeScope, NodeType
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L027_role_without_metadata_graph import RoleWithoutMetadataGraphRule
from apme_engine.validators.native.rules.L052_galaxy_version_incorrect_graph import GalaxyVersionIncorrectGraphRule
from apme_engine.validators.native.rules.L053_meta_incorrect_graph import MetaIncorrectGraphRule
from apme_engine.validators.native.rules.L054_meta_no_tags_graph import MetaNoTagsGraphRule
from apme_engine.validators.native.rules.L055_meta_video_links_graph import MetaVideoLinksGraphRule
from apme_engine.validators.native.rules.L077_role_arg_specs_graph import RoleArgSpecsGraphRule
from apme_engine.validators.native.rules.L079_role_var_prefix_graph import RoleVarPrefixGraphRule


def _make_role(
    *,
    role_name: str = "myrole",
    role_metadata: YAMLDict | None = None,
    default_variables: YAMLDict | None = None,
    role_variables: YAMLDict | None = None,
    file_path: str = "project/roles/myrole/tasks/main.yml",
    path: str = "roles/myrole",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> role graph.

    Args:
        role_name: Display name for the role node.
        role_metadata: Parsed meta/main.yml contents.
        default_variables: Role defaults mapping.
        role_variables: Role vars mapping.
        file_path: Source file path for the role.
        path: YAML path identity for the role node.

    Returns:
        Tuple of ``(graph, role_node_id)``.
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
    role = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.ROLE),
        file_path=file_path,
        name=role_name,
        role_fqcn=role_name,
        role_metadata=role_metadata or {},
        default_variables=default_variables or {},
        role_variables=role_variables or {},
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(role)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, role.node_id, EdgeType.CONTAINS)
    return g, role.node_id


# ---------------------------------------------------------------------------
# L027 — RoleWithoutMetadata
# ---------------------------------------------------------------------------


class TestL027RoleWithoutMetadataGraphRule:
    """Tests for L027 RoleWithoutMetadataGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> RoleWithoutMetadataGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return RoleWithoutMetadataGraphRule()

    def test_violation_empty_metadata(self, rule: RoleWithoutMetadataGraphRule) -> None:
        """Role with empty metadata violates.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(role_metadata={})
        assert rule.match(g, rid)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_metadata(self, rule: RoleWithoutMetadataGraphRule) -> None:
        """Role with populated metadata passes.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(role_metadata={"galaxy_info": {"author": "me"}})
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_skip_non_role(self, rule: RoleWithoutMetadataGraphRule) -> None:
        """Non-role nodes do not match.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role()
        play_id = "site.yml/plays[0]"
        assert not rule.match(g, play_id)


# ---------------------------------------------------------------------------
# L052 — GalaxyVersionIncorrect
# ---------------------------------------------------------------------------


class TestL052GalaxyVersionIncorrectGraphRule:
    """Tests for L052 GalaxyVersionIncorrectGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> GalaxyVersionIncorrectGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return GalaxyVersionIncorrectGraphRule()

    def test_violation_bad_version(self, rule: GalaxyVersionIncorrectGraphRule) -> None:
        """Non-semver version violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"version": "latest"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_semver(self, rule: GalaxyVersionIncorrectGraphRule) -> None:
        """Valid semver passes.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"version": "1.2.3"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_two_part(self, rule: GalaxyVersionIncorrectGraphRule) -> None:
        """Two-part version passes.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"version": "1.0"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_missing_version(self, rule: GalaxyVersionIncorrectGraphRule) -> None:
        """Missing version is not a violation.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": "me"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L053 — MetaIncorrect
# ---------------------------------------------------------------------------


class TestL053MetaIncorrectGraphRule:
    """Tests for L053 MetaIncorrectGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> MetaIncorrectGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return MetaIncorrectGraphRule()

    def test_violation_galaxy_info_not_dict(self, rule: MetaIncorrectGraphRule) -> None:
        """Galaxy_info as string violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": "bad"}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_violation_deps_not_list(self, rule: MetaIncorrectGraphRule) -> None:
        """Dependencies as string violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"dependencies": "bad"}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_violation_string_field_wrong_type(self, rule: MetaIncorrectGraphRule) -> None:
        """Non-string value in string field violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": 123}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_valid(self, rule: MetaIncorrectGraphRule) -> None:
        """Valid meta structure passes.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": "me"}, "dependencies": []}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L054 — MetaNoTags
# ---------------------------------------------------------------------------


class TestL054MetaNoTagsGraphRule:
    """Tests for L054 MetaNoTagsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> MetaNoTagsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return MetaNoTagsGraphRule()

    def test_violation_missing_tags(self, rule: MetaNoTagsGraphRule) -> None:
        """Galaxy_info without tags violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": "me"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_violation_empty_tags(self, rule: MetaNoTagsGraphRule) -> None:
        """Empty galaxy_tags list violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"galaxy_tags": []}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_tags(self, rule: MetaNoTagsGraphRule) -> None:
        """Galaxy_tags present passes.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"galaxy_tags": ["web", "nginx"]}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_with_categories(self, rule: MetaNoTagsGraphRule) -> None:
        """Categories key also accepted.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"categories": ["system"]}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_pass_no_galaxy_info(self, rule: MetaNoTagsGraphRule) -> None:
        """Missing galaxy_info is not a violation.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(role_metadata={"dependencies": []})
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L055 — MetaVideoLinks
# ---------------------------------------------------------------------------


class TestL055MetaVideoLinksGraphRule:
    """Tests for L055 MetaVideoLinksGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> MetaVideoLinksGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return MetaVideoLinksGraphRule()

    def test_violation_bad_urls(self, rule: MetaVideoLinksGraphRule) -> None:
        """Invalid URLs in video_links violate.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"video_links": ["not-a-url", "ftp://bad"]}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_valid_urls(self, rule: MetaVideoLinksGraphRule) -> None:
        """Valid URLs pass.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"video_links": ["https://example.com/video"]}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_pass_no_video_links(self, rule: MetaVideoLinksGraphRule) -> None:
        """Missing video_links is not a violation.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": "me"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_violation_not_list(self, rule: MetaVideoLinksGraphRule) -> None:
        """Non-list video_links violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"video_links": "https://example.com"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# L077 — RoleArgSpecs
# ---------------------------------------------------------------------------


class TestL077RoleArgSpecsGraphRule:
    """Tests for L077 RoleArgSpecsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> RoleArgSpecsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return RoleArgSpecsGraphRule()

    def test_violation_missing_arg_specs(self, rule: RoleArgSpecsGraphRule) -> None:
        """Role without argument_specs violates.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"galaxy_info": {"author": "me"}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_arg_specs(self, rule: RoleArgSpecsGraphRule) -> None:
        """Role with argument_specs passes.

        Args:
            rule: Rule instance under test.
        """
        meta: YAMLDict = {"argument_specs": {"main": {"short_description": "main entry"}}}
        g, rid = _make_role(role_metadata=meta)
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L079 — RoleVarPrefix
# ---------------------------------------------------------------------------


class TestL079RoleVarPrefixGraphRule:
    """Tests for L079 RoleVarPrefixGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> RoleVarPrefixGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return RoleVarPrefixGraphRule()

    def test_violation_unprefixed(self, rule: RoleVarPrefixGraphRule) -> None:
        """Default variable without role name prefix violates.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="webserver",
            default_variables={"port": 8080, "webserver_ssl": True},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_prefixed(self, rule: RoleVarPrefixGraphRule) -> None:
        """Variables with correct prefix pass.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="webserver",
            default_variables={"webserver_port": 8080, "webserver_ssl": True},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_skip_ansible_vars(self, rule: RoleVarPrefixGraphRule) -> None:
        """Ansible connection variables are skipped.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="webserver",
            default_variables={"ansible_host": "10.0.0.1", "webserver_port": 80},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_skip_double_underscore(self, rule: RoleVarPrefixGraphRule) -> None:
        """Internal variables with __ prefix are skipped.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="webserver",
            default_variables={"__internal": "value", "webserver_port": 80},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_dash_to_underscore(self, rule: RoleVarPrefixGraphRule) -> None:
        """Role name with dashes converts to underscore prefix.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="my-role",
            default_variables={"my_role_port": 80},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_pass_no_name(self, rule: RoleVarPrefixGraphRule) -> None:
        """Role without a name passes (cannot determine prefix).

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role(
            role_name="",
            default_variables={"my_var": "value"},
        )
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestRoleMetadataScannerIntegration:
    """Scanner integration tests for role-metadata rules."""

    def test_scan_role_without_metadata(self) -> None:
        """Scanner picks up L027 violations."""
        g, _rid = _make_role(role_metadata={})
        rules: list[GraphRule] = [RoleWithoutMetadataGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "L027"

    def test_scan_no_violations(self) -> None:
        """Scanner with valid role produces no violations."""
        meta: YAMLDict = {
            "galaxy_info": {
                "author": "me",
                "version": "1.0.0",
                "galaxy_tags": ["web"],
            },
            "argument_specs": {"main": {}},
            "dependencies": [],
        }
        g, _rid = _make_role(
            role_name="webserver",
            role_metadata=meta,
            default_variables={"webserver_port": 80},
        )
        rules: list[GraphRule] = [
            RoleWithoutMetadataGraphRule(),
            GalaxyVersionIncorrectGraphRule(),
            MetaIncorrectGraphRule(),
            MetaNoTagsGraphRule(),
            RoleArgSpecsGraphRule(),
            RoleVarPrefixGraphRule(),
        ]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 0
