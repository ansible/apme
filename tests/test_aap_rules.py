"""Unit tests for AAP-specific graph rules (A001, A002)."""

from __future__ import annotations

from apme_engine.engine.models import YAMLDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
)
from apme_engine.graph.rules.A001_template_id_usage_graph import (
    TemplateIDUsageGraphRule,
)
from apme_engine.graph.rules.A002_deprecated_aap_api_graph import (
    DeprecatedAAPAPIGraphRule,
)
from apme_engine.graph.scanner import GraphScanReport, scan


def _find_violations(report: GraphScanReport, rule_id: str) -> list[GraphRuleResult]:
    """Extract violations for a specific rule from scan report.

    Args:
        report: GraphScanReport from scan().
        rule_id: Rule ID to filter by.

    Returns:
        List of RuleResults that are violations for the given rule.
    """
    return [
        rr
        for nr in report.node_results
        for rr in nr.rule_results
        if rr.rule and rr.rule.rule_id == rule_id and rr.verdict
    ]


def _make_task(
    *,
    module: str = "debug",
    module_options: YAMLDict | None = None,
    name: str | None = None,
    file_path: str = "site.yml",
    line_start: int = 10,
    path: str = "site.yml/plays[0]/tasks[0]",
) -> tuple[ContentGraph, str]:
    """Build a minimal playbook->play->task graph.

    Args:
        module: Module name as authored in YAML.
        module_options: Module argument mapping.
        name: Optional task name.
        file_path: Source file path for the task.
        line_start: Starting line number.
        path: YAML path identity for the task node.

    Returns:
        Tuple of (graph, task_node_id).
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


class TestA001TemplateIDUsage:
    """Tests for A001: hardcoded template ID detection."""

    def test_uri_hardcoded_job_template_id_flagged(self) -> None:
        """URI module with /api/v2/job_templates/<id>/ is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://controller.example.com/api/v2/job_templates/42/launch/",
                "method": "POST",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "42"
        assert violations[0].detail["template_type"] == "job_template"

    def test_uri_hardcoded_workflow_template_id_flagged(self) -> None:
        """URI module with /api/v2/workflow_job_templates/<id>/ is flagged."""
        g, _ = _make_task(
            module="uri",
            module_options={
                "url": "https://aap.local/api/v2/workflow_job_templates/99/launch/",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "99"
        assert violations[0].detail["template_type"] == "workflow_template"

    def test_uri_gateway_path_hardcoded_id_flagged(self) -> None:
        """URI module with /api/controller/v2/job_templates/<id>/ is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://aap.local/api/controller/v2/job_templates/42/launch/",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "42"
        assert violations[0].detail["template_type"] == "job_template"

    def test_uri_hardcoded_id_with_query_params_flagged(self) -> None:
        """URI module with /api/v2/job_templates/<id>?params is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://controller.example.com/api/v2/job_templates/42?foo=bar",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "42"

    def test_uri_hardcoded_id_with_fragment_flagged(self) -> None:
        """URI module with /api/v2/job_templates/<id>#section is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://controller.example.com/api/v2/job_templates/42#details",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "42"

    def test_uri_named_url_not_flagged(self) -> None:
        """URI module with named_url pattern is not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://controller.example.com/api/controller/v2/job_templates/Deploy+App++Default/launch/",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0

    def test_uri_templated_base_with_hardcoded_id_flagged(self) -> None:
        """URI module with templated base and literal numeric ID is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "{{ controller_url }}/api/v2/job_templates/42/launch/",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "42"

    def test_uri_templated_template_id_not_flagged(self) -> None:
        """URI module with templated template ID is not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "{{ controller_url }}/api/v2/job_templates/{{ template_id }}/launch/",
            },
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0

    def test_controller_module_numeric_job_template_id_flagged(self) -> None:
        """Controller module with numeric job_template_id is flagged."""
        g, _ = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template_id": 123},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "123"

    def test_controller_module_numeric_job_template_flagged(self) -> None:
        """Controller module with numeric job_template value is flagged."""
        g, _ = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template": 456},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "456"

    def test_short_controller_module_numeric_job_template_id_flagged(self) -> None:
        """Short controller module names are detected when authored via collections."""
        g, _ = _make_task(
            module="job_launch",
            module_options={"job_template_id": 123},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "123"
        assert violations[0].detail["module"] == "job_launch"

    def test_controller_module_numeric_workflow_template_id_flagged(self) -> None:
        """Controller module with numeric workflow_template_id is flagged."""
        g, _ = _make_task(
            module="ansible.controller.workflow_launch",
            module_options={"workflow_template_id": 789},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "789"
        assert violations[0].detail["template_type"] == "workflow_template"

    def test_controller_module_numeric_workflow_flagged(self) -> None:
        """Controller module with numeric workflow value is flagged."""
        g, _ = _make_task(
            module="ansible.controller.workflow_launch",
            module_options={"workflow": 321},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["hardcoded_id"] == "321"
        assert violations[0].detail["template_type"] == "workflow_template"

    def test_controller_module_named_job_template_not_flagged(self) -> None:
        """Controller module with named job_template is not flagged."""
        g, _ = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template": "Deploy Application"},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0

    def test_controller_module_templated_id_not_flagged(self) -> None:
        """Controller module with Jinja-templated ID is not flagged."""
        g, _ = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template_id": "{{ my_template_id }}"},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0

    def test_non_numeric_job_template_id_not_flagged(self) -> None:
        """Controller module with non-numeric job_template_id string is not flagged."""
        g, _ = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template_id": "my-template-name"},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0

    def test_unrelated_module_not_flagged(self) -> None:
        """Non-AAP modules are not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.debug",
            module_options={"msg": "job_templates/42/"},
        )
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A001")
        assert len(violations) == 0


class TestA002DeprecatedAAPAPI:
    """Tests for A002: deprecated AAP API detection."""

    def test_deprecated_api_v2_job_templates_flagged(self) -> None:
        """URI with deprecated /api/v2/job_templates is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://controller.example.com/api/v2/job_templates/",
            },
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["service"] == "controller"
        assert violations[0].detail["deprecated_path"] == "/api/v2/"
        assert "/api/controller/v2/job_templates" in str(violations[0].detail["recommended_path"])

    def test_deprecated_api_v2_inventories_flagged(self) -> None:
        """URI with deprecated /api/v2/inventories is flagged."""
        g, _ = _make_task(
            module="uri",
            module_options={"url": "https://aap.local/api/v2/inventories/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["service"] == "controller"

    def test_deprecated_hub_api_v2_collections_flagged(self) -> None:
        """URI with deprecated hub /api/v2/collections is flagged as hub."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "https://hub.example.com/api/v2/collections/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["service"] == "hub"
        assert violations[0].detail["recommended_path"] == "/api/hub/v2/collections"

    def test_gateway_api_not_flagged(self) -> None:
        """URI with new gateway /api/controller/v2/ is not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={
                "url": "https://aap.local/api/controller/v2/job_templates/",
            },
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0

    def test_hub_gateway_api_not_flagged(self) -> None:
        """URI with /api/hub/v2/ is not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "https://hub.example.com/api/hub/v2/collections/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0

    def test_templated_base_with_deprecated_path_flagged(self) -> None:
        """Templated base with a literal deprecated AAP path is flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "{{ controller_url }}/api/v2/job_templates/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["service"] == "controller"

    def test_templated_resource_path_not_flagged(self) -> None:
        """Templated resources after /api/v2/ are not classified as AAP resources."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "https://aap.local/api/v2/{{ resource_name }}/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0

    def test_resource_prefix_match_not_flagged(self) -> None:
        """Resources that only start with a known name are not misclassified."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "https://aap.local/api/v2/job_templates_backup/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0

    def test_deprecated_hub_module_flagged(self) -> None:
        """Deprecated ansible.hub.ah_token module is flagged."""
        g, _ = _make_task(
            module="ansible.hub.ah_token",
            module_options={"state": "present"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["module"] == "ansible.hub.ah_token"
        assert violations[0].detail["replacement"] == "ansible.platform.token"

    def test_short_deprecated_hub_module_flagged(self) -> None:
        """Short deprecated hub module names are detected when authored via collections."""
        g, _ = _make_task(
            module="ah_token",
            module_options={"state": "present"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["module"] == "ah_token"
        assert violations[0].detail["replacement"] == "ansible.platform.token"

    def test_deprecated_hub_ah_user_flagged(self) -> None:
        """Deprecated ansible.hub.ah_user module is flagged."""
        g, _ = _make_task(
            module="ansible.hub.ah_user",
            module_options={"username": "admin"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 1
        assert violations[0].detail is not None
        assert violations[0].detail["replacement"] == "ansible.platform.user"

    def test_unrelated_uri_not_flagged(self) -> None:
        """URI to non-AAP endpoints is not flagged."""
        g, _ = _make_task(
            module="ansible.builtin.uri",
            module_options={"url": "https://api.github.com/repos"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0

    def test_non_uri_module_not_flagged(self) -> None:
        """Non-uri modules are not flagged even with /api/v2/ in options."""
        g, _ = _make_task(
            module="ansible.builtin.debug",
            module_options={"msg": "/api/v2/job_templates/"},
        )
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]
        report = scan(g, rules)
        violations = _find_violations(report, "A002")
        assert len(violations) == 0


class TestNoqaSuppression:
    """Test inline suppression via # noqa for A001/A002."""

    def test_a001_noqa_suppression(self) -> None:
        """A001 can be suppressed with noqa comment."""
        g, tid = _make_task(
            module="ansible.controller.job_launch",
            module_options={"job_template_id": 42},
        )
        node = g.get_node(tid)
        assert node is not None
        rules: list[GraphRule] = [TemplateIDUsageGraphRule()]

        baseline = scan(g, rules)
        baseline_violations = _find_violations(baseline, "A001")
        assert baseline_violations, "Baseline should have A001 violation"

        node.yaml_lines = (
            "- name: Launch job  # noqa: A001\n  ansible.controller.job_launch:\n    job_template_id: 42\n"
        )
        report = scan(g, rules)
        suppressed = _find_violations(report, "A001")
        assert not suppressed, "A001 should be suppressed by # noqa: A001"

    def test_a002_noqa_suppression(self) -> None:
        """A002 can be suppressed with noqa comment."""
        g, tid = _make_task(
            module="ansible.hub.ah_token",
            module_options={"state": "present"},
        )
        node = g.get_node(tid)
        assert node is not None
        rules: list[GraphRule] = [DeprecatedAAPAPIGraphRule()]

        baseline = scan(g, rules)
        baseline_violations = _find_violations(baseline, "A002")
        assert baseline_violations, "Baseline should have A002 violation"

        node.yaml_lines = "- name: Get token  # noqa: A002\n  ansible.hub.ah_token:\n    state: present\n"
        report = scan(g, rules)
        suppressed = _find_violations(report, "A002")
        assert not suppressed, "A002 should be suppressed by # noqa: A002"
