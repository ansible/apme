"""Unit tests for graph-native rules L047, L045, M022, and M026 (inherited properties).

These rules use ``ContentGraph``, ``CONTAINS`` edges, and scope walks for
``no_log``, ``environment``, callback detection, and identifier validation.
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
from apme_engine.validators.native.rules.L045_inline_env_var_graph import InlineEnvVarGraphRule
from apme_engine.validators.native.rules.L047_no_log_password_graph import NoLogPasswordGraphRule
from apme_engine.validators.native.rules.M022_tree___oneline_callback_plugins_graph import (
    TreeOnelineCallbackPluginsGraphRule,
)
from apme_engine.validators.native.rules.M026_invalid_inventory_variable_names_graph import (
    InvalidInventoryVariableNamesGraphRule,
)


def _add_playbook_play_task_chain(
    play: ContentNode,
    task: ContentNode,
) -> ContentGraph:
    """Attach a playbook root and wire CONTAINS edges: playbook to play to task.

    Args:
        play: Play node (must already carry a unique ``NodeIdentity.path``).
        task: Task node under that play.

    Returns:
        Graph containing playbook, play, and task with hierarchy edges.
    """
    pb_path = play.identity.path.rsplit("/", 1)[0] if "/" in play.identity.path else "site.yml"
    pb = ContentNode(
        identity=NodeIdentity(path=pb_path, node_type=NodeType.PLAYBOOK),
        file_path=play.file_path or pb_path,
        scope=NodeScope.OWNED,
    )
    g = ContentGraph()
    g.add_node(pb)
    g.add_node(play)
    g.add_node(task)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return g


class TestL047GraphRule:
    """Tests for ``NoLogPasswordGraphRule`` (L047).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> NoLogPasswordGraphRule:
        """Provide a fresh L047 rule instance.

        Returns:
            A new ``NoLogPasswordGraphRule``.
        """
        return NoLogPasswordGraphRule()

    def test_match_task_with_password_param(self, rule: NoLogPasswordGraphRule) -> None:
        """Task exposing a password-like module key matches.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"password": "secret_val"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        assert rule.match(g, task.node_id) is True

    def test_no_match_task_without_password(self, rule: NoLogPasswordGraphRule) -> None:
        """Task without password-like keys does not match.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"user": "alice"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        assert rule.match(g, task.node_id) is False

    def test_no_match_play_node(self, rule: NoLogPasswordGraphRule) -> None:
        """Play nodes are not evaluated for password-like module keys.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            module_options={"password": "x"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_violation_no_no_log(self, rule: NoLogPasswordGraphRule) -> None:
        """Password-like parameter without ``no_log`` anywhere yields a violation.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"password": "secret_val"},
            no_log=None,
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert "message" in r.detail

    def test_no_violation_local_no_log(self, rule: NoLogPasswordGraphRule) -> None:
        """``no_log: true`` on the task clears the finding.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"password": "secret_val"},
            no_log=True,
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is False

    def test_no_violation_inherited_no_log(self, rule: NoLogPasswordGraphRule) -> None:
        """``no_log: true`` on an ancestor play satisfies the rule for the task.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            no_log=True,
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"password": "secret_val"},
            no_log=None,
            scope=NodeScope.OWNED,
        )
        g = _add_playbook_play_task_chain(play, task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is False

    def test_handler_with_password_param(self, rule: NoLogPasswordGraphRule) -> None:
        """Handlers with password-like options match like tasks.

        Args:
            rule: Rule instance under test.
        """
        handler = ContentNode(
            identity=NodeIdentity(path="handlers/main.yml/handlers[0]", node_type=NodeType.HANDLER),
            file_path="handlers/main.yml",
            line_start=2,
            module_options={"password": "x"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(handler)
        assert rule.match(g, handler.node_id) is True

    def test_scan_integration(self, rule: NoLogPasswordGraphRule) -> None:
        """L047 participates in the graph scanner pipeline.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"api_key": "k"},
            scope=NodeScope.OWNED,
        )
        g = _add_playbook_play_task_chain(play, task)
        report = scan(g, [rule])
        task_results = [nr for nr in report.node_results if nr.node_id == task.node_id]
        assert task_results
        assert any(rr.verdict is True for nr in task_results for rr in nr.rule_results)


class TestL045GraphRule:
    """Tests for ``InlineEnvVarGraphRule`` (L045).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InlineEnvVarGraphRule:
        """Provide a fresh L045 rule instance.

        Returns:
            A new ``InlineEnvVarGraphRule``.
        """
        return InlineEnvVarGraphRule()

    def test_match_task_with_local_env(self, rule: InlineEnvVarGraphRule) -> None:
        """A task with a non-empty ``environment`` map matches.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment={"FOO": "bar"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        assert rule.match(g, task.node_id) is True

    def test_no_match_task_without_env(self, rule: InlineEnvVarGraphRule) -> None:
        """Task with no local or inherited environment does not match.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment=None,
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        assert rule.match(g, task.node_id) is False

    def test_match_inherited_env(self, rule: InlineEnvVarGraphRule) -> None:
        """Environment defined only on the play still matches the child task.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            environment={"FOO": "from_play"},
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment=None,
            scope=NodeScope.OWNED,
        )
        g = _add_playbook_play_task_chain(play, task)
        assert rule.match(g, task.node_id) is True

    def test_process_local_env(self, rule: InlineEnvVarGraphRule) -> None:
        """Local inline environment produces a violation with env detail.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment={"FOO": "bar"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("environment") == {"FOO": "bar"}
        assert "inherited_from" not in r.detail

    def test_process_inherited_env_attribution(self, rule: InlineEnvVarGraphRule) -> None:
        """Inherited environment lists ``inherited_from`` and defining file.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            environment={"FOO": "from_play"},
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment=None,
            scope=NodeScope.OWNED,
        )
        g = _add_playbook_play_task_chain(play, task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("inherited_from") == play.node_id
        assert r.detail.get("defined_in_file") == "site.yml"
        assert r.detail.get("environment") == {"FOO": "from_play"}

    def test_no_match_play_node(self, rule: InlineEnvVarGraphRule) -> None:
        """Plays are not direct targets for L045 matching.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            environment={"X": "1"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(play)
        assert rule.match(g, play.node_id) is False


class TestM022GraphRule:
    """Tests for ``TreeOnelineCallbackPluginsGraphRule`` (M022).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> TreeOnelineCallbackPluginsGraphRule:
        """Provide a fresh M022 rule instance.

        Returns:
            A new ``TreeOnelineCallbackPluginsGraphRule``.
        """
        return TreeOnelineCallbackPluginsGraphRule()

    def test_no_match_non_task(self, rule: TreeOnelineCallbackPluginsGraphRule) -> None:
        """Play nodes are not matched (only tasks and handlers are).

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_no_violation_no_callbacks(self, rule: TreeOnelineCallbackPluginsGraphRule) -> None:
        """Ordinary tasks without removed callbacks get a clean verdict.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="ansible.builtin.debug",
            module_options={"msg": "ok"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is False

    def test_violation_env_stdout_callback(self, rule: TreeOnelineCallbackPluginsGraphRule) -> None:
        """``ANSIBLE_STDOUT_CALLBACK`` set to ``tree`` is a violation.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            environment={"ANSIBLE_STDOUT_CALLBACK": "tree"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert "tree" in str(r.detail.get("removed_callbacks", ()))

    def test_violation_inherited_env(self, rule: TreeOnelineCallbackPluginsGraphRule) -> None:
        """Callback env on the play is visible when walking the scope chain.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            environment={"ANSIBLE_STDOUT_CALLBACK": "tree"},
            scope=NodeScope.OWNED,
        )
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module="ansible.builtin.command",
            module_options={"cmd": "true"},
            environment=None,
            scope=NodeScope.OWNED,
        )
        g = _add_playbook_play_task_chain(play, task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True

    def test_violation_option_text(self, rule: TreeOnelineCallbackPluginsGraphRule) -> None:
        """Callback references embedded in string option values are detected.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"cmd": "stdout_callback=tree"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True


class TestM026GraphRule:
    """Tests for ``InvalidInventoryVariableNamesGraphRule`` (M026).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> InvalidInventoryVariableNamesGraphRule:
        """Provide a fresh M026 rule instance.

        Returns:
            A new ``InvalidInventoryVariableNamesGraphRule``.
        """
        return InvalidInventoryVariableNamesGraphRule()

    def test_no_match_non_task(self, rule: InvalidInventoryVariableNamesGraphRule) -> None:
        """Play nodes are not matched.

        Args:
            rule: Rule instance under test.
        """
        play = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="site.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_no_violation_valid_names(self, rule: InvalidInventoryVariableNamesGraphRule) -> None:
        """Valid Python identifiers in module options and vars pass.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"valid_name": "x"},
            variables={"good_name": 1},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is False

    def test_violation_invalid_module_option(self, rule: InvalidInventoryVariableNamesGraphRule) -> None:
        """Hyphenated module option keys are invalid identifiers.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            module_options={"foo-bar": "x"},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        detail = r.detail
        assert detail is not None
        names = detail.get("invalid_names")
        assert isinstance(names, list)
        assert "foo-bar" in names

    def test_violation_invalid_variable(self, rule: InvalidInventoryVariableNamesGraphRule) -> None:
        """Inline task variables must be valid identifiers.

        Args:
            rule: Rule instance under test.
        """
        task = ContentNode(
            identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=10,
            variables={"bad-name": 1},
            scope=NodeScope.OWNED,
        )
        g = ContentGraph()
        g.add_node(task)
        r = rule.process(g, task.node_id)
        assert r is not None
        assert r.verdict is True
        detail = r.detail
        assert detail is not None
        names = detail.get("invalid_names")
        assert isinstance(names, list)
        assert "bad-name" in names
