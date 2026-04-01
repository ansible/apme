"""Unit tests for module-options graph rules (L035, L046, R111, R112) — Phase 2G."""

from __future__ import annotations

import pytest

from apme_engine.engine.content_graph import ContentGraph, ContentNode, EdgeType, NodeIdentity, NodeScope, NodeType
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.L035_unnecessary_set_fact_graph import UnnecessarySetFactGraphRule
from apme_engine.validators.native.rules.L046_no_free_form_graph import NoFreeFormGraphRule
from apme_engine.validators.native.rules.R111_parameterized_import_role_graph import ParameterizedImportRoleGraphRule
from apme_engine.validators.native.rules.R112_parameterized_import_taskfile_graph import (
    ParameterizedImportTaskfileGraphRule,
)


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    name: str | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook -> play -> task graph.

    Args:
        module: Module name as authored in YAML (short or FQCN).
        module_options: Module argument mapping.
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
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g, task.node_id


# ---------------------------------------------------------------------------
# L035 -- UnnecessarySetFact
# ---------------------------------------------------------------------------


class TestL035UnnecessarySetFactGraphRule:
    """Tests for L035 UnnecessarySetFactGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnnecessarySetFactGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return UnnecessarySetFactGraphRule()

    def test_match_set_fact(self, rule: UnnecessarySetFactGraphRule) -> None:
        """Match returns True for set_fact tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.set_fact")
        assert rule.match(g, tid) is True

    def test_no_match_debug(self, rule: UnnecessarySetFactGraphRule) -> None:
        """Match returns False for non-set_fact modules.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert rule.match(g, tid) is False

    def test_violation_random_in_value(self, rule: UnnecessarySetFactGraphRule) -> None:
        """Violation when set_fact value uses random filter.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_password": "{{ lookup('password', '/dev/null') | random }}"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert "impure_args" in result.detail

    def test_no_violation_without_random(self, rule: UnnecessarySetFactGraphRule) -> None:
        """No violation when set_fact values are deterministic.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"my_var": "hello"}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_multiple_impure(self, rule: UnnecessarySetFactGraphRule) -> None:
        """Multiple impure args collected in detail.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {
            "rand_a": "{{ 100 | random }}",
            "clean": "static_value",
            "rand_b": "{{ range(10) | random }}",
        }
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert isinstance(result.detail, dict)
        impure = result.detail["impure_args"]
        assert isinstance(impure, list)
        assert len(impure) == 2

    def test_no_violation_non_string_value(self, rule: UnnecessarySetFactGraphRule) -> None:
        """No violation when values are non-string (int, bool).

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"count": 42, "flag": True}
        g, tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_match_short_module_name(self, rule: UnnecessarySetFactGraphRule) -> None:
        """Match with unresolved short module name.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="set_fact")
        assert rule.match(g, tid) is True


# ---------------------------------------------------------------------------
# L046 -- NoFreeForm
# ---------------------------------------------------------------------------


class TestL046NoFreeFormGraphRule:
    """Tests for L046 NoFreeFormGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoFreeFormGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return NoFreeFormGraphRule()

    def test_match_module_task(self, rule: NoFreeFormGraphRule) -> None:
        """Match returns True for any task with a module.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.stat")
        assert rule.match(g, tid) is True

    def test_no_match_play_node(self, rule: NoFreeFormGraphRule) -> None:
        """Match returns False for non-task nodes.

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

    def test_violation_kv_syntax(self, rule: NoFreeFormGraphRule) -> None:
        """Violation for key=value free-form on non-command module.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "path=/tmp mode=0755"}
        g, tid = _make_task(module="ansible.builtin.stat", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["module"] == "ansible.builtin.stat"

    def test_violation_command_module(self, rule: NoFreeFormGraphRule) -> None:
        """Violation for command module with _raw_params (always free-form).

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "echo hello"}
        g, tid = _make_task(module="ansible.builtin.command", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_no_violation_structured_args(self, rule: NoFreeFormGraphRule) -> None:
        """No violation for properly structured module args.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"path": "/tmp", "mode": "0755"}
        g, tid = _make_task(module="ansible.builtin.stat", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_empty_raw_params(self, rule: NoFreeFormGraphRule) -> None:
        """No violation when _raw_params is empty or whitespace.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "  "}
        g, tid = _make_task(module="ansible.builtin.stat", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_no_raw_params(self, rule: NoFreeFormGraphRule) -> None:
        """No violation when no _raw_params key present.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"src": "/etc/hosts", "dest": "/tmp/hosts"}
        g, tid = _make_task(module="ansible.builtin.copy", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_shell_module(self, rule: NoFreeFormGraphRule) -> None:
        """Violation for shell module with _raw_params.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "ls -la /tmp"}
        g, tid = _make_task(module="ansible.builtin.shell", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# R111 -- ParameterizedImportRole
# ---------------------------------------------------------------------------


class TestR111ParameterizedImportRoleGraphRule:
    """Tests for R111 ParameterizedImportRoleGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ParameterizedImportRoleGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return ParameterizedImportRoleGraphRule()

    def test_match_include_role(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Match returns True for include_role tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_role")
        assert rule.match(g, tid) is True

    def test_match_import_role(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Match returns True for import_role tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.import_role")
        assert rule.match(g, tid) is True

    def test_no_match_include_tasks(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Match returns False for include_tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_tasks")
        assert rule.match(g, tid) is False

    def test_no_match_debug(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Match returns False for regular modules.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.debug")
        assert rule.match(g, tid) is False

    def test_violation_templated_role_name(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Violation when role name contains Jinja2 template.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "{{ role_name }}"}
        g, tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["role"] == "{{ role_name }}"

    def test_no_violation_static_role_name(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """No violation for static role name.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "my_static_role"}
        g, tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_missing_name(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """No violation when name parameter is absent.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {}
        g, tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_violation_jinja_block(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Violation when role name uses Jinja block syntax.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "{% if env == 'prod' %}secure_role{% else %}basic_role{% endif %}"}
        g, tid = _make_task(module="ansible.builtin.import_role", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True

    def test_violation_import_role_fqcn(self, rule: ParameterizedImportRoleGraphRule) -> None:
        """Violation via FQCN import_role with templated name.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"name": "{{ dynamic_role }}"}
        g, tid = _make_task(module="ansible.builtin.import_role", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# R112 -- ParameterizedImportTaskfile
# ---------------------------------------------------------------------------


class TestR112ParameterizedImportTaskfileGraphRule:
    """Tests for R112 ParameterizedImportTaskfileGraphRule."""

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> ParameterizedImportTaskfileGraphRule:
        """Create rule instance.

        Returns:
            Rule instance under test.
        """
        return ParameterizedImportTaskfileGraphRule()

    def test_match_include_tasks(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Match returns True for include_tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_tasks")
        assert rule.match(g, tid) is True

    def test_match_import_tasks(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Match returns True for import_tasks.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.import_tasks")
        assert rule.match(g, tid) is True

    def test_no_match_include_role(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Match returns False for include_role.

        Args:
            rule: Rule instance under test.
        """
        g, tid = _make_task(module="ansible.builtin.include_role")
        assert rule.match(g, tid) is False

    def test_violation_templated_file(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Violation when file parameter contains Jinja2 template.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ task_file }}.yml"}
        g, tid = _make_task(module="ansible.builtin.include_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["taskfile"] == "{{ task_file }}.yml"

    def test_violation_templated_raw_params(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Violation via _raw_params single-line syntax.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "{{ dynamic_tasks }}.yml"}
        g, tid = _make_task(module="ansible.builtin.import_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["taskfile"] == "{{ dynamic_tasks }}.yml"

    def test_no_violation_static_file(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """No violation for static file path.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "install.yml"}
        g, tid = _make_task(module="ansible.builtin.include_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_static_raw_params(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """No violation for static _raw_params path.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"_raw_params": "tasks/setup.yml"}
        g, tid = _make_task(module="ansible.builtin.import_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_no_violation_missing_file(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """No violation when no file/_raw_params present.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {}
        g, tid = _make_task(module="ansible.builtin.include_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is False

    def test_file_param_takes_precedence(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """The ``file`` parameter takes precedence over ``_raw_params``.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{{ templated }}.yml", "_raw_params": "static.yml"}
        g, tid = _make_task(module="ansible.builtin.include_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True
        assert result.detail is not None
        assert result.detail["taskfile"] == "{{ templated }}.yml"

    def test_violation_jinja_block_syntax(self, rule: ParameterizedImportTaskfileGraphRule) -> None:
        """Violation for Jinja block syntax in file path.

        Args:
            rule: Rule instance under test.
        """
        mo: YAMLDict = {"file": "{% if env %}prod.yml{% else %}dev.yml{% endif %}"}
        g, tid = _make_task(module="ansible.builtin.import_tasks", module_options=mo)
        result = rule.process(g, tid)
        assert result is not None
        assert result.verdict is True


# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------


class TestPhase2GScannerIntegration:
    """Scanner integration tests for Phase 2G rules."""

    def test_scan_set_fact_random(self) -> None:
        """Scanner picks up L035 violations."""
        mo: YAMLDict = {"my_rand": "{{ 100 | random }}"}
        g, _tid = _make_task(module="ansible.builtin.set_fact", module_options=mo)
        rules: list[GraphRule] = [UnnecessarySetFactGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "L035"

    def test_scan_free_form(self) -> None:
        """Scanner picks up L046 violations."""
        mo: YAMLDict = {"_raw_params": "path=/tmp mode=0755"}
        g, _tid = _make_task(module="ansible.builtin.file", module_options=mo)
        rules: list[GraphRule] = [NoFreeFormGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "L046"

    def test_scan_parameterized_role(self) -> None:
        """Scanner picks up R111 violations."""
        mo: YAMLDict = {"name": "{{ role_var }}"}
        g, _tid = _make_task(module="ansible.builtin.include_role", module_options=mo)
        rules: list[GraphRule] = [ParameterizedImportRoleGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "R111"

    def test_scan_parameterized_taskfile(self) -> None:
        """Scanner picks up R112 violations."""
        mo: YAMLDict = {"file": "{{ task_var }}.yml"}
        g, _tid = _make_task(module="ansible.builtin.include_tasks", module_options=mo)
        rules: list[GraphRule] = [ParameterizedImportTaskfileGraphRule()]
        report = scan(g, rules)
        violations = [rr for nr in report.node_results for rr in nr.rule_results if rr.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "R112"
