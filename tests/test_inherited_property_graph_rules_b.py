"""Unit tests for graph-native rules L033, M030, L049, and M010 (inherited properties).

These rules consume ``ContentGraph`` and walk ``CONTAINS`` ancestry for effective
``when``, ``loop_control``, and variable scope.
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
from apme_engine.validators.native.rules.L033_unconditional_override_graph import (
    UnconditionalOverrideGraphRule,
)
from apme_engine.validators.native.rules.L049_loop_var_prefix_graph import LoopVarPrefixGraphRule
from apme_engine.validators.native.rules.M010_python2_interpreter_graph import (
    Python2InterpreterGraphRule,
)
from apme_engine.validators.native.rules.M030_broken_conditional_expressions_graph import (
    HAS_JINJA,
    BrokenConditionalExpressionsGraphRule,
)


class TestL033GraphRule:
    """Tests for ``UnconditionalOverrideGraphRule`` (L033).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> UnconditionalOverrideGraphRule:
        """Provide a fresh L033 rule instance.

        Returns:
            A new ``UnconditionalOverrideGraphRule``.
        """
        return UnconditionalOverrideGraphRule()

    def test_no_match_no_set_facts(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Plain task with no ``set_facts`` or ``register`` does not match.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is False

    def test_match_task_with_set_facts(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Task defining facts matches the rule.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            set_facts={"my_var": "v"},
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is True

    def test_match_task_with_register(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Task with ``register`` matches the rule.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            register="result",
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is True

    def test_no_violation_with_when(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Local ``when`` suppresses unconditional-override findings.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            set_facts={"x": 1},
            when_expr="x is defined",
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_no_violation_with_inherited_when(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Ancestor ``when`` is effective; detail records inherited source.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        blk = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="p.yml",
            line_start=5,
            scope=NodeScope.OWNED,
            when_expr="some_condition | bool",
        )
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=12,
            scope=NodeScope.OWNED,
            set_facts={"a": 1},
        )
        g.add_node(play)
        g.add_node(blk)
        g.add_node(t)
        g.add_edge(play.node_id, blk.node_id, EdgeType.CONTAINS)
        g.add_edge(blk.node_id, t.node_id, EdgeType.CONTAINS)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False
        assert r.detail is not None
        assert r.detail.get("inherited_when_from") == blk.node_id

    def test_violation_multiple_definers(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Two tasks defining the same var without conditions yields a violation.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        t1 = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            set_facts={"shared": 1},
        )
        t2 = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[1]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=20,
            scope=NodeScope.OWNED,
            set_facts={"shared": 2},
        )
        g.add_node(play)
        g.add_node(t1)
        g.add_node(t2)
        g.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS)
        g.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS)
        r = rule.process(g, t1.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        vars_detail = r.detail.get("variables")
        assert isinstance(vars_detail, list)
        names: set[str] = set()
        for entry in vars_detail:
            assert isinstance(entry, dict)
            n = entry.get("name")
            assert isinstance(n, str)
            names.add(n)
        assert "shared" in names

    def test_no_violation_single_definer(self, rule: UnconditionalOverrideGraphRule) -> None:
        """Only one definer for a name does not produce a violation.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            set_facts={"only_me": 1},
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False


@pytest.mark.skipif(not HAS_JINJA, reason="M030 requires Jinja2")
class TestM030GraphRule:
    """Tests for ``BrokenConditionalExpressionsGraphRule`` (M030).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> BrokenConditionalExpressionsGraphRule:
        """Provide a fresh M030 rule instance.

        Returns:
            A new ``BrokenConditionalExpressionsGraphRule``.
        """
        return BrokenConditionalExpressionsGraphRule()

    def test_match_task(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Tasks match when Jinja2 is available.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is True

    def test_no_match_play(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Play nodes are not matched.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_no_violation_valid_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Syntactically valid Jinja in ``when`` does not violate.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            when_expr="x is defined",
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_no_violation_valid_when_list(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """List of valid ``when`` strings does not violate.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            when_expr=["x is defined", "y is defined"],
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_violation_broken_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Unclosed Jinja in ``when`` is reported in ``broken_conditions``.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            when_expr='broken {{ invalid"',
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        broken = r.detail.get("broken_conditions")
        assert isinstance(broken, list)
        conditions = [str(e.get("condition", "")) for e in broken if isinstance(e, dict)]
        assert any("invalid" in c for c in conditions)

    def test_violation_broken_when_in_list(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """A broken entry inside a list ``when`` is reported with ``broken_conditions``.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            when_expr=["x is defined", 'broken {{ oops"'],
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        broken = r.detail.get("broken_conditions") if r.detail else None
        assert isinstance(broken, list)
        conditions = [str(e.get("condition", "")) for e in broken if isinstance(e, dict)]
        assert any("oops" in c for c in conditions)

    def test_no_when_no_violation(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """No ``when`` on task or ancestors yields a clean result.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_ancestor_broken_when(self, rule: BrokenConditionalExpressionsGraphRule) -> None:
        """Broken ``when`` on a block is attributed via ``defined_at``.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        blk = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="p.yml",
            line_start=5,
            scope=NodeScope.OWNED,
            when_expr="broken {{ unclosed",
        )
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=12,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        g.add_node(blk)
        g.add_node(t)
        g.add_edge(play.node_id, blk.node_id, EdgeType.CONTAINS)
        g.add_edge(blk.node_id, t.node_id, EdgeType.CONTAINS)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        broken = r.detail.get("broken_conditions") if r.detail else None
        assert isinstance(broken, list) and broken
        first = broken[0]
        assert isinstance(first, dict)
        assert first.get("defined_at") == blk.node_id


class TestL049GraphRule:
    """Tests for ``LoopVarPrefixGraphRule`` (L049).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> LoopVarPrefixGraphRule:
        """Provide a fresh L049 rule instance.

        Returns:
            A new ``LoopVarPrefixGraphRule``.
        """
        return LoopVarPrefixGraphRule()

    def test_no_match_no_loop(self, rule: LoopVarPrefixGraphRule) -> None:
        """Tasks without a ``loop`` do not match.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is False

    def test_match_task_with_loop(self, rule: LoopVarPrefixGraphRule) -> None:
        """Task with ``loop`` matches.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            loop=["a", "b"],
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is True

    def test_violation_default_loop_var(self, rule: LoopVarPrefixGraphRule) -> None:
        """Implicit default ``item`` loop variable violates.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            loop=["a", "b"],
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("loop_var") == "item"

    def test_no_violation_prefixed_loop_var(self, rule: LoopVarPrefixGraphRule) -> None:
        """``loop_var`` with ``item_`` prefix passes.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            loop=["a", "b"],
            loop_control={"loop_var": "item_foo"},
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_violation_unprefixed_loop_var(self, rule: LoopVarPrefixGraphRule) -> None:
        """Custom loop var without ``item_`` prefix violates.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            loop=["a", "b"],
            loop_control={"loop_var": "my_var"},
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("loop_var") == "my_var"

    def test_inherited_loop_control(self, rule: LoopVarPrefixGraphRule) -> None:
        """Effective ``loop_control`` from a block sets ``inherited_loop_control_from``.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        blk = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]", node_type=NodeType.BLOCK),
            file_path="p.yml",
            line_start=5,
            scope=NodeScope.OWNED,
            loop_control={"loop_var": "my_var"},
        )
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/block[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=12,
            scope=NodeScope.OWNED,
            loop=["a", "b"],
        )
        g.add_node(play)
        g.add_node(blk)
        g.add_node(t)
        g.add_edge(play.node_id, blk.node_id, EdgeType.CONTAINS)
        g.add_edge(blk.node_id, t.node_id, EdgeType.CONTAINS)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("inherited_loop_control_from") == blk.node_id
        assert r.detail.get("loop_var") == "my_var"


class TestM010GraphRule:
    """Tests for ``Python2InterpreterGraphRule`` (M010).

    The ``rule`` fixture yields a new rule instance for each test.
    """

    @pytest.fixture  # type: ignore[untyped-decorator]
    def rule(self) -> Python2InterpreterGraphRule:
        """Provide a fresh M010 rule instance.

        Returns:
            A new ``Python2InterpreterGraphRule``.
        """
        return Python2InterpreterGraphRule()

    def test_match_task(self, rule: Python2InterpreterGraphRule) -> None:
        """Task nodes match M010.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        assert rule.match(g, t.node_id) is True

    def test_no_match_play(self, rule: Python2InterpreterGraphRule) -> None:
        """Play nodes do not match.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        assert rule.match(g, play.node_id) is False

    def test_violation_local_python2(self, rule: Python2InterpreterGraphRule) -> None:
        """Task-level ``ansible_python_interpreter`` pointing at Python 2 violates.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            variables={"ansible_python_interpreter": "/usr/bin/python2.7"},
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert "python2" in str(r.detail.get("interpreter", "")).lower()

    def test_no_violation_python3(self, rule: Python2InterpreterGraphRule) -> None:
        """Python 3 interpreter path does not violate.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
            variables={"ansible_python_interpreter": "/usr/bin/python3"},
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False

    def test_violation_inherited_python2(self, rule: Python2InterpreterGraphRule) -> None:
        """Play-level Python 2 interpreter inherited by the task is reported with provenance.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        play = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]", node_type=NodeType.PLAY),
            file_path="p.yml",
            line_start=1,
            scope=NodeScope.OWNED,
            variables={"ansible_python_interpreter": "/usr/bin/python2"},
        )
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(play)
        g.add_node(t)
        g.add_edge(play.node_id, t.node_id, EdgeType.CONTAINS)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is True
        assert r.detail is not None
        assert r.detail.get("defined_at") == play.node_id
        assert r.detail.get("source") == "play"

    def test_no_violation_no_interpreter(self, rule: Python2InterpreterGraphRule) -> None:
        """No interpreter configured means no violation.

        Args:
            rule: Rule instance under test.
        """
        g = ContentGraph()
        t = ContentNode(
            identity=NodeIdentity(path="p.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
            file_path="p.yml",
            line_start=10,
            scope=NodeScope.OWNED,
        )
        g.add_node(t)
        r = rule.process(g, t.node_id)
        assert r is not None
        assert r.verdict is False
