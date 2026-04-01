"""Unit tests for task-local graph rules batch 2 (L037, L038, L075, L080, L085, L100–L102, M027)."""

from __future__ import annotations

import pytest

from apme_engine.engine.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeScope, NodeType
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L037_unresolved_module_graph import UnresolvedModuleGraphRule
from apme_engine.validators.native.rules.L038_unresolved_role_graph import UnresolvedRoleGraphRule
from apme_engine.validators.native.rules.L075_ansible_managed_graph import AnsibleManagedGraphRule
from apme_engine.validators.native.rules.L080_internal_var_prefix_graph import InternalVarPrefixGraphRule
from apme_engine.validators.native.rules.L085_role_path_include_graph import RolePathIncludeGraphRule
from apme_engine.validators.native.rules.L100_var_naming_keyword_graph import VarNamingKeywordGraphRule
from apme_engine.validators.native.rules.L101_var_naming_reserved_graph import VarNamingReservedGraphRule
from apme_engine.validators.native.rules.L102_var_naming_read_only_graph import VarNamingReadOnlyGraphRule
from apme_engine.validators.native.rules.M027_legacy_kv_merged_with_args_graph import LegacyKvMergedWithArgsGraphRule


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    set_facts: YAMLDict | None = None,
    options: YAMLDict | None = None,
    register: str | None = None,
    name: str | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Module name as authored in YAML (short or FQCN).
        module_options: Module argument mapping.
        set_facts: Extracted set_fact key mapping.
        options: Task options dict.
        register: Register variable name.
        name: Optional task name.
        file_path: Source file path for the task.
        line_start: Starting line number.
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
        set_facts=set_facts or {},
        options=options or {},
        register=register,
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


# ---------------------------------------------------------------------------
# L037 — UnresolvedModule
# ---------------------------------------------------------------------------


class TestL037UnresolvedModuleGraphRule:
    """Tests for L037 UnresolvedModuleGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnresolvedModuleGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return UnresolvedModuleGraphRule()

    def test_violation_unresolved(self, rule: UnresolvedModuleGraphRule) -> None:
        """Module with no resolution is flagged.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="custom_module")
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["module"] == "custom_module"

    def test_no_violation_resolved(self, rule: UnresolvedModuleGraphRule) -> None:
        """Module with resolved FQCN passes.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.file")
        assert not rule.match(g, tid)

    def test_skip_include_tasks(self, rule: UnresolvedModuleGraphRule) -> None:
        """Include/import actions are excluded from matching.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="include_tasks")
        assert not rule.match(g, tid)

    def test_skip_import_role(self, rule: UnresolvedModuleGraphRule) -> None:
        """Import_role actions are excluded from matching.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="import_role")
        assert not rule.match(g, tid)

    def test_skip_bare_include(self, rule: UnresolvedModuleGraphRule) -> None:
        """Legacy bare ``include`` action is excluded from matching.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="include")
        assert not rule.match(g, tid)

    def test_skip_empty_module(self, rule: UnresolvedModuleGraphRule) -> None:
        """Tasks with no module string do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="")
        assert not rule.match(g, tid)


# ---------------------------------------------------------------------------
# L038 — UnresolvedRole
# ---------------------------------------------------------------------------


class TestL038UnresolvedRoleGraphRule:
    """Tests for L038 UnresolvedRoleGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnresolvedRoleGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return UnresolvedRoleGraphRule()

    def test_violation_no_edge(self, rule: UnresolvedRoleGraphRule) -> None:
        """Include_role with no outgoing role edge is a violation.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "missing_role"}
        g, tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["role"] == "missing_role"

    def test_no_violation_with_edge(self, rule: UnresolvedRoleGraphRule) -> None:
        """Include_role with an outgoing INCLUDE edge passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "webserver"}
        g, tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        role_node = ContentNode(
            identity=NodeIdentity(path="roles/webserver", node_type=NodeType.ROLE),
            file_path="roles/webserver/tasks/main.yml",
            scope=NodeScope.OWNED,
        )
        g.add_node(role_node)
        g.add_edge(tid, role_node.node_id, EdgeType.INCLUDE)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_skip_non_role_module(self, rule: UnresolvedRoleGraphRule) -> None:
        """Non-role modules do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert not rule.match(g, tid)


# ---------------------------------------------------------------------------
# L075 — AnsibleManaged
# ---------------------------------------------------------------------------


class TestL075AnsibleManagedGraphRule:
    """Tests for L075 AnsibleManagedGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> AnsibleManagedGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return AnsibleManagedGraphRule()

    def test_violation_non_j2_src(self, rule: AnsibleManagedGraphRule) -> None:
        """Template with non-.j2 src violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"src": "nginx.conf"}
        g, tid = _make_task(module="ansible.builtin.template", module_options=mo)
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_j2_src(self, rule: AnsibleManagedGraphRule) -> None:
        """Template with .j2 src passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"src": "nginx.conf.j2"}
        g, tid = _make_task(module="ansible.builtin.template", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_skip_non_template(self, rule: AnsibleManagedGraphRule) -> None:
        """Non-template modules do not match.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.copy")
        assert not rule.match(g, tid)

    def test_skip_jinja_src(self, rule: AnsibleManagedGraphRule) -> None:
        """Jinja-templated src paths are skipped.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"src": "{{ template_name }}"}
        g, tid = _make_task(module="ansible.builtin.template", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L080 — InternalVarPrefix
# ---------------------------------------------------------------------------


class TestL080InternalVarPrefixGraphRule:
    """Tests for L080 InternalVarPrefixGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InternalVarPrefixGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return InternalVarPrefixGraphRule()

    def test_violation_in_role(self, rule: InternalVarPrefixGraphRule) -> None:
        """Set_fact in role without __ prefix violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_var": "hello", "cacheable": False}
        g, tid = _make_task(
            module="ansible.builtin.set_fact",
            module_options=mo,
            file_path="project/roles/myrole/tasks/main.yml",
        )
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_prefixed(self, rule: InternalVarPrefixGraphRule) -> None:
        """Set_fact with __ prefix passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"__internal_var": "hello"}
        g, tid = _make_task(
            module="ansible.builtin.set_fact",
            module_options=mo,
            file_path="project/roles/myrole/tasks/main.yml",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_relative_role_path(self, rule: InternalVarPrefixGraphRule) -> None:
        """Set_fact under relative ``roles/`` path also matches.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_var": "hello"}
        g, tid = _make_task(
            module="ansible.builtin.set_fact",
            module_options=mo,
            file_path="roles/myrole/tasks/main.yml",
        )
        assert rule.match(g, tid)

    def test_skip_outside_role(self, rule: InternalVarPrefixGraphRule) -> None:
        """Set_fact outside /roles/ does not match.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_var": "hello"}
        g, tid = _make_task(
            module="ansible.builtin.set_fact",
            module_options=mo,
            file_path="playbooks/site.yml",
        )
        assert not rule.match(g, tid)


# ---------------------------------------------------------------------------
# L085 — RolePathInclude
# ---------------------------------------------------------------------------


class TestL085RolePathIncludeGraphRule:
    """Tests for L085 RolePathIncludeGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> RolePathIncludeGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return RolePathIncludeGraphRule()

    def test_violation_no_role_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Include with Jinja path but no role_path violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ task_file }}"}
        g, tid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options=mo,
            file_path="project/roles/web/tasks/main.yml",
        )
        assert rule.match(g, tid)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_with_role_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Include with role_path prefix passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ role_path }}/tasks/setup.yml"}
        g, tid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options=mo,
            file_path="project/roles/web/tasks/main.yml",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_skip_static_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Static include path (no Jinja) passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "tasks/setup.yml"}
        g, tid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options=mo,
            file_path="project/roles/web/tasks/main.yml",
        )
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_relative_role_path(self, rule: RolePathIncludeGraphRule) -> None:
        """Include under relative ``roles/`` path also matches.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ task_file }}"}
        g, tid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options=mo,
            file_path="roles/web/tasks/main.yml",
        )
        assert rule.match(g, tid)

    def test_skip_outside_role(self, rule: RolePathIncludeGraphRule) -> None:
        """Include outside /roles/ does not match.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ task_file }}"}
        g, tid = _make_task(
            module="ansible.builtin.include_tasks",
            module_options=mo,
            file_path="playbooks/site.yml",
        )
        assert not rule.match(g, tid)


# ---------------------------------------------------------------------------
# L100 — VarNamingKeyword
# ---------------------------------------------------------------------------


class TestL100VarNamingKeywordGraphRule:
    """Tests for L100 VarNamingKeywordGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> VarNamingKeywordGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return VarNamingKeywordGraphRule()

    def test_violation_set_fact_keyword(self, rule: VarNamingKeywordGraphRule) -> None:
        """Set_fact with Python keyword name violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"True": "yes", "cacheable": False}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_violation_register_keyword(self, rule: VarNamingKeywordGraphRule) -> None:
        """Register variable with Ansible keyword name violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug", register="tags")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_normal_names(self, rule: VarNamingKeywordGraphRule) -> None:
        """Normal variable names pass.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_fact": "value"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_include_vars_name(self, rule: VarNamingKeywordGraphRule) -> None:
        """Include_vars with keyword ``name`` param violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "register", "file": "vars.yml"}
        g, tid = _make_task(module="ansible.builtin.include_vars", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# L101 — VarNamingReserved
# ---------------------------------------------------------------------------


class TestL101VarNamingReservedGraphRule:
    """Tests for L101 VarNamingReservedGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> VarNamingReservedGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return VarNamingReservedGraphRule()

    def test_violation_set_fact_reserved(self, rule: VarNamingReservedGraphRule) -> None:
        """Set_fact with reserved name violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"ansible_host": "10.0.0.1"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_violation_register_reserved(self, rule: VarNamingReservedGraphRule) -> None:
        """Register with reserved name violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug", register="ansible_facts")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_normal(self, rule: VarNamingReservedGraphRule) -> None:
        """Normal names pass.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"app_version": "1.0"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# L102 — VarNamingReadOnly
# ---------------------------------------------------------------------------


class TestL102VarNamingReadOnlyGraphRule:
    """Tests for L102 VarNamingReadOnlyGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> VarNamingReadOnlyGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return VarNamingReadOnlyGraphRule()

    def test_violation_set_fact_read_only(self, rule: VarNamingReadOnlyGraphRule) -> None:
        """Set_fact overwriting read-only var violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"inventory_hostname": "override"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_violation_register_read_only(self, rule: VarNamingReadOnlyGraphRule) -> None:
        """Register with read-only var name violates.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.command", register="groups")
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_normal(self, rule: VarNamingReadOnlyGraphRule) -> None:
        """Normal names pass.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_result": "data"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# M027 — LegacyKvMergedWithArgs
# ---------------------------------------------------------------------------


class TestM027LegacyKvMergedWithArgsGraphRule:
    """Tests for M027 LegacyKvMergedWithArgsGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> LegacyKvMergedWithArgsGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return LegacyKvMergedWithArgsGraphRule()

    def test_violation_mixed(self, rule: LegacyKvMergedWithArgsGraphRule) -> None:
        """Inline k=v plus args: mapping violates.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "src=/tmp/a dest=/tmp/b"}
        opts: YAMLDict = {"args": {"mode": "0644"}}
        g, tid = _make_task(module="ansible.builtin.copy", module_options=mo, options=opts)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_args_only(self, rule: LegacyKvMergedWithArgsGraphRule) -> None:
        """Only args: mapping without inline k=v passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"src": "/tmp/a", "dest": "/tmp/b"}
        opts: YAMLDict = {"args": {"mode": "0644"}}
        g, tid = _make_task(module="ansible.builtin.copy", module_options=mo, options=opts)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_no_args_key(self, rule: LegacyKvMergedWithArgsGraphRule) -> None:
        """Inline k=v without args: key passes.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "src=/tmp/a dest=/tmp/b"}
        g, tid = _make_task(module="ansible.builtin.copy", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestBatch2ScannerIntegration:
    """Scanner integration tests for batch 2 rules."""

    def test_scan_unresolved_module(self) -> None:
        """Scanner picks up L037 violations."""
        g, _tid = _make_task(module="unknown_module")
        rules: list[GraphRule] = [UnresolvedModuleGraphRule(enabled=True)]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "L037"

    def test_scan_var_naming_keyword(self) -> None:
        """Scanner picks up L100 violations."""
        mo: YAMLDict = {"True": "yes"}
        g, _tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        rules: list[GraphRule] = [VarNamingKeywordGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) >= 1
        assert any(v.rule is not None and v.rule.rule_id == "L100" for v in violations)
