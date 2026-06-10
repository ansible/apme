"""Unit tests for GraphRule M031: Sensitive tag recommendation for sensitive variables."""

from __future__ import annotations

from typing import cast

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.engine.graph_scanner import scan
from apme_engine.engine.models import YAMLDict, YAMLValue
from apme_engine.validators.native.rules.graph_rule_base import GraphRule
from apme_engine.validators.native.rules.M031_sensitive_tag_recommendation_graph import (
    SensitiveTagRecommendationGraphRule,
    _find_sensitive_registered_vars,
    _find_sensitive_set_facts,
    _value_has_sensitive_filter,
    _var_name_is_sensitive,
)


def _make_set_fact_graph(
    *,
    facts: dict[str, str] | None = None,
    no_log: bool | None = None,
    block_no_log: bool | None = None,
    play_no_log: bool | None = None,
) -> tuple[ContentGraph, str]:
    """Build a playbook > play > [block >] set_fact task graph.

    Args:
        facts: Dict of fact names to values for set_fact module.
        no_log: Task-level no_log setting.
        block_no_log: Block-level no_log setting.
        play_no_log: Play-level no_log setting.

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
        line_start=1,
        no_log=play_no_log,
        scope=NodeScope.OWNED,
    )

    g.add_node(pb)
    g.add_node(play)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)

    parent_id = play.node_id

    if block_no_log is not None:
        block = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="site.yml",
            line_start=5,
            no_log=block_no_log,
            scope=NodeScope.OWNED,
        )
        g.add_node(block)
        g.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS)
        parent_id = block.node_id

    module_options: YAMLDict = cast("dict[str, YAMLValue]", facts) if facts else {}

    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        module="ansible.builtin.set_fact",
        module_options=module_options,
        no_log=no_log,
        scope=NodeScope.OWNED,
    )
    g.add_node(task)
    g.add_edge(parent_id, task.node_id, EdgeType.CONTAINS)

    return g, task.node_id


def _make_register_graph(
    *,
    register: str | None = None,
    module: str = "ansible.builtin.command",
    no_log: bool | None = None,
    block_no_log: bool | None = None,
) -> tuple[ContentGraph, str]:
    """Build a playbook > play > [block >] task with register graph.

    Args:
        register: Name of variable to register.
        module: Module name for the task.
        no_log: Task-level no_log setting.
        block_no_log: Block-level no_log setting.

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
        line_start=1,
        scope=NodeScope.OWNED,
    )

    g.add_node(pb)
    g.add_node(play)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)

    parent_id = play.node_id

    if block_no_log is not None:
        block = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="site.yml",
            line_start=5,
            no_log=block_no_log,
            scope=NodeScope.OWNED,
        )
        g.add_node(block)
        g.add_edge(play.node_id, block.node_id, EdgeType.CONTAINS)
        parent_id = block.node_id

    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        module=module,
        module_options={"cmd": "echo test"},
        register=register,
        no_log=no_log,
        scope=NodeScope.OWNED,
    )
    g.add_node(task)
    g.add_edge(parent_id, task.node_id, EdgeType.CONTAINS)

    return g, task.node_id


class TestVarNameIsSensitive:
    """Tests for _var_name_is_sensitive helper."""

    def test_password_variants(self) -> None:
        """Password variants are sensitive."""
        assert _var_name_is_sensitive("password")
        assert _var_name_is_sensitive("db_password")
        assert _var_name_is_sensitive("PASSWORD")
        assert _var_name_is_sensitive("passwd")
        assert _var_name_is_sensitive("user_pwd")

    def test_secret_variants(self) -> None:
        """Secret variants are sensitive."""
        assert _var_name_is_sensitive("secret")
        assert _var_name_is_sensitive("app_secret")
        assert _var_name_is_sensitive("secrets")
        assert _var_name_is_sensitive("client_secret")

    def test_token_variants(self) -> None:
        """Token variants are sensitive."""
        assert _var_name_is_sensitive("token")
        assert _var_name_is_sensitive("auth_token")
        assert _var_name_is_sensitive("access_token")
        assert _var_name_is_sensitive("bearer")
        assert _var_name_is_sensitive("jwt")

    def test_api_key_variants(self) -> None:
        """API key variants are sensitive."""
        assert _var_name_is_sensitive("api_key")
        assert _var_name_is_sensitive("apikey")
        assert _var_name_is_sensitive("auth_key")

    def test_credential_variants(self) -> None:
        """Credential variants are sensitive."""
        assert _var_name_is_sensitive("credential")
        assert _var_name_is_sensitive("credentials")
        assert _var_name_is_sensitive("db_cred")

    def test_key_variants(self) -> None:
        """Key variants are sensitive."""
        assert _var_name_is_sensitive("private_key")
        assert _var_name_is_sensitive("ssh_key")
        assert _var_name_is_sensitive("access_key")
        assert _var_name_is_sensitive("client_key")

    def test_oauth_sensitive(self) -> None:
        """OAuth-related names are sensitive."""
        assert _var_name_is_sensitive("oauth")
        assert _var_name_is_sensitive("oauth_token")

    def test_non_sensitive(self) -> None:
        """Non-sensitive names return False."""
        assert not _var_name_is_sensitive("username")
        assert not _var_name_is_sensitive("hostname")
        assert not _var_name_is_sensitive("port")
        assert not _var_name_is_sensitive("config")
        assert not _var_name_is_sensitive("result")

    def test_false_positive_avoidance(self) -> None:
        """Substring matches that are not word-bounded are rejected."""
        assert not _var_name_is_sensitive("secretary_name")
        assert not _var_name_is_sensitive("tokenized_value")
        assert not _var_name_is_sensitive("accreditation")


class TestFindSensitiveSetFacts:
    """Tests for _find_sensitive_set_facts helper."""

    def test_single_sensitive_fact(self) -> None:
        """Find single sensitive fact name."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.set_fact",
            module_options={"db_password": "secret123"},
        )
        result = _find_sensitive_set_facts(node)
        assert result == ["db_password"]

    def test_multiple_sensitive_facts(self) -> None:
        """Find multiple sensitive fact names."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.set_fact",
            module_options={
                "api_token": "tok123",
                "db_password": "pass456",
                "username": "admin",
            },
        )
        result = _find_sensitive_set_facts(node)
        assert "api_token" in result
        assert "db_password" in result
        assert "username" not in result

    def test_no_sensitive_facts(self) -> None:
        """No sensitive facts returns empty list."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.set_fact",
            module_options={"hostname": "example.com", "port": 8080},
        )
        result = _find_sensitive_set_facts(node)
        assert result == []

    def test_empty_module_options(self) -> None:
        """Empty module options returns empty list."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.set_fact",
            module_options={},
        )
        result = _find_sensitive_set_facts(node)
        assert result == []


class TestFindSensitiveRegisteredVars:
    """Tests for _find_sensitive_registered_vars helper."""

    def test_sensitive_register(self) -> None:
        """Find sensitive registered variable."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.command",
            register="api_token_result",
        )
        result = _find_sensitive_registered_vars(node)
        assert result == "api_token_result"

    def test_non_sensitive_register(self) -> None:
        """Non-sensitive register returns None."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.command",
            register="command_result",
        )
        result = _find_sensitive_registered_vars(node)
        assert result is None

    def test_no_register(self) -> None:
        """No register returns None."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.command",
        )
        result = _find_sensitive_registered_vars(node)
        assert result is None


class TestSensitiveTagRecommendationGraphRule:
    """Tests for the M031 GraphRule."""

    def test_set_fact_with_sensitive_var_fires(self) -> None:
        """Rule fires when set_fact defines sensitive variable."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret123"})
        rule = SensitiveTagRecommendationGraphRule()

        assert rule.match(graph, task_id)
        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        assert "db_password" in str(result.detail)

    def test_set_fact_with_multiple_sensitive_vars(self) -> None:
        """Rule fires for multiple sensitive variables."""
        graph, task_id = _make_set_fact_graph(facts={"api_token": "tok", "secret_key": "key", "hostname": "host"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        sensitive_vars = cast(list[str], detail.get("sensitive_vars", []))
        assert "api_token" in sensitive_vars
        assert "secret_key" in sensitive_vars
        assert "hostname" not in sensitive_vars

    def test_set_fact_non_sensitive_passes(self) -> None:
        """Rule passes when set_fact only defines non-sensitive variables."""
        graph, task_id = _make_set_fact_graph(facts={"hostname": "example.com", "port": "8080"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_set_fact_with_no_log_passes(self) -> None:
        """Rule passes when no_log is set on task."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, no_log=True)
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_set_fact_with_block_no_log_passes(self) -> None:
        """Rule passes when no_log is set on containing block."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, block_no_log=True)
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_set_fact_with_play_no_log_passes(self) -> None:
        """Rule passes when no_log is set on containing play."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, play_no_log=True)
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_register_sensitive_var_fires(self) -> None:
        """Rule fires when task registers sensitive variable."""
        graph, task_id = _make_register_graph(register="password_lookup_result")
        rule = SensitiveTagRecommendationGraphRule()

        assert rule.match(graph, task_id)
        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        assert "password_lookup_result" in str(result.detail)

    def test_register_non_sensitive_passes(self) -> None:
        """Rule passes when task registers non-sensitive variable."""
        graph, task_id = _make_register_graph(register="command_output")
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_register_with_no_log_passes(self) -> None:
        """Rule passes when registering sensitive var with no_log."""
        graph, task_id = _make_register_graph(register="secret_result", no_log=True)
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_no_match_for_non_set_fact_non_register(self) -> None:
        """Rule does not match tasks without set_fact or register."""
        g = ContentGraph()
        task = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            file_path="test.yml",
            module="ansible.builtin.debug",
            module_options={"msg": "hello"},
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        rule = SensitiveTagRecommendationGraphRule()

        assert not rule.match(g, task.node_id)

    def test_no_match_for_non_task(self) -> None:
        """Rule does not match non-task nodes."""
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.PLAY),
            file_path="test.yml",
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        rule = SensitiveTagRecommendationGraphRule()

        assert not rule.match(g, play.node_id)

    def test_legacy_set_fact_module_matches(self) -> None:
        """Rule matches legacy 'set_fact' module name."""
        g = ContentGraph()
        task = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            file_path="test.yml",
            module="set_fact",
            module_options={"db_password": "secret"},
            scope=NodeScope.OWNED,
        )
        g.add_node(task)
        rule = SensitiveTagRecommendationGraphRule()

        assert rule.match(g, task.node_id)
        result = rule.process(g, task.node_id)
        assert result is not None
        assert result.verdict is True

    def test_scanner_integration(self) -> None:
        """Rule integrates correctly with graph scanner."""
        graph, _ = _make_set_fact_graph(facts={"api_secret": "secret123"})
        rules: list[GraphRule] = [SensitiveTagRecommendationGraphRule()]

        report = scan(graph, rules, owned_only=True)

        violations = [r for nr in report.node_results for r in nr.rule_results if r.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "M031"

    def test_detail_contains_context(self) -> None:
        """Violation detail includes context (set_fact vs register)."""
        graph, task_id = _make_set_fact_graph(facts={"token": "secret"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert detail.get("context") == "set_fact"

    def test_detail_contains_recommendation(self) -> None:
        """Violation detail includes recommendation."""
        graph, task_id = _make_set_fact_graph(facts={"password": "secret"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert "recommendation" in detail
        assert "Sensitive tag" in str(detail.get("recommendation"))

    def test_handler_with_sensitive_register_fires(self) -> None:
        """Rule fires for handlers registering sensitive variables."""
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
        handler = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/handlers[0]", node_type=NodeType.HANDLER),
            file_path="site.yml",
            line_start=10,
            module="ansible.builtin.command",
            module_options={"cmd": "get-secret"},
            register="secret_value",
            scope=NodeScope.OWNED,
        )

        g.add_node(pb)
        g.add_node(play)
        g.add_node(handler)
        g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, handler.node_id, EdgeType.CONTAINS)

        rule = SensitiveTagRecommendationGraphRule()

        assert rule.match(g, handler.node_id)
        result = rule.process(g, handler.node_id)

        assert result is not None
        assert result.verdict is True

    def test_no_log_false_overrides_block_true(self) -> None:
        """Task no_log: false overrides block no_log: true."""
        graph, task_id = _make_set_fact_graph(
            facts={"password": "secret"},
            no_log=False,
            block_no_log=True,
        )
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True

    def test_bearer_token_sensitive(self) -> None:
        """Bearer variable name is sensitive."""
        assert _var_name_is_sensitive("bearer")
        assert _var_name_is_sensitive("bearer_token")

    def test_jwt_sensitive(self) -> None:
        """JWT variable name is sensitive."""
        assert _var_name_is_sensitive("jwt")
        assert _var_name_is_sensitive("jwt_token")

    def test_sorted_output(self) -> None:
        """Sensitive vars are sorted in output."""
        graph, task_id = _make_set_fact_graph(facts={"z_secret": "a", "a_password": "b", "m_token": "c"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        detail = result.detail or {}
        sensitive_vars = cast(list[str], detail.get("sensitive_vars", []))
        assert sensitive_vars == sorted(sensitive_vars)

    def test_set_fact_with_sensitive_filter_passes(self) -> None:
        """Rule passes when value already has | sensitive filter."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "{{ vault_pass | sensitive }}"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_set_fact_with_fqcn_sensitive_filter_passes(self) -> None:
        """Rule passes when value has | ansible.builtin.sensitive filter."""
        graph, task_id = _make_set_fact_graph(facts={"api_token": "{{ token | ansible.builtin.sensitive }}"})
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_mixed_tagged_and_untagged_vars(self) -> None:
        """Rule only flags untagged sensitive vars, not already-tagged ones."""
        graph, task_id = _make_set_fact_graph(
            facts={
                "db_password": "{{ vault_pass | sensitive }}",
                "api_secret": "plaintext_value",
            }
        )
        rule = SensitiveTagRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        sensitive_vars = cast(list[str], detail.get("sensitive_vars", []))
        assert "api_secret" in sensitive_vars
        assert "db_password" not in sensitive_vars


class TestValueHasSensitiveFilter:
    """Tests for _value_has_sensitive_filter helper."""

    def test_simple_sensitive_filter(self) -> None:
        """Detect simple | sensitive filter."""
        assert _value_has_sensitive_filter("{{ password | sensitive }}")

    def test_fqcn_sensitive_filter(self) -> None:
        """Detect ansible.builtin.sensitive filter."""
        assert _value_has_sensitive_filter("{{ token | ansible.builtin.sensitive }}")

    def test_sensitive_with_other_filters(self) -> None:
        """Detect sensitive filter in filter chain."""
        assert _value_has_sensitive_filter("{{ val | default('') | sensitive }}")

    def test_no_sensitive_filter(self) -> None:
        """Return False when no sensitive filter present."""
        assert not _value_has_sensitive_filter("{{ password }}")
        assert not _value_has_sensitive_filter("{{ val | default('') }}")

    def test_non_string_value(self) -> None:
        """Return False for non-string values."""
        assert not _value_has_sensitive_filter(123)
        assert not _value_has_sensitive_filter(None)
        assert not _value_has_sensitive_filter(["list"])
