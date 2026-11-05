"""Unit tests for GraphRule M031: register sensitive values for output masking."""

from __future__ import annotations

from typing import cast

from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeScope,
    NodeType,
)
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.rules.M031_register_secret_recommendation_graph import (
    RegisterSecretRecommendationGraphRule,
    _find_sensitive_registered_vars,
    _find_sensitive_set_facts,
    _value_has_register_secret_filter,
    _var_name_is_sensitive,
)
from apme_engine.graph.scanner import scan
from apme_engine.graph.types import YAMLDict, YAMLValue


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
    register_projection: dict[str, str] | None = None,
    module: str = "ansible.builtin.command",
    no_log: bool | None = None,
    block_no_log: bool | None = None,
) -> tuple[ContentGraph, str]:
    """Build a playbook > play > [block >] task with register graph.

    Args:
        register: Name of variable to register.
        register_projection: Dictionary-form variable projections from task result.
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
        options=cast(YAMLDict, {"register": register_projection}) if register_projection else {},
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
        assert result == ["api_token_result"]

    def test_non_sensitive_register(self) -> None:
        """Non-sensitive register names return no findings."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.command",
            register="command_result",
        )
        result = _find_sensitive_registered_vars(node)
        assert result == []

    def test_register_projection_without_filter(self) -> None:
        """Sensitive projected strings require the filter."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.uri",
            options={"register": {"api_token": "_task.result.json.token"}},
        )
        assert _find_sensitive_registered_vars(node) == ["api_token"]

    def test_register_projection_with_filter(self) -> None:
        """Projected secret strings using the filter are compliant."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.uri",
            options={"register": {"api_token": "_task.result.json.token | ansible.builtin.register_secret"}},
        )
        assert _find_sensitive_registered_vars(node) == []

    def test_no_register(self) -> None:
        """No register returns no findings."""
        node = ContentNode(
            identity=NodeIdentity(path="test", node_type=NodeType.TASK),
            module="ansible.builtin.command",
        )
        result = _find_sensitive_registered_vars(node)
        assert result == []


class TestRegisterSecretRecommendationGraphRule:
    """Tests for the M031 GraphRule."""

    def test_set_fact_with_sensitive_var_fires(self) -> None:
        """Rule fires when set_fact defines sensitive variable."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret123"})
        rule = RegisterSecretRecommendationGraphRule()

        assert rule.match(graph, task_id)
        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        assert "db_password" in str(result.detail)

    def test_set_fact_with_multiple_sensitive_vars(self) -> None:
        """Rule fires for multiple sensitive variables."""
        graph, task_id = _make_set_fact_graph(facts={"api_token": "tok", "secret_key": "key", "hostname": "host"})
        rule = RegisterSecretRecommendationGraphRule()

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
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_set_fact_with_no_log_still_fires(self) -> None:
        """Task-level no_log does not register a value for later output."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, no_log=True)
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True

    def test_set_fact_with_block_no_log_still_fires(self) -> None:
        """Block-level no_log does not register a value for later output."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, block_no_log=True)
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True

    def test_set_fact_with_play_no_log_still_fires(self) -> None:
        """Play-level no_log does not register a value for later output."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "secret"}, play_no_log=True)
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True

    def test_register_sensitive_var_fires(self) -> None:
        """Rule fires when task registers sensitive variable."""
        graph, task_id = _make_register_graph(register="password_lookup_result")
        rule = RegisterSecretRecommendationGraphRule()

        assert rule.match(graph, task_id)
        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        assert "password_lookup_result" in str(result.detail)

    def test_register_non_sensitive_passes(self) -> None:
        """Rule passes when task registers non-sensitive variable."""
        graph, task_id = _make_register_graph(register="command_output")
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_register_projection_without_filter_fires(self) -> None:
        """A sensitive register projection without the filter fires."""
        graph, task_id = _make_register_graph(register_projection={"api_token": "_task.result.json.token"})
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert detail.get("context") == "register"
        assert "dictionary-form `register`" in str(detail.get("message"))
        assert "dictionary-form `register`" in str(detail.get("recommendation"))

    def test_register_projection_with_filter_passes(self) -> None:
        """A sensitive register projection with the filter passes."""
        graph, task_id = _make_register_graph(
            register_projection={
                "api_token": "_task.result.json.token | ansible.builtin.register_secret",
                "task_result": "_task.result",
            }
        )
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_register_with_no_log_still_fires(self) -> None:
        """Task-level no_log does not register a result field for later output."""
        graph, task_id = _make_register_graph(register="secret_result", no_log=True)
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True

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
        rule = RegisterSecretRecommendationGraphRule()

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
        rule = RegisterSecretRecommendationGraphRule()

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
        rule = RegisterSecretRecommendationGraphRule()

        assert rule.match(g, task.node_id)
        result = rule.process(g, task.node_id)
        assert result is not None
        assert result.verdict is True

    def test_scanner_integration(self) -> None:
        """Rule integrates correctly with graph scanner."""
        graph, _ = _make_set_fact_graph(facts={"api_secret": "secret123"})
        rules: list[GraphRule] = [RegisterSecretRecommendationGraphRule()]

        report = scan(graph, rules, owned_only=True)

        violations = [r for nr in report.node_results for r in nr.rule_results if r.verdict]
        assert len(violations) == 1
        assert violations[0].rule is not None
        assert violations[0].rule.rule_id == "M031"

    def test_detail_contains_context(self) -> None:
        """Violation detail includes context (set_fact vs register)."""
        graph, task_id = _make_set_fact_graph(facts={"token": "secret"})
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert detail.get("context") == "set_fact"

    def test_detail_contains_recommendation(self) -> None:
        """Violation detail includes recommendation."""
        graph, task_id = _make_set_fact_graph(facts={"password": "secret"})
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert "recommendation" in detail
        assert "register_secret" in str(detail.get("recommendation"))

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

        rule = RegisterSecretRecommendationGraphRule()

        assert rule.match(g, handler.node_id)
        result = rule.process(g, handler.node_id)

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
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        detail = result.detail or {}
        sensitive_vars = cast(list[str], detail.get("sensitive_vars", []))
        assert sensitive_vars == sorted(sensitive_vars)

    def test_set_fact_with_register_secret_filter_passes(self) -> None:
        """Rule passes when value already has | register_secret filter."""
        graph, task_id = _make_set_fact_graph(facts={"db_password": "{{ vault_pass | register_secret }}"})
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False
        assert _var_name_is_sensitive("db_password")

        report = scan(graph, [rule], owned_only=True)
        findings = [
            rule_result
            for node_result in report.node_results
            for rule_result in node_result.rule_results
            if rule_result.verdict and rule_result.rule is not None and rule_result.rule.rule_id == "M031"
        ]
        assert findings == []

    def test_set_fact_with_unrelated_register_secret_filter_still_fires(self) -> None:
        """An unrelated filtered string does not protect the assigned secret."""
        graph, task_id = _make_set_fact_graph(
            facts={"db_password": "{{ raw_password }}{{ 'other' | register_secret }}"}
        )
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        assert detail.get("sensitive_vars") == ["db_password"]

    def test_set_fact_with_fqcn_register_secret_filter_passes(self) -> None:
        """Rule passes when value uses the FQCN filter."""
        graph, task_id = _make_set_fact_graph(facts={"api_token": "{{ token | ansible.builtin.register_secret }}"})
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is False

    def test_mixed_registered_and_unregistered_vars(self) -> None:
        """Rule only flags sensitive vars without explicit secret registration."""
        graph, task_id = _make_set_fact_graph(
            facts={
                "db_password": "{{ vault_pass | register_secret }}",
                "api_secret": "plaintext_value",
            }
        )
        rule = RegisterSecretRecommendationGraphRule()

        result = rule.process(graph, task_id)

        assert result is not None
        assert result.verdict is True
        detail = result.detail or {}
        sensitive_vars = cast(list[str], detail.get("sensitive_vars", []))
        assert "api_secret" in sensitive_vars
        assert "db_password" not in sensitive_vars


class TestValueHasRegisterSecretFilter:
    """Tests for _value_has_register_secret_filter helper."""

    def test_simple_register_secret_filter(self) -> None:
        """Detect simple | register_secret filter."""
        assert _value_has_register_secret_filter("{{ password | register_secret }}")

    def test_fqcn_register_secret_filter(self) -> None:
        """Detect the ansible.builtin.register_secret filter."""
        assert _value_has_register_secret_filter("{{ token | ansible.builtin.register_secret }}")

    def test_register_secret_with_other_filters(self) -> None:
        """Detect register_secret in a filter chain."""
        assert _value_has_register_secret_filter("{{ val | default('') | register_secret }}")

    def test_no_register_secret_filter(self) -> None:
        """Return False when register_secret is absent."""
        assert not _value_has_register_secret_filter("{{ password }}")
        assert not _value_has_register_secret_filter("{{ val | default('') }}")
        assert not _value_has_register_secret_filter("{{ val | sensitive }}")

    def test_filter_on_unrelated_concatenated_value_does_not_match(self) -> None:
        """A filter on one concatenated part does not protect the full value."""
        assert not _value_has_register_secret_filter("{{ raw_password }}{{ 'other' | register_secret }}")
        assert not _value_has_register_secret_filter("{{ raw_password ~ ('other' | register_secret) }}")

    def test_non_string_value(self) -> None:
        """Return False for non-string values."""
        assert not _value_has_register_secret_filter(123)
        assert not _value_has_register_secret_filter(None)
        assert not _value_has_register_secret_filter(["list"])
