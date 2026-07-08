"""Tests for AI-as-graph-transform: ai_context, AINodeFix, unified convergence.

Covers ``build_ai_node_context``, ``AINodeFix``, the unified Tier 1 + Tier 2
convergence loop in ``GraphRemediationEngine``, and ``approve_pending``
with ``source_filter``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from apme_engine.engine.models import ViolationDict
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    EdgeType,
    NodeIdentity,
    NodeType,
)
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
)
from apme_engine.remediation.ai_context import (
    AINodeContext,
    _simplify_dict,
    _truncate_snippet,
    build_ai_node_context,
)
from apme_engine.remediation.ai_provider import AINodeFix, AISkipped
from apme_engine.remediation.graph_engine import (
    AINodeProposal,
    GraphRemediationEngine,
)
from apme_engine.remediation.registry import TransformRegistry

if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_TASK_YAML_APT = """\
- name: Install nginx
  apt:
    name: nginx
    state: present
"""

_TASK_YAML_FQCN = """\
- name: Install nginx
  ansible.builtin.apt:
    name: nginx
    state: present
"""


def _make_node(
    node_id: str = "site.yml/plays[0]/tasks[0]",
    *,
    node_type: NodeType = NodeType.TASK,
    module: str = "apt",
    yaml_lines: str = _TASK_YAML_APT,
    file_path: str = "/workspace/site.yml",
    line_start: int = 3,
    line_end: int = 6,
    name: str = "",
) -> ContentNode:
    """Create a ContentNode for testing.

    Args:
        node_id: Graph node identifier.
        node_type: Type of node.
        module: Module name.
        yaml_lines: YAML content.
        file_path: File path.
        line_start: Line start.
        line_end: Line end.
        name: Node name.

    Returns:
        Configured ContentNode.
    """
    identity = NodeIdentity(path=node_id, node_type=node_type)
    return ContentNode(
        identity=identity,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        module=module,
        yaml_lines=yaml_lines,
        name=name,
    )


class _FQCNRule(GraphRule):
    """Mock rule that flags non-FQCN module names."""

    def __init__(self) -> None:
        """Initialize the FQCN rule."""
        super().__init__(rule_id="M001", description="Use FQCN for modules", enabled=True, precedence=1)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Check if the node uses a short module name.

        Args:
            graph: ContentGraph to inspect.
            node_id: Node to check.

        Returns:
            True if the node has a non-FQCN module name.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.TASK:
            return False
        return bool(node.module and "." not in (node.module or ""))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Create a violation result for non-FQCN modules.

        Args:
            graph: ContentGraph to inspect.
            node_id: Node to process.

        Returns:
            GraphRuleResult with the violation details.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        return GraphRuleResult(
            rule=self.get_metadata(),
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start or 0),
            detail={"message": f"Use FQCN for {node.module}"},
        )


def _fqcn_transform(task: CommentedMap, violation: ViolationDict) -> bool:
    """Node transform: rename short module to FQCN.

    Args:
        task: Ephemeral CommentedMap for the task.
        violation: Violation dict with rule metadata.

    Returns:
        True if the module key was renamed.
    """
    from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

    mk = get_module_key(task)
    if mk and "." not in mk:
        rename_key(task, mk, f"ansible.builtin.{mk}")
        return True
    return False


def _build_registry_with_fqcn() -> TransformRegistry:
    """Build a TransformRegistry with the FQCN transform.

    Returns:
        TransformRegistry with M001 registered.
    """
    reg = TransformRegistry()
    reg.register("M001", node=_fqcn_transform)
    return reg


# ---------------------------------------------------------------------------
# AINodeContext tests
# ---------------------------------------------------------------------------


class TestAINodeContext:
    """Tests for ``build_ai_node_context`` and helpers."""

    def test_build_basic_context(self) -> None:
        """Build context for a simple single-node graph."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)

        violations: list[ViolationDict] = [
            {"rule_id": "M001", "message": "Use FQCN", "path": node.node_id},
        ]

        ctx = build_ai_node_context(graph, node.node_id, violations)

        assert ctx is not None
        assert ctx.node_id == node.node_id
        assert ctx.node_type == "task"
        assert ctx.yaml_lines == _TASK_YAML_APT
        assert len(ctx.violations) == 1
        assert ctx.file_path == "/workspace/site.yml"
        assert ctx.feedback == ""

    def test_returns_none_for_missing_node(self) -> None:
        """Returns None for a node that doesn't exist."""
        graph = ContentGraph()
        ctx = build_ai_node_context(graph, "nonexistent", [])
        assert ctx is None

    def test_returns_none_for_empty_yaml(self) -> None:
        """Returns None for a node with no YAML content."""
        graph = ContentGraph()
        node = _make_node(yaml_lines="")
        graph.add_node(node)
        ctx = build_ai_node_context(graph, node.node_id, [])
        assert ctx is None

    def test_feedback_passthrough(self) -> None:
        """Feedback string is passed through to the context."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)

        ctx = build_ai_node_context(
            graph,
            node.node_id,
            [{"rule_id": "M001", "message": "x"}],
            feedback="Your fix broke things",
        )

        assert ctx is not None
        assert ctx.feedback == "Your fix broke things"

    def test_frozen_dataclass(self) -> None:
        """AINodeContext is immutable (frozen + slots)."""
        import dataclasses

        assert dataclasses.fields(AINodeContext)
        ctx = AINodeContext(
            node_id="test",
            node_type="task",
            yaml_lines="- debug:",
            violations=[],
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ctx.__dict__  # noqa: B018 — slots=True means no __dict__

    def test_parent_context_with_vars(self) -> None:
        """Parent context includes play variables."""
        graph = ContentGraph()
        play = _make_node(
            "site.yml/plays[0]",
            node_type=NodeType.PLAY,
            yaml_lines="- hosts: all\n  vars:\n    pkg: nginx\n",
            name="Install stuff",
        )
        play.variables = {"pkg": "nginx"}
        task = _make_node(
            "site.yml/plays[0]/tasks[0]",
            node_type=NodeType.TASK,
        )
        graph.add_node(play)
        graph.add_node(task)
        graph.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)

        ctx = build_ai_node_context(graph, task.node_id, [])
        assert ctx is not None
        assert "pkg" in ctx.parent_context

    def test_sibling_snippets(self) -> None:
        """Sibling YAML snippets are included."""
        graph = ContentGraph()
        play = _make_node(
            "site.yml/plays[0]",
            node_type=NodeType.PLAY,
            yaml_lines="- hosts: all\n",
        )
        t1 = _make_node(
            "site.yml/plays[0]/tasks[0]",
            yaml_lines="- name: Task one\n  debug:\n    msg: hello\n",
            line_start=3,
            line_end=5,
        )
        t2 = _make_node(
            "site.yml/plays[0]/tasks[1]",
            yaml_lines="- name: Task two\n  apt:\n    name: vim\n",
            line_start=6,
            line_end=8,
        )
        t3 = _make_node(
            "site.yml/plays[0]/tasks[2]",
            yaml_lines="- name: Task three\n  copy:\n    src: a\n    dest: b\n",
            line_start=9,
            line_end=12,
        )
        graph.add_node(play)
        graph.add_node(t1)
        graph.add_node(t2)
        graph.add_node(t3)
        graph.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS)
        graph.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS)
        graph.add_edge(play.node_id, t3.node_id, EdgeType.CONTAINS)

        ctx = build_ai_node_context(graph, t2.node_id, [], max_siblings=1)
        assert ctx is not None
        assert len(ctx.sibling_snippets) == 2
        assert "Task one" in ctx.sibling_snippets[0]
        assert "Task three" in ctx.sibling_snippets[1]


class TestContextHelpers:
    """Tests for ai_context helper functions."""

    def test_simplify_dict_truncates_long_values(self) -> None:
        """Long string values are truncated."""
        d = {"short": "hello", "long": "x" * 300}
        result = _simplify_dict(d)
        assert result["short"] == "hello"
        assert len(str(result["long"])) < 300
        assert str(result["long"]).endswith("...")

    def test_simplify_dict_recurses(self) -> None:
        """Nested dicts are recursively simplified."""
        d = {"nested": {"deep": "y" * 300}}
        result = _simplify_dict(d)
        assert isinstance(result["nested"], dict)

    def test_simplify_dict_non_dict(self) -> None:
        """Non-dict input returns empty dict."""
        assert _simplify_dict("not a dict") == {}

    def test_truncate_snippet_short(self) -> None:
        """Short snippets are returned unchanged."""
        text = "line1\nline2\n"
        assert _truncate_snippet(text, max_lines=5) == text

    def test_truncate_snippet_long(self) -> None:
        """Long snippets are truncated with a marker."""
        text = "\n".join(f"line{i}" for i in range(30)) + "\n"
        result = _truncate_snippet(text, max_lines=5)
        assert result.endswith("# ... (truncated)\n")
        assert result.count("\n") <= 6


# ---------------------------------------------------------------------------
# AINodeFix tests
# ---------------------------------------------------------------------------


class TestAINodeFix:
    """Tests for the AINodeFix dataclass."""

    def test_basic_construction(self) -> None:
        """AINodeFix is constructable with just fixed_snippet."""
        fix = AINodeFix(fixed_snippet="- name: fixed\n  ansible.builtin.apt:\n    name: nginx\n")
        assert fix.confidence == 0.85
        assert fix.rule_ids == []
        assert fix.skipped == []

    def test_with_metadata(self) -> None:
        """AINodeFix carries rule IDs and explanation."""
        fix = AINodeFix(
            fixed_snippet="fixed yaml",
            rule_ids=["M001", "L007"],
            explanation="Applied FQCN and formatting",
            confidence=0.92,
            skipped=[AISkipped(rule_id="L013", line=5, reason="complex", suggestion="add changed_when")],
        )
        assert fix.rule_ids == ["M001", "L007"]
        assert len(fix.skipped) == 1


# ---------------------------------------------------------------------------
# approve_pending with source_filter
# ---------------------------------------------------------------------------


class TestApprovePendingSourceFilter:
    """Tests for ``ContentGraph.approve_pending(source_filter=...)``."""

    def test_no_filter_approves_all(self) -> None:
        """Without source_filter, all pending entries are approved."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "transformed", source="deterministic")
        node.record_state(2, "transformed", source="ai")

        count = graph.approve_pending()
        assert count == 3
        assert all(s.approved for s in node.progression)

    def test_filter_skips_ai(self) -> None:
        """source_filter='deterministic' skips AI entries."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "transformed", source="deterministic")
        node.record_state(2, "transformed", source="ai")

        count = graph.approve_pending(source_filter="deterministic")
        assert count == 2

        scanned = node.progression[0]
        det = node.progression[1]
        ai = node.progression[2]
        assert scanned.approved is True
        assert det.approved is True
        assert ai.approved is False

    def test_filter_approves_empty_source(self) -> None:
        """Entries with empty source (scans) are approved even with a filter."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")

        count = graph.approve_pending(source_filter="deterministic")
        assert count == 1
        assert node.progression[0].approved is True


# ---------------------------------------------------------------------------
# Unified convergence loop with AI
# ---------------------------------------------------------------------------


class _MockAIProvider:
    """Mock AIProvider that returns canned AINodeFix results."""

    def __init__(self, fixes: dict[str, AINodeFix | None] | None = None) -> None:
        """Initialize mock provider.

        Args:
            fixes: Map of node_id -> AINodeFix to return.
        """
        self._fixes = fixes or {}
        self.call_count = 0
        self.calls: list[str] = []

    async def propose_node_fix(
        self,
        context: AINodeContext,
        *,
        model: str | None = None,
    ) -> AINodeFix | None:
        """Return canned fix for the requested node.

        Args:
            context: AI node context.
            model: Model identifier.

        Returns:
            Canned AINodeFix or None.
        """
        self.call_count += 1
        self.calls.append(context.node_id)
        return self._fixes.get(context.node_id)


class _L013Rule(GraphRule):
    """Mock rule that flags tasks without changed_when."""

    def __init__(self) -> None:
        """Initialize the rule."""
        super().__init__(rule_id="L013", description="Use changed_when", enabled=True, precedence=2)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Check if a task uses shell/command without changed_when.

        Args:
            graph: ContentGraph.
            node_id: Node to check.

        Returns:
            True if the node matches.
        """
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.TASK:
            return False
        return node.module in ("ansible.builtin.command", "ansible.builtin.shell") and "changed_when" not in (
            node.yaml_lines or ""
        )

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Create a violation.

        Args:
            graph: ContentGraph.
            node_id: Node to process.

        Returns:
            GraphRuleResult.
        """
        node = graph.get_node(node_id)
        if node is None:
            return None
        return GraphRuleResult(
            rule=self.get_metadata(),
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start or 0),
            detail={"message": "Use changed_when"},
        )


class TestUnifiedConvergence:
    """Tests for unified Tier 1 + Tier 2 convergence."""

    async def test_tier1_only_no_ai(self) -> None:
        """Without AI provider, only Tier 1 runs and auto-approves."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed >= 1
        assert report.ai_proposals == []
        assert all(s.approved for s in node.progression)

    async def test_ai_fires_after_tier1_exhausts(self) -> None:
        """AI is called when Tier 1 has no transforms for remaining violations."""
        graph = ContentGraph()
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines="- name: Run thing\n  ansible.builtin.command: hostname\n",
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        fixed_yaml = "- name: Run thing\n  ansible.builtin.command: hostname\n  changed_when: false\n"
        mock_ai = _MockAIProvider(
            fixes={node.node_id: AINodeFix(fixed_snippet=fixed_yaml, rule_ids=["L013"])},
        )

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        report = await engine.remediate()

        assert mock_ai.call_count >= 1
        assert len(report.ai_proposals) == 1
        assert report.ai_proposals[0].node_id == node.node_id
        assert report.ai_proposals[0].after_yaml == fixed_yaml

    async def test_ai_entries_not_auto_approved(self) -> None:
        """AI transforms remain unapproved after convergence."""
        graph = ContentGraph()
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines="- name: Run thing\n  ansible.builtin.command: hostname\n",
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        fixed_yaml = "- name: Run thing\n  ansible.builtin.command: hostname\n  changed_when: false\n"
        mock_ai = _MockAIProvider(
            fixes={node.node_id: AINodeFix(fixed_snippet=fixed_yaml, rule_ids=["L013"])},
        )

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        await engine.remediate()

        ai_entries = [s for s in node.progression if s.source == "ai"]
        assert len(ai_entries) >= 1
        assert not any(s.approved for s in ai_entries)

    async def test_ai_not_called_when_tier1_suffices(self) -> None:
        """AI is NOT called when Tier 1 fixes everything."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        mock_ai = _MockAIProvider()

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        await engine.remediate()

        assert mock_ai.call_count == 0

    async def test_max_ai_attempts_respected(self) -> None:
        """AI resubmission stops after max_ai_attempts."""
        graph = ContentGraph()
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines="- name: Run thing\n  ansible.builtin.command: hostname\n",
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        mock_ai = _MockAIProvider(fixes={})

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
            max_ai_attempts=1,
        )
        await engine.remediate()

        assert mock_ai.call_count == 1

    async def test_ai_proposal_contains_before_after(self) -> None:
        """AINodeProposal captures before and after YAML."""
        graph = ContentGraph()
        original_yaml = "- name: Run thing\n  ansible.builtin.command: hostname\n"
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines=original_yaml,
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        fixed_yaml = "- name: Run thing\n  ansible.builtin.command: hostname\n  changed_when: false\n"
        mock_ai = _MockAIProvider(
            fixes={node.node_id: AINodeFix(fixed_snippet=fixed_yaml, rule_ids=["L013"])},
        )

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        report = await engine.remediate()

        proposal = report.ai_proposals[0]
        assert proposal.before_yaml == original_yaml
        assert proposal.after_yaml == fixed_yaml
        assert "L013" in proposal.rule_ids
        assert proposal.line_start == 3
        assert proposal.line_end == 6

    async def test_ai_skip_unchanged_snippet(self) -> None:
        """AI returning unchanged YAML produces no proposal."""
        graph = ContentGraph()
        original_yaml = "- name: Run thing\n  ansible.builtin.command: hostname\n"
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines=original_yaml,
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        mock_ai = _MockAIProvider(
            fixes={node.node_id: AINodeFix(fixed_snippet=original_yaml)},
        )

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        report = await engine.remediate()

        assert len(report.ai_proposals) == 0

    async def test_ai_returns_none(self) -> None:
        """AI returning None produces no proposal."""
        graph = ContentGraph()
        node = _make_node(
            module="ansible.builtin.command",
            yaml_lines="- name: Run thing\n  ansible.builtin.command: hostname\n",
        )
        graph.add_node(node)
        rules: list[GraphRule] = [_L013Rule()]
        registry = TransformRegistry()

        mock_ai = _MockAIProvider(fixes={})

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            ai_provider=mock_ai,
        )
        report = await engine.remediate()

        assert len(report.ai_proposals) == 0


# ---------------------------------------------------------------------------
# AINodeProposal dataclass
# ---------------------------------------------------------------------------


class TestAINodeProposal:
    """Tests for the AINodeProposal dataclass."""

    def test_basic_construction(self) -> None:
        """AINodeProposal is constructable with required fields."""
        p = AINodeProposal(
            node_id="test",
            file_path="site.yml",
            before_yaml="old",
            after_yaml="new",
            rule_ids=["M001"],
            explanation="Fixed FQCN",
            confidence=0.95,
        )
        assert p.node_id == "test"
        assert p.confidence == 0.95

    def test_line_fields_default_to_zero(self) -> None:
        """Line fields default to 0 when not specified."""
        p = AINodeProposal(
            node_id="n",
            file_path="f.yml",
            before_yaml="a",
            after_yaml="b",
        )
        assert p.line_start == 0
        assert p.line_end == 0

    def test_line_fields_populated(self) -> None:
        """Line fields are stored when explicitly provided."""
        p = AINodeProposal(
            node_id="site.yml/plays[0]/tasks[0]",
            file_path="site.yml",
            before_yaml="old",
            after_yaml="new",
            line_start=10,
            line_end=15,
        )
        assert p.line_start == 10
        assert p.line_end == 15


# ---------------------------------------------------------------------------
# _build_graph_proposals proto conversion
# ---------------------------------------------------------------------------


class TestBuildGraphProposals:
    """Proto conversion preserves line info from AINodeProposal."""

    def test_line_fields_forwarded_to_proto(self) -> None:
        """Line start/end from AINodeProposal appear on the Proposal proto."""
        from apme_engine.daemon.primary_server import PrimaryServicer

        anp = AINodeProposal(
            node_id="site.yml/plays[0]/tasks[0]",
            file_path="site.yml",
            before_yaml="- name: Old\n  debug:\n    msg: hi\n",
            after_yaml="- name: New\n  debug:\n    msg: hi\n",
            rule_ids=["L013"],
            confidence=0.9,
            line_start=5,
            line_end=8,
        )
        proposals = PrimaryServicer._build_graph_proposals([anp])

        assert len(proposals) == 1
        assert proposals[0].line_start == 5
        assert proposals[0].line_end == 8
        assert proposals[0].file == "site.yml"
        assert proposals[0].tier == 2
