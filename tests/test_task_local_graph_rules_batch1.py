"""Unit tests for task-local graph rules (L026, L030, L036, L044, L048, L074, L081–L084, L092)."""

from __future__ import annotations

import pytest

from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeScope, NodeType
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.rules.L026_non_fqcn_use_graph import NonFQCNUseGraphRule
from apme_engine.graph.rules.L030_non_builtin_use_graph import NonBuiltinUseGraphRule
from apme_engine.graph.rules.L036_unnecessary_include_vars_graph import UnnecessaryIncludeVarsGraphRule
from apme_engine.graph.rules.L044_avoid_implicit_graph import AvoidImplicitGraphRule
from apme_engine.graph.rules.L048_no_same_owner_graph import NoSameOwnerGraphRule
from apme_engine.graph.rules.L074_no_dashes_in_role_name_graph import NoDashesInRoleNameGraphRule
from apme_engine.graph.rules.L081_numbered_names_graph import NumberedNamesGraphRule
from apme_engine.graph.rules.L082_template_j2_ext_graph import TemplateJ2ExtGraphRule
from apme_engine.graph.rules.L084_subtask_prefix_graph import SubtaskPrefixGraphRule
from apme_engine.graph.rules.L092_loop_var_in_name_graph import LoopVarInNameGraphRule
from apme_engine.graph.scanner import scan


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    name: str | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    tags: list[str] | None = None,
    when_expr: str | None = None,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook→play→task graph.

    Args:
        module: Module name as authored in YAML (short or FQCN).
        module_options: Module argument mapping.
        name: Optional task name.
        file_path: Source file path for the task.
        line_start: Starting line number.
        tags: Task tags list.
        when_expr: When condition string.
        path: YAML path identity for the task node.

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
        name=name,
        module=module,
        module_options=module_options or {},
        tags=tags or [],
        when_expr=when_expr,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


def _make_role_graph(
    *,
    name: str | None = "myrole",
    role_fqcn: str = "",
    file_path: str = "roles/myrole/tasks/main.yml",
    path: str = "site.yml/plays[0]/roles[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook→play→role graph.

    Args:
        name: Role display name.
        role_fqcn: Role FQCN string.
        file_path: File path stored on the role node.
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
        line_start=1,
        name=name,
        role_fqcn=role_fqcn,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(role)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, role.node_id, EdgeType.CONTAINS)
    return g, role.node_id


class TestL026NonFQCNUseGraphRule:
    """Tests for ``NonFQCNUseGraphRule`` (L026)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NonFQCNUseGraphRule:
        """Provide a fresh L026 rule instance.

        Returns:
            A new ``NonFQCNUseGraphRule``.
        """
        return NonFQCNUseGraphRule()

    def test_match_short_module_resolves_to_collection(self, rule: NonFQCNUseGraphRule) -> None:
        """Short module name (no dot) matches.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="copy")
        assert rule.match(g, tid)

    def test_no_match_builtin_resolved(self, rule: NonFQCNUseGraphRule) -> None:
        """Declared FQCN (builtin) does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert not rule.match(g, tid)

    def test_no_match_already_fqcn(self, rule: NonFQCNUseGraphRule) -> None:
        """Declared FQCN form does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.copy")
        assert not rule.match(g, tid)

    def test_violation_detail(self, rule: NonFQCNUseGraphRule) -> None:
        """Violation includes module in detail.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="copy")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        d: YAMLDict = result.detail
        assert d["module"] == "copy"


class TestL030NonBuiltinUseGraphRule:
    """Tests for ``NonBuiltinUseGraphRule`` (L030)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NonBuiltinUseGraphRule:
        """Provide a fresh L030 rule instance.

        Returns:
            A new ``NonBuiltinUseGraphRule``.
        """
        return NonBuiltinUseGraphRule()

    def test_match_collection_module(self, rule: NonBuiltinUseGraphRule) -> None:
        """Declared non-builtin FQCN on module matches.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="community.general.copy")
        assert rule.match(g, tid)

    def test_no_match_builtin(self, rule: NonBuiltinUseGraphRule) -> None:
        """Declared ansible.builtin FQCN does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert not rule.match(g, tid)

    def test_violation_detail_has_fqcn(self, rule: NonBuiltinUseGraphRule) -> None:
        """Violation detail exposes fqcn and builtin alternative.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="community.general.copy")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        d: YAMLDict = result.detail
        assert d["fqcn"] == "community.general.copy"
        assert d["builtin_alternative"] == "ansible.builtin.copy"

    def test_no_match_no_builtin_equivalent(self, rule: NonBuiltinUseGraphRule) -> None:
        """Non-builtin FQCN with no builtin counterpart does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="community.general.timezone")
        assert not rule.match(g, tid)

    def test_no_violation_no_builtin_equivalent(self, rule: NonBuiltinUseGraphRule) -> None:
        """Process returns verdict=False when no builtin equivalent exists.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="community.general.timezone")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_match_amazon_aws_module(self, rule: NonBuiltinUseGraphRule) -> None:
        """Cloud provider modules without builtin equivalents pass.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="amazon.aws.ec2_instance")
        assert not rule.match(g, tid)


class TestL036UnnecessaryIncludeVarsGraphRule:
    """Tests for ``UnnecessaryIncludeVarsGraphRule`` (L036)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnnecessaryIncludeVarsGraphRule:
        """Provide a fresh L036 rule instance.

        Returns:
            A new ``UnnecessaryIncludeVarsGraphRule``.
        """
        return UnnecessaryIncludeVarsGraphRule()

    def test_match_include_vars(self, rule: UnnecessaryIncludeVarsGraphRule) -> None:
        """Resolved include_vars matches.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_vars")
        assert rule.match(g, tid)

    def test_no_match_debug(self, rule: UnnecessaryIncludeVarsGraphRule) -> None:
        """Non-include_vars module does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert not rule.match(g, tid)

    def test_violation_no_tags_no_when(self, rule: UnnecessaryIncludeVarsGraphRule) -> None:
        """include_vars without tags or when is a violation.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_vars")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_tags(self, rule: UnnecessaryIncludeVarsGraphRule) -> None:
        """Tags provide a condition — no violation.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_vars", tags=["setup"])
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_with_when(self, rule: UnnecessaryIncludeVarsGraphRule) -> None:
        """When expression provides a condition — no violation.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_vars", when_expr="condition")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


class TestL044AvoidImplicitGraphRule:
    """Tests for ``AvoidImplicitGraphRule`` (L044)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> AvoidImplicitGraphRule:
        """Provide a fresh L044 rule instance.

        Returns:
            A new ``AvoidImplicitGraphRule``.
        """
        return AvoidImplicitGraphRule()

    def test_violation_file_without_state(self, rule: AvoidImplicitGraphRule) -> None:
        """File module without state violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.file", module_options={})
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        d: YAMLDict = result.detail
        assert d["module"] == "ansible.builtin.file"
        assert "state" in str(d.get("message", "")).lower()

    def test_no_violation_file_with_state(self, rule: AvoidImplicitGraphRule) -> None:
        """Explicit state clears the violation.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.file",
            module_options={"state": "present"},
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_match_debug_not_in_set(self, rule: AvoidImplicitGraphRule) -> None:
        """Modules not requiring explicit state do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert not rule.match(g, tid)


class TestL048NoSameOwnerGraphRule:
    """Tests for ``NoSameOwnerGraphRule`` (L048)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoSameOwnerGraphRule:
        """Provide a fresh L048 rule instance.

        Returns:
            A new ``NoSameOwnerGraphRule``.
        """
        return NoSameOwnerGraphRule()

    def test_match_copy_remote_src(self, rule: NoSameOwnerGraphRule) -> None:
        """Copy with truthy remote_src matches.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.copy",
            module_options={"remote_src": True},
        )
        assert rule.match(g, tid)

    def test_no_match_copy_without_remote_src(self, rule: NoSameOwnerGraphRule) -> None:
        """Copy without remote_src does not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.copy", module_options={})
        assert not rule.match(g, tid)

    def test_violation_no_owner(self, rule: NoSameOwnerGraphRule) -> None:
        """remote_src without owner violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.copy",
            module_options={"remote_src": True},
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_owner(self, rule: NoSameOwnerGraphRule) -> None:
        """Owner set avoids violation.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.copy",
            module_options={"remote_src": True, "owner": "root"},
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


class TestL074NoDashesInRoleNameGraphRule:
    """Tests for ``NoDashesInRoleNameGraphRule`` (L074)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoDashesInRoleNameGraphRule:
        """Provide a fresh L074 rule instance.

        Returns:
            A new ``NoDashesInRoleNameGraphRule``.
        """
        return NoDashesInRoleNameGraphRule()

    def test_match_role_node(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """ROLE nodes match.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role_graph(name="my-role")
        assert rule.match(g, rid)

    def test_no_match_task(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """TASK nodes do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task()
        assert not rule.match(g, tid)

    def test_no_violation_role_name_without_dash(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """Role name without hyphen passes.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role_graph(name="myrole", role_fqcn="")
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is False

    def test_violation_role_fqcn_with_dash(self, rule: NoDashesInRoleNameGraphRule) -> None:
        """Dashes in role FQCN violate with role_name detail.

        Args:
            rule: Rule instance under test.
        """
        g, rid = _make_role_graph(name="", role_fqcn="ns.my-role")
        result = rule.process(g, rid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        d: YAMLDict = result.detail
        assert d["role_name"] == "ns.my-role"


class TestL081NumberedNamesGraphRule:
    """Tests for ``NumberedNamesGraphRule`` (L081)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NumberedNamesGraphRule:
        """Provide a fresh L081 rule instance.

        Returns:
            A new ``NumberedNamesGraphRule``.
        """
        return NumberedNamesGraphRule()

    def test_violation_numbered_basename(self, rule: NumberedNamesGraphRule) -> None:
        """Basename like 01_setup.yml violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(file_path="playbooks/01_setup.yml")
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_plain_name(self, rule: NumberedNamesGraphRule) -> None:
        """Non-numbered basename passes.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(file_path="setup.yml")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


class TestL082TemplateJ2ExtGraphRule:
    """Tests for ``TemplateJ2ExtGraphRule`` (L082)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> TemplateJ2ExtGraphRule:
        """Provide a fresh L082 rule instance.

        Returns:
            A new ``TemplateJ2ExtGraphRule``.
        """
        return TemplateJ2ExtGraphRule()

    def test_violation_src_without_j2(self, rule: TemplateJ2ExtGraphRule) -> None:
        """Literal src without .j2 extension violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.template",
            module_options={"src": "config.cfg"},
        )
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_src_j2(self, rule: TemplateJ2ExtGraphRule) -> None:
        """Src ending in .j2 passes.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.template",
            module_options={"src": "config.j2"},
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_src_jinja_expression(self, rule: TemplateJ2ExtGraphRule) -> None:
        """Templated src is skipped.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            module="ansible.builtin.template",
            module_options={"src": "{{ var }}"},
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


class TestL084SubtaskPrefixGraphRule:
    """Tests for ``SubtaskPrefixGraphRule`` (L084)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> SubtaskPrefixGraphRule:
        """Provide a fresh L084 rule instance.

        Returns:
            A new ``SubtaskPrefixGraphRule``.
        """
        return SubtaskPrefixGraphRule()

    def _role_subtask_path(self) -> str:
        """Return a file path under roles/ that satisfies L084 path rules.

        Returns:
            Relative path containing ``/roles/``.
        """
        return "project/roles/web/tasks/install.yml"

    def test_violation_unprefixed_name(self, rule: SubtaskPrefixGraphRule) -> None:
        """Named task in non-main role file without pipe violates.

        Args:
            rule: Rule instance under test.
        """
        fp = self._role_subtask_path()
        g, tid = _make_task(name="Install nginx", file_path=fp)
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_prefixed_name(self, rule: SubtaskPrefixGraphRule) -> None:
        """Prefix with pipe avoids violation.

        Args:
            rule: Rule instance under test.
        """
        fp = self._role_subtask_path()
        g, tid = _make_task(name="install | Install nginx", file_path=fp)
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_message_uses_filename(self, rule: SubtaskPrefixGraphRule) -> None:
        """Violation message suggests the actual filename stem as prefix.

        Args:
            rule: Rule instance under test.
        """
        fp = self._role_subtask_path()
        g, tid = _make_task(name="Install nginx", file_path=fp)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "install | Description" in str(result.detail.get("message", ""))

    def test_no_match_main_yml(self, rule: SubtaskPrefixGraphRule) -> None:
        """main.yml under roles is excluded.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            name="Install nginx",
            file_path="project/roles/web/tasks/main.yml",
        )
        assert not rule.match(g, tid)

    def test_no_match_outside_roles(self, rule: SubtaskPrefixGraphRule) -> None:
        """Paths without /roles/ do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(name="Install nginx", file_path="site.yml")
        assert not rule.match(g, tid)

    def test_violation_wrong_prefix(self, rule: SubtaskPrefixGraphRule) -> None:
        """Legacy 'sub | ...' name where prefix doesn't match filename violates.

        Args:
            rule: Rule instance under test.
        """
        fp = self._role_subtask_path()
        g, tid = _make_task(name="sub | Install nginx", file_path=fp)
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_match_main_yaml(self, rule: SubtaskPrefixGraphRule) -> None:
        """main.yaml (not just main.yml) under roles is excluded.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(
            name="Install nginx",
            file_path="project/roles/web/tasks/main.yaml",
        )
        assert not rule.match(g, tid)

    def test_case_insensitive_prefix(self, rule: SubtaskPrefixGraphRule) -> None:
        """Prefix comparison is case-insensitive.

        Args:
            rule: Rule instance under test.
        """
        fp = self._role_subtask_path()
        g, tid = _make_task(name="Install | Install nginx", file_path=fp)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


class TestL092LoopVarInNameGraphRule:
    """Tests for ``LoopVarInNameGraphRule`` (L092)."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> LoopVarInNameGraphRule:
        """Provide a fresh L092 rule instance.

        Returns:
            A new ``LoopVarInNameGraphRule``.
        """
        return LoopVarInNameGraphRule()

    def test_violation_item_in_name(self, rule: LoopVarInNameGraphRule) -> None:
        """{{ item }} pattern in name violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(name="Install {{ item }}")
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_plain_name(self, rule: LoopVarInNameGraphRule) -> None:
        """Name without loop var passes.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(name="Install packages")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_match_unnamed_task(self, rule: LoopVarInNameGraphRule) -> None:
        """Tasks without a name do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(name=None)
        assert not rule.match(g, tid)


class TestTaskLocalGraphScanIntegration:
    """Integration tests for ``scan`` with selected graph rules."""

    def test_scan_non_fqcn_violation(self) -> None:
        """L026 fires for short module name (not FQCN).

        Returns:
            None; asserts scan report contains an L026 violation.
        """
        g, _ = _make_task(module="copy")
        rules: list[GraphRule] = [NonFQCNUseGraphRule()]
        report = scan(g, rules)
        assert report.node_results
        found = False
        for nr in report.node_results:
            for rr in nr.rule_results:
                meta = rr.rule
                if meta is not None and meta.rule_id == "L026" and rr.verdict:
                    found = True
                    break
        assert found

    def test_scan_avoid_implicit_violation(self) -> None:
        """L044 fires for file module without state.

        Returns:
            None; asserts scan report contains an L044 violation.
        """
        g, _ = _make_task(module="ansible.builtin.file", module_options={})
        rules: list[GraphRule] = [AvoidImplicitGraphRule()]
        report = scan(g, rules)
        assert report.node_results
        found = False
        for nr in report.node_results:
            for rr in nr.rule_results:
                meta = rr.rule
                if meta is not None and meta.rule_id == "L044" and rr.verdict:
                    found = True
                    break
        assert found


class TestNoqaSuppression:
    """Tests for inline ``# noqa:`` comment suppression in the scanner."""

    def test_noqa_suppresses_specific_rule(self) -> None:
        """A ``# noqa: L026`` comment suppresses that rule for the node."""
        g, tid = _make_task(module="copy")
        node = g.get_node(tid)
        assert node is not None

        rules: list[GraphRule] = [NonFQCNUseGraphRule()]

        node.yaml_lines = "- name: Copy file\n  copy:\n    src: a\n    dest: /tmp/b\n"
        baseline = scan(g, rules)
        baseline_l026 = [
            rr
            for nr in baseline.node_results
            for rr in nr.rule_results
            if rr.rule and rr.rule.rule_id == "L026" and rr.verdict
        ]
        assert baseline_l026, "Baseline scan should produce an L026 violation without noqa"

        node.yaml_lines = "- name: Copy file\n  copy:  # noqa: L026\n    src: a\n    dest: /tmp/b\n"
        report = scan(g, rules)
        suppressed_l026 = [
            rr
            for nr in report.node_results
            for rr in nr.rule_results
            if rr.rule and rr.rule.rule_id == "L026" and rr.verdict
        ]
        assert not suppressed_l026, "L026 should be suppressed by # noqa: L026"

    def test_noqa_does_not_suppress_other_rules(self) -> None:
        """A ``# noqa: L030`` comment does not suppress L026."""
        g, tid = _make_task(module="copy")
        node = g.get_node(tid)
        assert node is not None
        node.yaml_lines = "- name: Copy file\n  copy:  # noqa: L030\n    src: a\n    dest: /tmp/b\n"

        rules: list[GraphRule] = [NonFQCNUseGraphRule()]
        report = scan(g, rules)
        found = False
        for nr in report.node_results:
            for rr in nr.rule_results:
                meta = rr.rule
                if meta is not None and meta.rule_id == "L026" and rr.verdict:
                    found = True
                    break
        assert found

    def test_noqa_multiple_rules(self) -> None:
        """A ``# noqa: L026, L030`` comment suppresses both rules."""
        g, tid = _make_task(module="community.general.copy")
        node = g.get_node(tid)
        assert node is not None

        rules: list[GraphRule] = [NonFQCNUseGraphRule(), NonBuiltinUseGraphRule()]

        node.yaml_lines = "- name: Copy file\n  community.general.copy:\n    src: a\n    dest: /tmp/b\n"
        baseline = scan(g, rules)
        baseline_hits = [
            rr
            for nr in baseline.node_results
            for rr in nr.rule_results
            if rr.rule and rr.rule.rule_id in ("L026", "L030") and rr.verdict
        ]
        assert baseline_hits, "Baseline scan should produce L026 and/or L030 violations without noqa"

        node.yaml_lines = (
            "- name: Copy file  # noqa: L026, L030\n  community.general.copy:\n    src: a\n    dest: /tmp/b\n"
        )
        report = scan(g, rules)
        suppressed_hits = [
            rr
            for nr in report.node_results
            for rr in nr.rule_results
            if rr.rule and rr.rule.rule_id in ("L026", "L030") and rr.verdict
        ]
        assert not suppressed_hits, "Both L026 and L030 should be suppressed by # noqa: L026, L030"

    def test_parse_noqa_empty(self) -> None:
        """No noqa comment yields empty set."""
        from apme_engine.graph.scanner import parse_noqa

        assert parse_noqa("- name: Install\n  ansible.builtin.yum:\n") == frozenset()

    def test_parse_noqa_single(self) -> None:
        """Single rule noqa is parsed correctly."""
        from apme_engine.graph.scanner import parse_noqa

        assert parse_noqa("- name: Run cmd  # noqa: R108\n") == frozenset({"R108"})

    def test_parse_noqa_multi(self) -> None:
        """Multiple rules in one noqa comment are all captured."""
        from apme_engine.graph.scanner import parse_noqa

        result = parse_noqa("  become: true  # noqa: R108, R103\n")
        assert result == frozenset({"R108", "R103"})
