"""Tests for graph-aware remediation engine (ADR-044 Phase 3, PR 3).

Covers ``rescan_dirty``, ``GraphRemediationEngine``, and
``splice_modifications``.
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
    NodeScope,
    NodeType,
)
from apme_engine.graph.rule_base import (
    GraphRule,
    GraphRuleResult,
)
from apme_engine.graph.scanner import (
    graph_report_to_violations,
    rescan_dirty,
    scan,
)
from apme_engine.graph.types import RemediationResolution, RuleScope
from apme_engine.remediation.graph_engine import (
    GraphRemediationEngine,
    SpliceOutcome,
    SpliceResult,
    splice_modifications,
    splice_with_outcome,
)
from apme_engine.remediation.registry import TransformRegistry

if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap

# ---------------------------------------------------------------------------
# Helpers
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

_TASK_YAML_COPY = """\
- name: Copy file
  copy:
    src: a.txt
    dest: /tmp/a.txt
"""


def _make_node(
    node_id: str = "site.yml/plays[0]/tasks[0]",
    *,
    module: str = "apt",
    yaml_lines: str = _TASK_YAML_APT,
    file_path: str = "/workspace/site.yml",
    line_start: int = 3,
    line_end: int = 6,
) -> ContentNode:
    identity = NodeIdentity(
        path=node_id,
        node_type=NodeType.TASK,
    )
    return ContentNode(
        identity=identity,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        module=module,
        yaml_lines=yaml_lines,
    )


def _make_play_task_graph(*, task_module: str = "apt") -> tuple[ContentGraph, ContentNode, ContentNode, ContentNode]:
    """Build a playbook graph with one play containing one task.

    Args:
        task_module: Module name to assign to the task node.

    Returns:
        Tuple of ``(graph, playbook, play, task)``.
    """
    graph = ContentGraph()
    playbook = ContentNode(
        identity=NodeIdentity(path="site.yml", node_type=NodeType.PLAYBOOK),
        file_path="/workspace/site.yml",
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="/workspace/site.yml",
        line_start=1,
        scope=NodeScope.OWNED,
    )
    task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="/workspace/site.yml",
        line_start=3,
        line_end=6,
        module=task_module,
        yaml_lines=_TASK_YAML_APT if task_module == "apt" else _TASK_YAML_FQCN,
        scope=NodeScope.OWNED,
    )
    graph.add_node(playbook)
    graph.add_node(play)
    graph.add_node(task)
    graph.add_edge(playbook.node_id, play.node_id, EdgeType.CONTAINS)
    graph.add_edge(play.node_id, task.node_id, EdgeType.CONTAINS)
    return graph, playbook, play, task


class _FQCNRule(GraphRule):
    """Mock rule that flags non-FQCN module names."""

    def __init__(self) -> None:
        super().__init__(rule_id="M001", description="Use FQCN for modules", enabled=True, precedence=1)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        node = graph.get_node(node_id)
        if node is None or node.node_type != NodeType.TASK:
            return False
        return bool(node.module and "." not in (node.module or ""))

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        node = graph.get_node(node_id)
        if node is None:
            return None
        return GraphRuleResult(
            rule=self.get_metadata(),
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start or 0),
            detail={
                "message": f"Use FQCN for {node.module}",
                # Real M001 findings come from the Ansible validator with the
                # canonical target attached; FQCN remediation now refuses to
                # guess one, so the fixture has to carry it like production.
                "fqcn": f"ansible.builtin.{node.module}",
            },
        )


class _AlwaysPassRule(GraphRule):
    """Mock rule that never fires."""

    def __init__(self) -> None:
        super().__init__(rule_id="L999", description="Always passes", enabled=True, precedence=10)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        return True

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        return GraphRuleResult(verdict=False, node_id=node_id)


class _PlayAggregateRule(GraphRule):
    """Mock play-scoped rule that reflects descendant task module state."""

    def __init__(self) -> None:
        super().__init__(
            rule_id="TPLAY1",
            description="Play still contains short module names",
            enabled=True,
            precedence=5,
            scope=RuleScope.PLAY,
        )

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.PLAY

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        node = graph.get_node(node_id)
        if node is None:
            return None
        short_modules = []
        for desc_id in graph.structural_descendants(node_id):
            desc = graph.get_node(desc_id)
            if desc is None or desc.node_type != NodeType.TASK:
                continue
            if desc.module and "." not in desc.module:
                short_modules.append(desc.module)
        if not short_modules:
            return GraphRuleResult(
                rule=self.get_metadata(),
                verdict=False,
                node_id=node_id,
                file=(node.file_path, node.line_start or 0),
            )
        return GraphRuleResult(
            rule=self.get_metadata(),
            verdict=True,
            node_id=node_id,
            file=(node.file_path, node.line_start or 0),
            detail={"message": f"Play references short modules: {', '.join(sorted(short_modules))}"},
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
    reg = TransformRegistry()
    reg.register("M001", node=_fqcn_transform)
    return reg


# ---------------------------------------------------------------------------
# rescan_dirty
# ---------------------------------------------------------------------------


class TestRescanDirty:
    """Tests for ``graph_scanner.rescan_dirty``."""

    def test_only_dirty_nodes_scanned(self) -> None:
        """Only nodes in the dirty set are evaluated."""
        graph = ContentGraph()
        n1 = _make_node("site.yml/plays[0]/tasks[0]", module="apt")
        n2 = _make_node(
            "site.yml/plays[0]/tasks[1]",
            module="copy",
            yaml_lines=_TASK_YAML_COPY,
        )
        graph.add_node(n1)
        graph.add_node(n2)

        rules: list[GraphRule] = [_FQCNRule()]

        # Full scan catches both
        full = scan(graph, rules)
        full_v = graph_report_to_violations(full)
        assert len(full_v) == 2

        # Rescan only n1
        dirty = frozenset({n1.node_id})
        partial = rescan_dirty(graph, rules, dirty)
        assert partial.nodes_scanned == 1
        partial_v = graph_report_to_violations(partial)
        assert len(partial_v) == 1
        assert partial_v[0]["path"] == n1.node_id

    def test_empty_dirty_set(self) -> None:
        """An empty dirty set produces zero results."""
        graph = ContentGraph()
        graph.add_node(_make_node())
        report = rescan_dirty(graph, [_FQCNRule()], frozenset())
        assert report.nodes_scanned == 0
        assert not report.node_results

    def test_nonexistent_node_id(self) -> None:
        """Unknown node IDs are silently skipped."""
        graph = ContentGraph()
        report = rescan_dirty(graph, [_FQCNRule()], frozenset({"bogus"}))
        assert report.nodes_scanned == 0

    def test_clean_node_no_results(self) -> None:
        """A dirty node that passes all rules yields no violation results."""
        graph = ContentGraph()
        n = _make_node(module="ansible.builtin.apt", yaml_lines=_TASK_YAML_FQCN)
        graph.add_node(n)
        report = rescan_dirty(graph, [_FQCNRule()], frozenset({n.node_id}))
        assert report.nodes_scanned == 1
        assert not graph_report_to_violations(report)

    def test_play_scoped_rules_rescan_enclosing_play(self) -> None:
        """Dirty task rescans enclosing play when a play-scoped rule is enabled."""
        graph, _playbook, play, task = _make_play_task_graph(task_module="apt")

        report = rescan_dirty(graph, [_PlayAggregateRule()], frozenset({task.node_id}))

        assert report.nodes_scanned == 2
        violations = graph_report_to_violations(report)
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "TPLAY1"
        assert violations[0]["path"] == play.node_id


# ---------------------------------------------------------------------------
# GraphRemediationEngine
# ---------------------------------------------------------------------------


class TestGraphRemediationEngine:
    """Tests for ``GraphRemediationEngine.remediate``."""

    async def test_single_pass_convergence(self) -> None:
        """One fixable violation is resolved in a single pass."""
        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules, max_passes=5)
        report = await engine.remediate()

        assert report.fixed == 1
        assert report.passes >= 1
        assert not report.oscillation_detected
        assert report.nodes_modified == 1

        node = graph.get_node("site.yml/plays[0]/tasks[0]")
        assert node is not None
        assert "ansible.builtin.apt" in (node.module or "")

    async def test_already_converged(self) -> None:
        """When content is already clean, pass 1 exits immediately with zero fixes."""
        graph = ContentGraph()
        graph.add_node(_make_node(module="ansible.builtin.apt", yaml_lines=_TASK_YAML_FQCN))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed == 0
        assert report.passes == 1
        assert report.nodes_modified == 0

    async def test_initial_violations_parameter(self) -> None:
        """Supplying initial_violations skips the first scan."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        pre_violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            }
        ]
        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate(initial_violations=pre_violations)

        assert report.fixed == 1
        assert report.nodes_modified == 1

    async def test_multi_node_remediation(self) -> None:
        """Multiple nodes are fixed in the same pass."""
        graph = ContentGraph()
        graph.add_node(_make_node("site.yml/plays[0]/tasks[0]", module="apt"))
        graph.add_node(
            _make_node(
                "site.yml/plays[0]/tasks[1]",
                module="copy",
                yaml_lines=_TASK_YAML_COPY,
                line_start=7,
                line_end=10,
            )
        )
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed == 2
        assert report.nodes_modified == 2

    async def test_no_transform_available(self) -> None:
        """Violations with no registered transform are left unfixed."""
        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = TransformRegistry()  # empty — no transforms

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed == 0
        assert report.nodes_modified == 0
        assert len(report.remaining_violations) == 1

    async def test_progression_recorded(self) -> None:
        """NodeState progression is recorded during convergence."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        assert len(node.progression) >= 2
        assert node.progression[0].phase == "scanned"
        assert any(ns.phase == "transformed" for ns in node.progression)

    async def test_entries_approved_after_convergence(self) -> None:
        """All progression entries are auto-approved after convergence."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        assert all(s.approved for s in node.progression)

    async def test_interactive_skips_auto_approve_and_emits_tier1_proposals(self) -> None:
        """interactive=True leaves deterministic entries pending and builds proposals."""
        from apme_engine.remediation.graph_engine import collect_tier1_proposals

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate(interactive=True)

        assert any(not s.approved and s.source == "deterministic" for s in node.progression)
        assert report.tier1_proposals
        assert report.tier1_proposals[0].node_id == node.node_id
        assert collect_tier1_proposals(graph)
        proposed = graph.query_violations(status="pending_review")
        assert proposed
        assert all(v.get("remediation_resolution") != RemediationResolution.AI_PROPOSED for v in proposed)
        assert any(v["rule_id"] == "M001" for v in proposed)

    async def test_transform_source_deterministic(self) -> None:
        """Transformed entries have source='deterministic'."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        transformed = [s for s in node.progression if s.phase == "transformed"]
        assert len(transformed) >= 1
        assert all(s.source == "deterministic" for s in transformed)

    async def test_clean_state_after_rescan(self) -> None:
        """After convergence, ledger shows the violation as fixed."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        fixed = graph.query_violations(status="fixed")
        assert len(fixed) >= 1
        assert any(v["rule_id"] == "M001" for v in fixed)
        assert graph.collect_violations() == []

    async def test_play_scoped_violation_resolved_after_task_fix(self) -> None:
        """Aggregate play violations are resolved when descendant tasks are fixed."""
        graph, _playbook, _play, _task = _make_play_task_graph(task_module="apt")
        rules: list[GraphRule] = [_FQCNRule(), _PlayAggregateRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        fixed = graph.query_violations(status="fixed")
        assert any(v["rule_id"] == "M001" for v in fixed)
        assert any(v["rule_id"] == "TPLAY1" for v in fixed)
        assert graph.collect_violations() == []

    async def test_progress_callback(self) -> None:
        """Progress callback is invoked during remediation."""
        messages: list[str] = []

        def on_progress(phase: str, msg: str, frac: float, level: int) -> None:
            messages.append(msg)

        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules, progress_callback=on_progress)
        await engine.remediate()

        assert any("fixable" in m.lower() or "converged" in m.lower() for m in messages)

    async def test_max_passes_limit(self) -> None:
        """Engine respects max_passes even if violations remain."""

        class _InfiniteRule(GraphRule):
            """Rule that always fires (causes oscillation)."""

            def __init__(self) -> None:
                super().__init__(rule_id="T999", description="Always fires", enabled=True, precedence=1)

            def match(self, graph: ContentGraph, node_id: str) -> bool:
                return graph.get_node(node_id) is not None

            def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
                return GraphRuleResult(
                    rule=self.get_metadata(),
                    verdict=True,
                    node_id=node_id,
                    detail={"message": "always fires"},
                )

        def _noop_transform(task: CommentedMap, violation: ViolationDict) -> bool:
            task["__touched"] = True
            return True

        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        registry = TransformRegistry()
        registry.register("T999", node=_noop_transform)

        engine = GraphRemediationEngine(registry, graph, [_InfiniteRule()], max_passes=3)
        report = await engine.remediate()

        assert report.passes <= 3
        assert report.oscillation_detected

    async def test_rescan_fn_called_instead_of_builtin(self) -> None:
        """When rescan_fn is provided, it replaces the built-in rescan_dirty call."""
        rescan_calls: list[tuple[ContentGraph, frozenset[str]]] = []

        async def _custom_rescan(
            g: ContentGraph,
            dirty: frozenset[str],
        ) -> list[ViolationDict]:
            rescan_calls.append((g, dirty))
            return []

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            }
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            rescan_fn=_custom_rescan,
        )
        report = await engine.remediate(initial_violations=violations)

        assert report.fixed == 1
        assert len(rescan_calls) == 1
        captured_graph, captured_dirty = rescan_calls[0]
        assert captured_graph is graph
        assert node.node_id in captured_dirty

    async def test_rescan_fn_violations_drive_convergence(self) -> None:
        """Violations returned by rescan_fn feed back into the convergence loop."""
        pass_count = [0]

        async def _rescan_with_new_violation(
            g: ContentGraph,
            dirty: frozenset[str],
        ) -> list[ViolationDict]:
            pass_count[0] += 1
            if pass_count[0] == 1:
                return [
                    {
                        "rule_id": "M001",
                        "path": "site.yml/plays[0]/tasks[0]",
                        "file": "/workspace/site.yml",
                        "line": 3,
                        "message": "Still non-FQCN",
                        # A real remaining M001 carries the resolver target;
                        # without one the new policy (correctly) parks it for
                        # manual review and the loop stops converging.
                        "resolved_fqcn": "ansible.builtin.apt",
                        "severity": "medium",
                        "source": "native",
                        "scope": "task",
                    }
                ]
            return []

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            }
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            max_passes=5,
            rescan_fn=_rescan_with_new_violation,
        )
        report = await engine.remediate(initial_violations=violations)

        assert pass_count[0] >= 1
        assert report.passes >= 2

    async def test_rescan_fn_none_uses_builtin(self) -> None:
        """When rescan_fn is None, the built-in rescan_dirty is used."""
        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            rescan_fn=None,
        )
        report = await engine.remediate()

        assert report.fixed == 1
        assert report.nodes_modified == 1

    async def test_rescan_fn_external_violations_dont_crash(self) -> None:
        """External (non-native) violations from rescan_fn are handled gracefully.

        External violations (OPA, Ansible) have ``path`` = file_path rather
        than node_id.  They cannot be transformed (no matching transforms)
        and are not included in the graph-rules-only final scan, but they
        must not crash the convergence loop.
        """
        rescan_calls: list[frozenset[str]] = []

        async def _bridge_with_opa(
            g: ContentGraph,
            dirty: frozenset[str],
        ) -> list[ViolationDict]:
            rescan_calls.append(dirty)
            return [
                {
                    "rule_id": "P001",
                    "path": "/workspace/site.yml",
                    "file": "/workspace/site.yml",
                    "line": 3,
                    "message": "OPA policy violation",
                    "severity": "high",
                    "source": "opa",
                    "scope": "task",
                }
            ]

        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": "site.yml/plays[0]/tasks[0]",
                "file": "/workspace/site.yml",
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            }
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            rescan_fn=_bridge_with_opa,
        )
        report = await engine.remediate(initial_violations=violations)

        assert report.fixed == 1
        assert len(rescan_calls) >= 1

    async def test_violations_stored_in_ledger(self) -> None:
        """Violations are registered in the node's violation_ledger during convergence."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = TransformRegistry()  # empty — violations remain unfixed

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed == 0
        open_violations = graph.query_violations(status="open")
        assert len(open_violations) >= 1
        assert any(v["rule_id"] == "M001" for v in open_violations)

    async def test_remaining_violations_from_graph(self) -> None:
        """remaining_violations in the report come from graph.collect_violations()."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = TransformRegistry()  # empty — violations remain

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        graph_violations = graph.collect_violations()
        assert len(report.remaining_violations) == len(graph_violations)
        assert report.remaining_violations[0]["rule_id"] == graph_violations[0]["rule_id"]

    async def test_tier1_stall_falls_through_to_ai(self) -> None:
        """When Tier 1 transforms exist but all return False, the engine falls through to Tier 2 AI.

        Sets up two violations on the same node:
        - M001 (FQCN) with a registered transform that always returns False (stall)
        - L043 (jinja2) with no transform, classified Tier 2 AI candidate
        When Tier 1 stalls, the engine should invoke the AI provider for L043.
        """
        from unittest.mock import AsyncMock

        from apme_engine.remediation.ai_provider import AINodeFix

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]

        def _always_fail_transform(task: CommentedMap, violation: ViolationDict) -> bool:
            return False

        registry = TransformRegistry()
        registry.register("M001", node=_always_fail_transform)

        ai_provider = AsyncMock()
        ai_provider.propose_node_fix = AsyncMock(
            return_value=AINodeFix(
                fixed_snippet=_TASK_YAML_FQCN,
                rule_ids=["L043"],
                explanation="Fixed jinja2 spacing",
                confidence=0.95,
            ),
        )

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
            {
                "rule_id": "L043",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Jinja2 spacing",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            max_passes=5,
            ai_provider=ai_provider,
        )
        report = await engine.remediate(initial_violations=violations)

        ai_provider.propose_node_fix.assert_called()
        assert report.passes >= 1
        assert len(report.ai_proposals) >= 1
        assert not report.oscillation_detected

    async def test_tier1_stall_on_fqcn_stays_manual(self) -> None:
        """A stalled FQCN rewrite is never handed to the AI provider.

        FQCN rules are excluded from AI escalation deliberately: the model must
        not invent a module name. Even with a resolvable target and a
        registered transform that keeps failing, the finding stays for manual
        review instead of being proposed by AI.
        """
        from unittest.mock import AsyncMock

        from apme_engine.remediation.ai_provider import AINodeFix

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]

        def _always_fail_transform(task: CommentedMap, violation: ViolationDict) -> bool:
            return False

        registry = TransformRegistry()
        registry.register("M001", node=_always_fail_transform)

        ai_provider = AsyncMock()
        ai_provider.propose_node_fix = AsyncMock(
            return_value=AINodeFix(
                fixed_snippet=_TASK_YAML_FQCN,
                rule_ids=["M001"],
                explanation="Fixed FQCN via AI",
                confidence=0.9,
            ),
        )

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            max_passes=5,
            ai_provider=ai_provider,
        )
        report = await engine.remediate(initial_violations=violations)

        ai_provider.propose_node_fix.assert_not_called()
        assert report.ai_proposals == []
        assert any(v["rule_id"] == "M001" for v in report.remaining_violations)

    async def test_ai_resolved_violations_pending_until_approved(self) -> None:
        """AI-resolved violations are 'proposed', not 'fixed', until approved."""
        from unittest.mock import AsyncMock

        from apme_engine.remediation.ai_provider import AINodeFix

        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]

        registry = TransformRegistry()

        ai_provider = AsyncMock()
        ai_provider.propose_node_fix = AsyncMock(
            return_value=AINodeFix(
                fixed_snippet=_TASK_YAML_FQCN,
                rule_ids=["L043"],
                explanation="Fixed Jinja2 spacing via AI",
                confidence=0.9,
            ),
        )

        violations: list[ViolationDict] = [
            {
                "rule_id": "L043",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Fix Jinja2 spacing",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            max_passes=5,
            ai_provider=ai_provider,
        )
        report = await engine.remediate(initial_violations=violations)

        # AI fixes are "proposed" — not counted as fixed until user approves
        assert report.fixed == 0, f"expected 0 fixed (AI pending), got {report.fixed}"
        assert len(report.fixed_violations) == 0
        # "proposed" is a distinct status; open violations should be empty
        assert len(report.remaining_violations) == 0
        proposed = graph.query_violations(status="proposed")
        assert len(proposed) == 1, "violation is in proposed state"
        assert len(report.ai_proposals) >= 1

    async def test_tier1_stall_no_false_convergence(self) -> None:
        """When Tier 1 stalls and no AI provider is set, the engine does not report full convergence."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]

        def _always_fail_transform(task: CommentedMap, violation: ViolationDict) -> bool:
            return False

        registry = TransformRegistry()
        registry.register("M001", node=_always_fail_transform)

        violations: list[ViolationDict] = [
            {
                "rule_id": "M001",
                "path": node.node_id,
                "file": node.file_path,
                "line": 3,
                "message": "Use FQCN for apt",
                "resolved_fqcn": "ansible.builtin.apt",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            }
        ]
        engine = GraphRemediationEngine(
            registry,
            graph,
            rules,
            max_passes=5,
            ai_provider=None,
        )
        report = await engine.remediate(initial_violations=violations)

        assert report.fixed == 0
        assert len(report.remaining_violations) >= 1
        assert not report.oscillation_detected

    async def test_step_diffs_populated(self) -> None:
        """step_diffs captures per-progression diffs after convergence."""
        graph = ContentGraph()
        graph.add_node(_make_node(module="apt"))
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        report = await engine.remediate()

        assert report.fixed == 1
        assert len(report.step_diffs) >= 1
        step = report.step_diffs[0]
        assert step["phase"] == "transformed"
        assert isinstance(step["diff"], str)
        assert len(str(step["diff"])) > 0


# ---------------------------------------------------------------------------
# native_rules_dir
# ---------------------------------------------------------------------------


class TestNativeRulesDir:
    """Tests for ``graph_scanner.native_rules_dir``."""

    def test_returns_existing_directory(self) -> None:
        """native_rules_dir returns a path that actually exists on disk."""
        import os

        from apme_engine.graph.scanner import native_rules_dir

        path = native_rules_dir()
        assert os.path.isdir(path), f"Expected directory to exist: {path}"
        assert path.endswith(os.path.join("graph", "rules"))


# ---------------------------------------------------------------------------
# splice_modifications
# ---------------------------------------------------------------------------


class TestSpliceModifications:
    """Tests for ``splice_modifications``."""

    _ORIGINAL = """\
---
- hosts: all
  tasks:
    - name: Install nginx
      apt:
        name: nginx
        state: present
    - name: Copy file
      copy:
        src: a.txt
        dest: /tmp/a.txt
"""

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    def test_length_changing_parent_child_overlap_rejected(self, reverse: bool) -> None:
        """Conflicting snapshots cannot overwrite a child fix or create two actions.

        Args:
            reverse: Whether the child is inserted into the graph before its parent.
        """
        original = "- name: Parent\n  block:\n    - debug:\n        msg: hello\n"
        parent = _make_node("tasks.yml/tasks[0]", yaml_lines=original, line_start=1, line_end=4)
        child = _make_node(
            "tasks.yml/tasks[0]/block[0]", yaml_lines="    - debug:\n        msg: hello\n", line_start=3, line_end=4
        )
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            node.file_path = "tasks.yml"
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent.update_from_yaml(original.replace("debug:", "ansible.builtin.command: echo hello\n      args:"))
        parent.record_state(1, "transformed")
        child.update_from_yaml("    - ansible.builtin.debug:\n        msg: hello\n")
        child.record_state(1, "transformed")
        graph.approve_pending()
        with pytest.raises(ValueError, match="Conflicting remediation edits in tasks.yml at line 3"):
            splice_modifications(graph, {"tasks.yml": original})

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("extra", ["adjacent", "comment", "name"])  # type: ignore[untyped-decorator]
    def test_parent_child_overlap_reconciliation(self, reverse: bool, extra: str) -> None:
        """Merge compatible overlap and reject ambiguous structural replacement.

        Args:
            reverse: Whether the child enters the graph first.
            extra: Additional parent edit adjacent to the repeated child fix.
        """
        original = "- name: Parent\n  block:\n    - debug:\n        msg: hello\n"
        child_original = "    - debug:\n        msg: hello\n"
        child_fixed = child_original.replace("debug:", "ansible.builtin.debug:")
        parent_fixed = original.replace("debug:", "ansible.builtin.debug:")
        if extra == "adjacent":
            parent_fixed = parent_fixed.replace("msg: hello", "msg: goodbye")
        elif extra == "comment":
            parent_fixed = parent_fixed.replace("    - ansible", "    # Kept comment\n    - ansible")
        else:
            parent_fixed = parent_fixed.replace("    - ansible", "    - name: Child\n      ansible")
        parent = _make_node("tasks.yml/tasks[0]", yaml_lines=original, line_start=1, line_end=4)
        child = _make_node("tasks.yml/tasks[0]/block[0]", yaml_lines=child_original, line_start=3, line_end=4)
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            node.file_path = "tasks.yml"
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent.update_from_yaml(parent_fixed)
        parent.record_state(1, "transformed")
        child.update_from_yaml(child_fixed)
        child.record_state(1, "transformed")
        graph.approve_pending()
        if extra in {"comment", "name"}:
            # Length-changing replacements have no line-to-line correspondence
            # unless an independently proposed insertion anchors their prefix.
            with pytest.raises(ValueError, match="Conflicting remediation edits"):
                splice_modifications(graph, {"tasks.yml": original})
            return
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert len(patches) == 1
        assert patches[0].patched == parent_fixed

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize(
        "case",
        [
            "comment",
            "label",
            "repeated_words",
            "repeated_alignment",
            "suffix_same",
            "suffix_conflict",
            "prefix_same",
            "prefix_conflict",
            "duplicate_key",
            "duplicate_key_disjoint",
            "resolved_key",
            "boolean_key",
        ],
    )  # type: ignore[untyped-decorator]
    def test_overlap_keeps_source_line_identity(self, reverse: bool, case: str) -> None:
        """Executable lines cannot realign to comments, names, or repeated scalar words.

        Args:
            reverse: Whether the child enters the graph first.
            case: Overlap/provenance or shared boundary insertion scenario.
        """
        before = "    - debug: {}\n"
        child_after = before.replace("debug:", "ansible.builtin.debug:")
        parent_after = before
        compatible = case.endswith("_same")
        if case == "comment":
            parent_after = "    - ansible.builtin.command: echo ok # debug: {}\n"
        elif case == "label":
            parent_after = "    - name: debug\n      ansible.builtin.debug: {}\n"
            child_after = before.replace("debug:", "ansible.builtin.fail:")
        elif case.startswith("repeated"):
            before = '    - debug:\n        msg: "a a a"\n'
            parent_after = before.replace("a a a", "b a")
            child_after = before.replace("a a a", "a a")
            if case == "repeated_alignment":
                before = before.replace("a a a", "foo bar foo")
                parent_after = before.replace("foo bar foo", "bar foo bar")
                child_after = before.replace("foo bar foo", "baz bar foo")
        elif case.startswith("duplicate_key"):
            before = "    - debug: {}\n      when: ready\n"
            fixed = before.replace("debug:", "ansible.builtin.debug:") if case == "duplicate_key" else before
            parent_after = fixed.replace("when: ready", "changed_when: ready")
            child_after = fixed + "      changed_when: false\n"
        elif case in {"resolved_key", "boolean_key"}:
            old, replacement, addition = ("0x20", "0x10", "16") if case == "resolved_key" else ("false", "yes", "true")
            before = f"    - debug: {{}}\n      vars:\n        {old}: old\n"
            parent_after = before.replace(old, replacement)
            child_after = before + f"        {addition}: new\n"
        elif case.startswith("suffix"):
            parent_after = before.replace("debug:", "ansible.builtin.debug:") + "      when: ready\n"
            child_after = before + ("      when: ready\n" if compatible else "      when: other\n")
        else:
            before = "    - name: Child\n      debug:\n        msg: hello\n"
            parent_after = before.replace("      debug:", "      when: ready\n      ansible.builtin.debug:").replace(
                "msg: hello", "msg: goodbye"
            )
            child_after = before.replace(
                "      debug:", "      when: ready\n      debug:" if compatible else "      when: other\n      debug:"
            ).replace("msg: hello", "msg: goodbye")
        wrapper = "- name: Parent\n  block:\n"
        original = wrapper + before
        parent = _make_node(
            "tasks.yml/tasks[0]", yaml_lines=original, line_start=1, line_end=2 + len(before.splitlines())
        )
        child = _make_node(
            "tasks.yml/tasks[0]/block[0]", yaml_lines=before, line_start=3, line_end=2 + len(before.splitlines())
        )
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            node.file_path = "tasks.yml"
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent.update_from_yaml(wrapper + parent_after)
        parent.record_state(1, "transformed")
        child.update_from_yaml(child_after)
        child.record_state(1, "transformed")
        graph.approve_pending()
        if not compatible:
            with pytest.raises(ValueError, match="Conflicting remediation edits"):
                splice_modifications(graph, {"tasks.yml": original})
            return
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert len(patches) == 1
        assert patches[0].patched == wrapper + parent_after

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("comment", [False, True])  # type: ignore[untyped-decorator]
    def test_independent_sibling_boundary_edits(self, reverse: bool, comment: bool) -> None:
        """A sibling insertion stays independent of the next task's expansion.

        Args:
            reverse: Whether nodes enter the graph in reverse source order.
            comment: Whether independent additions happen to share identical text.
        """
        first = "- ansible.builtin.command: first\n"
        second = "- ansible.builtin.command: second\n"
        original = first + second
        first_after = first + ("# Boundary\n" if comment else "  changed_when: false\n")
        second_after = ("# Boundary\n" if comment else "") + "- ansible.builtin.command:\n    cmd: second\n"
        graph = ContentGraph()
        nodes = [
            _make_node("tasks.yml/tasks[0]", yaml_lines=first, file_path="tasks.yml", line_start=1, line_end=1),
            _make_node("tasks.yml/tasks[1]", yaml_lines=second, file_path="tasks.yml", line_start=2, line_end=2),
        ]
        for node in reversed(nodes) if reverse else nodes:
            graph.add_node(node)
            node.record_state(0, "scanned")
        for node, after in zip(nodes, (first_after, second_after), strict=True):
            node.update_from_yaml(after)
            node.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert len(patches) == 1
        assert patches[0].patched == first_after + second_after

    @pytest.mark.parametrize("same", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    def test_pure_sibling_insertions_preserve_ownership(self, same: bool, reverse: bool) -> None:
        """Touching sibling additions preserve both owners and source order.

        Args:
            same: Whether both sibling additions have identical comment text.
            reverse: Whether nodes enter the graph in reverse order.
        """
        first = "- ansible.builtin.debug: {}\n"
        second = "- ansible.builtin.command: second\n"
        first_addition = "# Boundary\n" if same else "  changed_when: false\n"
        second_addition = "# Boundary\n" if same else "# Second task\n"
        graph = ContentGraph()
        nodes = [
            _make_node("tasks.yml/tasks[0]", yaml_lines=first, file_path="tasks.yml", line_start=1, line_end=1),
            _make_node("tasks.yml/tasks[1]", yaml_lines=second, file_path="tasks.yml", line_start=2, line_end=2),
        ]
        for node in reversed(nodes) if reverse else nodes:
            graph.add_node(node)
            node.record_state(0, "scanned")
        for node, after in zip(nodes, (first + first_addition, second_addition + second), strict=True):
            node.update_from_yaml(after)
            node.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": first + second})
        assert patches[0].patched == first + first_addition + second_addition + second

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("expand", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("alias", [False, True, "new", "parent", "boundary"])  # type: ignore[untyped-decorator]
    def test_block_condition_and_child_expansion_are_independent(
        self, reverse: bool, expand: bool, alias: bool | str
    ) -> None:
        """A block condition does not conflict with its last child's expanded action.

        Args:
            reverse: Whether the child enters the graph before its block.
            expand: Whether the child expands its action or appends its own condition.
            alias: Whether the child references an external anchor in a file without a final newline.
        """
        child_text = "    - ansible.builtin.command: second\n"
        block = "- name: Parent\n  block:\n" + child_text
        anchor = "- ansible.builtin.debug:\n    msg: &target /tmp/done\n" if alias and alias != "parent" else ""
        offset = len(anchor.splitlines())
        original = (anchor.replace("&target ", "") if alias == "new" else anchor) + block
        if alias:
            original = original.rstrip("\n")
        parent = _make_node(
            "tasks.yml/tasks[0]",
            yaml_lines=block,
            file_path="tasks.yml",
            line_start=offset + 1,
            line_end=offset + 3,
        )
        child = _make_node(
            "tasks.yml/tasks[0]/block[0]",
            yaml_lines=child_text,
            file_path="tasks.yml",
            line_start=offset + 3,
            line_end=offset + 3,
        )
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        anchor_after = anchor + ("  changed_when: false\n" if alias == "boundary" else "")
        if alias in ("new", "boundary"):
            anchor_node = _make_node(
                "tasks.yml/anchor",
                yaml_lines=anchor.replace("&target ", "") if alias == "new" else anchor,
                file_path="tasks.yml",
                line_start=1,
                line_end=offset,
            )
            graph.add_node(anchor_node)
            anchor_node.record_state(0, "scanned")
            anchor_node.update_from_yaml(anchor_after)
            anchor_node.record_state(1, "transformed")
        parent_after = block + "  when: ready\n"
        if alias == "parent":
            parent_after = parent_after.replace("  block:\n", "  vars: {target: &target /tmp/done}\n  block:\n")
        elif alias == "boundary":
            parent_after = "# Parent\n" + parent_after
        parent.update_from_yaml(parent_after)
        parent.record_state(1, "transformed")
        child_after = (
            "    - ansible.builtin.command:\n        cmd: second\n"
            if expand
            else child_text + "      changed_when: false\n"
        )
        if alias:
            child_after += "        creates: *target\n" if expand else "      vars:\n        path: *target\n"
        child.update_from_yaml(child_after)
        child.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert patches[0].patched == anchor_after + parent_after.replace(child_text, child_after)

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("comment", [False, True])  # type: ignore[untyped-decorator]
    @pytest.mark.parametrize("gap", ["", "        # kept\n", "        cmd: echo hi\n"])  # type: ignore[untyped-decorator]
    def test_boundary_edit_cannot_be_absorbed_into_a_script(self, reverse: bool, comment: bool, gap: str) -> None:
        """Valid YAML cannot hide a separately proposed field or comment in a script scalar.

        Args:
            reverse: Whether the child enters the graph before its block.
            comment: Whether the child adds a comment instead of a mapping field.
            gap: Unchanged source between the parent's replacement and child's addition.
        """
        child_text = "    - ansible.builtin.shell:\n" + (gap if "cmd:" in gap else "        cmd: echo hi\n" + gap)
        original = "- name: Parent\n  block:\n" + child_text
        end = len(original.splitlines())
        parent = _make_node(
            "tasks.yml/tasks[0]", yaml_lines=original, file_path="tasks.yml", line_start=1, line_end=end
        )
        child = _make_node(
            "tasks.yml/tasks[0]/block[0]", yaml_lines=child_text, file_path="tasks.yml", line_start=3, line_end=end
        )
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent.update_from_yaml(
            "- name: Parent\n  block:\n    - ansible.builtin.shell: |\n"
            + (gap if "cmd:" in gap else "        echo hi\n" + gap)
            + ("" if gap else "        # comment\n")
        )
        parent.record_state(1, "transformed")
        child.update_from_yaml(
            child_text + ("        # separate comment\n" if comment else "        creates: /tmp/done\n")
        )
        child.record_state(1, "transformed")
        graph.approve_pending()
        with pytest.raises(ValueError, match="container or structural role"):
            splice_modifications(graph, {"tasks.yml": original})

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    def test_sibling_comment_cannot_be_absorbed_into_copy_content(self, reverse: bool) -> None:
        """An indented sibling comment must not become a copied file's contents.

        Args:
            reverse: Whether nodes enter the graph in reverse order.
        """
        first = "- ansible.builtin.copy:\n    content: hello\n"
        second = "- ansible.builtin.debug: {}\n"
        graph = ContentGraph()
        nodes = [
            _make_node("tasks.yml/tasks[0]", yaml_lines=first, file_path="tasks.yml", line_start=1, line_end=2),
            _make_node("tasks.yml/tasks[1]", yaml_lines=second, file_path="tasks.yml", line_start=3, line_end=3),
        ]
        for node in reversed(nodes) if reverse else nodes:
            graph.add_node(node)
            node.record_state(0, "scanned")
        after = (first.replace("content: hello", "content: |\n      hello"), "      # next task\n" + second)
        for node, text in zip(nodes, after, strict=True):
            node.update_from_yaml(text)
            node.record_state(1, "transformed")
        graph.approve_pending()
        with pytest.raises(ValueError, match="container or structural role"):
            splice_modifications(graph, {"tasks.yml": first + second})

    @pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
    def test_implicit_null_does_not_mark_the_following_field(self, reverse: bool) -> None:
        """An implicit null does not transfer roles to a separately edited field.

        Args:
            reverse: Whether nodes enter the graph in reverse order.
        """
        child_text = "    - ansible.builtin.debug: {}\n      vars:\n        flag:\n        options: {}\n"
        original = "- name: Parent\n  block:\n" + child_text
        parent = _make_node("tasks.yml/parent", yaml_lines=original, file_path="tasks.yml", line_start=1, line_end=6)
        child = _make_node("tasks.yml/child", yaml_lines=child_text, file_path="tasks.yml", line_start=3, line_end=6)
        graph = ContentGraph()
        for node in [child, parent] if reverse else [parent, child]:
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent_after = original.replace("        options:", "        extra: true\n        options:")
        parent.update_from_yaml(parent_after)
        parent.record_state(1, "transformed")
        child.update_from_yaml(child_text.replace("options: {}", "options: []"))
        child.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert patches[0].patched == parent_after.replace("options: {}", "options: []")

    def test_parent_newline_does_not_overwrite_last_child(self) -> None:
        """Snippet newline normalization preserves a last child's independent fix."""
        original = "- name: parent\n  block:\n    - name: 'child'\n      debug: {}\n"
        parent = _make_node("tasks.yml/tasks[0]", yaml_lines=original.rstrip("\n"), line_start=1, line_end=4)
        child = _make_node(
            "tasks.yml/tasks[0]/block[0]", yaml_lines="    - name: 'child'\n      debug: {}", line_start=3, line_end=4
        )
        graph = ContentGraph()
        for node in (parent, child):
            node.file_path = "tasks.yml"
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        parent.update_from_yaml(original.replace("name: parent", "name: Parent"))
        parent.record_state(1, "transformed")
        child.update_from_yaml("    - name: 'Child'\n      debug: {}\n")
        child.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert patches[0].patched == original.replace("name: parent", "name: Parent").replace("'child'", "'Child'")

    @pytest.mark.parametrize("same_edit", [False, True])  # type: ignore[untyped-decorator]
    def test_shared_child_edit_or_insertion_boundary(self, same_edit: bool) -> None:
        """Deduplicate shared edits and preserve insertions before child replacements.

        Args:
            same_edit: Whether the parent repeats the child fix instead of inserting a comment.
        """
        original = "- name: Parent\n  block:\n    - debug: {}\n"
        parent = _make_node("tasks.yml/tasks[0]", yaml_lines=original, line_start=1, line_end=3)
        child = _make_node("tasks.yml/tasks[0]/block[0]", yaml_lines="    - debug: {}\n", line_start=3, line_end=3)
        graph = ContentGraph()
        for node in (parent, child):
            node.file_path = "tasks.yml"
            graph.add_node(node)
            node.record_state(0, "scanned")
        graph.add_edge(parent.node_id, child.node_id, EdgeType.CONTAINS)
        updated = (
            original.replace("debug:", "ansible.builtin.debug:")
            if same_edit
            else original.replace("    - debug:", "    # Child task\n    - debug:")
        )
        parent.update_from_yaml(updated)
        parent.record_state(1, "transformed")
        child.update_from_yaml("    - ansible.builtin.debug: {}\n")
        child.record_state(1, "transformed")
        graph.approve_pending()
        patches = splice_modifications(graph, {"tasks.yml": original})
        assert patches[0].patched == updated.replace("- debug:", "- ansible.builtin.debug:")

    def test_no_modifications(self) -> None:
        """When no nodes are modified, no patches are produced."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        patches = splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert patches == []

    def test_single_node_splice(self) -> None:
        """A single modified node produces a correct patch."""
        graph = ContentGraph()
        node = _make_node(
            module="apt",
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        graph.add_node(node)

        node.record_state(0, "scanned")
        v: ViolationDict = {"rule_id": "M001", "path": node.node_id}
        graph.register_violations([v], 0)
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        originals = {"/workspace/site.yml": self._ORIGINAL}
        patches = splice_modifications(graph, originals)

        assert len(patches) == 1
        patch = patches[0]
        assert patch.path == "/workspace/site.yml"
        assert "ansible.builtin.apt" in patch.patched
        assert "apt:" not in patch.patched.split("ansible.builtin.apt")[0].split("\n")[-1]
        assert patch.diff
        assert "-      apt:" in patch.diff or "- apt:" in patch.diff
        assert "M001" in patch.rule_ids

    def test_multi_node_bottom_up(self) -> None:
        """Multiple nodes in the same file are spliced bottom-up."""
        from textwrap import indent

        graph = ContentGraph()
        n1 = _make_node(
            "site.yml/plays[0]/tasks[0]",
            module="apt",
            yaml_lines=indent(_TASK_YAML_APT, "    "),
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        n2 = _make_node(
            "site.yml/plays[0]/tasks[1]",
            module="copy",
            yaml_lines=indent(_TASK_YAML_COPY, "    "),
            file_path="/workspace/site.yml",
            line_start=8,
            line_end=11,
        )
        graph.add_node(n1)
        graph.add_node(n2)

        n1.record_state(0, "scanned")
        v1: ViolationDict = {"rule_id": "M001", "path": n1.node_id}
        graph.register_violations([v1], 0)
        n1.update_from_yaml(indent(_TASK_YAML_FQCN, "    "))
        n1.record_state(1, "transformed")

        n2.record_state(0, "scanned")
        v2: ViolationDict = {"rule_id": "M001", "path": n2.node_id}
        graph.register_violations([v2], 0)
        n2.update_from_yaml(indent(_TASK_YAML_COPY.replace("copy:", "ansible.builtin.copy:"), "    "))
        n2.record_state(1, "transformed")
        graph.approve_pending()

        originals = {"/workspace/site.yml": self._ORIGINAL}
        patches = splice_modifications(graph, originals)

        assert len(patches) == 1
        p = patches[0]
        assert p.patched == self._ORIGINAL.replace("      apt:", "      ansible.builtin.apt:").replace(
            "      copy:", "      ansible.builtin.copy:"
        )

    def test_missing_original_skipped(self) -> None:
        """Nodes whose file is not in originals are skipped with a warning."""
        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        patches = splice_modifications(graph, {})
        assert patches == []
        # An empty patch list alone does not imply success — the skip
        # must be surfaced on the result object.
        assert isinstance(patches, SpliceResult)
        assert patches.unpatched == ["/unknown/path.yml"]
        assert patches.unpatched_count == 1

    def test_missing_original_outcome_and_tuple(self) -> None:
        """Outcome and tuple wrappers surface the same unpatched entry."""
        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        outcome = splice_with_outcome(graph, {})
        assert isinstance(outcome, SpliceOutcome)
        assert outcome.patches == []
        assert outcome.unpatched == ["/unknown/path.yml"]
        assert outcome.unpatched_count == 1

        outcome2 = splice_with_outcome(graph, {})
        assert outcome2.patches == []
        assert outcome2.unpatched == ["/unknown/path.yml"]

    def test_empty_patches_does_not_imply_success(self) -> None:
        """`result == []` must not be read as success without .unpatched."""
        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        result = splice_modifications(graph, {})
        assert result == []  # list-equality holds for the empty result
        assert result.unpatched != []  # ...but the modification did NOT land
        assert result.unpatched_count == 1

    def test_missing_original_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """A modified node with no originals entry logs a warning (never silent).

        Args:
            caplog: Pytest log capture fixture.
        """
        import logging

        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        with caplog.at_level(logging.WARNING, logger="apme.remediation.graph"):
            patches = splice_modifications(graph, {})
        assert patches == []
        assert patches.unpatched == ["/unknown/path.yml"]
        assert patches.unpatched_count == 1
        assert any("/unknown/path.yml" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)

    def test_missing_original_strict_raises(self) -> None:
        """strict=True raises ValueError listing unpatched files."""
        import pytest

        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        with pytest.raises(ValueError, match="/unknown/path.yml"):
            splice_modifications(graph, {}, strict=True)

    def test_contributors_name_only_the_nodes_whose_edits_landed(self) -> None:
        """``contributors`` excludes a sibling whose edit is not in the patch.

        Two nodes share one file: only the first changes content. The file still
        produces a patch, so a caller keying "landed" off the patched file path
        alone would treat the unchanged sibling's edit as present too.
        """
        graph = ContentGraph()
        # The original is two four-line tasks: 1-4 and 5-8.
        landed = _make_node("site.yml/plays[0]/tasks[0]", line_start=1, line_end=4)
        untouched = _make_node("site.yml/plays[0]/tasks[1]", line_start=5, line_end=8)
        graph.add_node(landed)
        graph.add_node(untouched)
        landed.record_state(0, "scanned")
        landed.update_from_yaml(_TASK_YAML_FQCN)
        landed.record_state(1, "transformed")
        untouched.record_state(0, "scanned")
        untouched.record_state(1, "transformed")
        graph.approve_pending()

        original = _TASK_YAML_APT + _TASK_YAML_APT
        outcome = splice_with_outcome(graph, {"/workspace/site.yml": original})

        assert [patch.path for patch in outcome.patches] == ["/workspace/site.yml"]
        # Only the first task's module is qualified; the second is untouched.
        assert outcome.patches[0].patched == _TASK_YAML_FQCN + _TASK_YAML_APT
        assert outcome.contributors == {"/workspace/site.yml": {landed.node_id}}

    def test_contributors_exclude_a_node_without_a_source_edit(self) -> None:
        """A snapshot change that produces no line edit lands for no node.

        Dropping a trailing newline changes the snapshot hash but not a single
        source line, so the node must not be reported as a contributor of the
        patch a sibling produced in the same file.
        """
        graph = ContentGraph()
        edited = _make_node("site.yml/plays[0]/tasks[0]", line_start=1, line_end=4)
        newline_only = _make_node(
            "site.yml/plays[0]/tasks[1]",
            module="copy",
            yaml_lines=_TASK_YAML_COPY,
            line_start=5,
            line_end=8,
        )
        graph.add_node(edited)
        graph.add_node(newline_only)
        edited.record_state(0, "scanned")
        edited.update_from_yaml(_TASK_YAML_FQCN)
        edited.record_state(1, "transformed")
        newline_only.record_state(0, "scanned")
        newline_only.yaml_lines = _TASK_YAML_COPY.rstrip("\n")
        newline_only.record_state(1, "transformed")
        graph.approve_pending()

        original = _TASK_YAML_APT + _TASK_YAML_COPY
        outcome = splice_with_outcome(graph, {"/workspace/site.yml": original})

        assert [patch.path for patch in outcome.patches] == ["/workspace/site.yml"]
        assert outcome.contributors == {"/workspace/site.yml": {edited.node_id}}

    def test_contributors_omit_a_file_that_normalises_to_its_original(self) -> None:
        """An edit set that leaves the bytes unchanged lands for no node."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "transformed")  # no content change at all
        graph.approve_pending()

        outcome = splice_with_outcome(graph, {"/workspace/site.yml": _TASK_YAML_APT})

        assert outcome.patches == []
        assert outcome.contributors == {}

    def test_baseline_less_strict_raises(self) -> None:
        """strict=True raises for legacy nodes without a recorded baseline."""
        import pytest

        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()
        node._baseline = None  # simulate a legacy payload predating baseline tracking

        with pytest.raises(ValueError, match="/workspace/site.yml"):
            splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL}, strict=True)

    def test_baseline_less_non_strict_splices_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-strict keeps the best-effort progression[0] fallback for legacy nodes.

        Args:
            caplog: Pytest log capture fixture.

        Returns:
            None.
        """
        import logging

        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()
        node._baseline = None  # simulate a legacy payload predating baseline tracking

        with caplog.at_level(logging.WARNING, logger="apme.remediation.graph"):
            patches = splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert len(patches) == 1
        assert "ansible.builtin.apt" in patches[0].patched
        # The fallback diff may be truncated: the file must ALSO be
        # flagged so ApprovalAck never reports unpatched_count == 0.
        assert patches.unpatched == ["/workspace/site.yml"]
        assert patches.unpatched_count == 1
        assert any(
            "approximate baseline" in r.getMessage() and "/workspace/site.yml" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )

    def test_baseline_less_outcome_and_tuple_flag_approximate(self) -> None:
        """Outcome and tuple wrappers flag the approximate-baseline file too."""
        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()
        node._baseline = None  # simulate a legacy payload predating baseline tracking

        outcome = splice_with_outcome(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert len(outcome.patches) == 1
        assert outcome.unpatched == ["/workspace/site.yml"]
        assert outcome.unpatched_count == 1

        outcome2 = splice_with_outcome(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert len(outcome2.patches) == 1
        assert outcome2.unpatched == ["/workspace/site.yml"]

    def test_baseline_less_strict_outcome_raises(self) -> None:
        """strict=True raises through the outcome core as well."""
        import pytest

        graph = ContentGraph()
        node = _make_node()
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()
        node._baseline = None  # simulate a legacy payload predating baseline tracking

        with pytest.raises(ValueError, match="/workspace/site.yml"):
            splice_with_outcome(graph, {"/workspace/site.yml": self._ORIGINAL}, strict=True)
        with pytest.raises(ValueError, match="/workspace/site.yml"):
            splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL}, strict=True)

    def test_unchanged_content_no_patch(self) -> None:
        """If progression exists but content hash is unchanged, no patch."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)

        node.record_state(0, "scanned")
        node.record_state(1, "scanned")

        patches = splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert patches == []

    def test_unchanged_legacy_content_does_not_fail_strict_splice(self) -> None:
        """Strict mode rejects changed legacy nodes, not unchanged content."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.record_state(1, "scanned")
        node._baseline = None  # simulate a legacy payload without baseline tracking

        outcome = splice_with_outcome(
            graph,
            {"/workspace/site.yml": self._ORIGINAL},
            strict=True,
        )

        assert outcome.patches == []
        assert outcome.unpatched == []

    def test_unapproved_entries_not_spliced(self) -> None:
        """Unapproved progression entries are ignored by splice."""
        graph = ContentGraph()
        node = _make_node(
            module="apt",
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        graph.add_node(node)

        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed", source="deterministic")
        # Do NOT approve — entries remain pending

        originals = {"/workspace/site.yml": self._ORIGINAL}
        patches = splice_modifications(graph, originals)

        # With no approved entries, last_approved falls back to
        # progression[0] (original), so hash matches → no patch
        assert patches == []

    def test_splice_uses_last_approved_not_latest(self) -> None:
        """splice_modifications uses the last approved entry, not the latest."""
        graph = ContentGraph()
        node = _make_node(
            module="apt",
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        graph.add_node(node)

        # Original → deterministic fix → AI fix
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed", source="deterministic")

        # Approve the deterministic entry only
        graph.approve_pending()

        # Now add an unapproved AI entry with different content
        ai_yaml = "- name: AI modified\n  ansible.builtin.apt:\n    name: nginx\n    state: latest\n"
        node.update_from_yaml(ai_yaml)
        node.record_state(2, "ai_transformed", source="ai")

        originals = {"/workspace/site.yml": self._ORIGINAL}
        patches = splice_modifications(graph, originals)

        assert len(patches) == 1
        # Patch should use the deterministic fix, not the AI fix
        assert "ansible.builtin.apt" in patches[0].patched
        assert "state: latest" not in patches[0].patched

    def test_include_pending_uses_latest(self) -> None:
        """include_pending=True uses the latest entry even if unapproved."""
        graph = ContentGraph()
        node = _make_node(
            module="apt",
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        graph.add_node(node)

        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed", source="deterministic")
        # No approve_pending — entries are still pending

        originals = {"/workspace/site.yml": self._ORIGINAL}

        # Default: no patch (pending entries ignored)
        assert splice_modifications(graph, originals) == []

        # include_pending: uses latest (pending) entry
        patches = splice_modifications(graph, originals, include_pending=True)
        assert len(patches) == 1
        assert "ansible.builtin.apt" in patches[0].patched


# ---------------------------------------------------------------------------
# Tests: Node-native L057 lookup (Ansible validator)
# ---------------------------------------------------------------------------


class TestAnsibleNodeLookup:
    """Tests for ``build_node_lookup`` and ``resolve_file_line_to_node``."""

    def test_build_lookup_from_graph_data(self) -> None:
        """Lookup table is built correctly from serialized ContentGraph."""
        import json

        from apme_engine.validators.ansible import build_node_lookup

        graph_data = json.dumps(
            {
                "version": 1,
                "nodes": [
                    {
                        "id": "play.yml/tasks[0]",
                        "data": {"file_path": "play.yml", "line_start": 4, "line_end": 7},
                    },
                    {
                        "id": "play.yml/tasks[1]",
                        "data": {"file_path": "play.yml", "line_start": 8, "line_end": 12},
                    },
                ],
                "edges": [],
            }
        ).encode()

        lookup = build_node_lookup(graph_data)
        assert "play.yml" in lookup
        assert len(lookup["play.yml"]) == 2

    def test_resolve_exact_match(self) -> None:
        """Line within a node range resolves to that node."""
        from apme_engine.validators.ansible import resolve_file_line_to_node

        lookup = {"play.yml": [(4, 7, "node-A"), (8, 12, "node-B")]}
        assert resolve_file_line_to_node(lookup, "play.yml", 5) == "node-A"
        assert resolve_file_line_to_node(lookup, "play.yml", 10) == "node-B"

    def test_resolve_narrowest_wins(self) -> None:
        """Overlapping ranges resolve to the narrower node."""
        from apme_engine.validators.ansible import resolve_file_line_to_node

        lookup = {"play.yml": [(1, 20, "outer"), (5, 8, "inner")]}
        assert resolve_file_line_to_node(lookup, "play.yml", 6) == "inner"

    def test_resolve_no_match(self) -> None:
        """Line outside all ranges returns empty string."""
        from apme_engine.validators.ansible import resolve_file_line_to_node

        lookup = {"play.yml": [(4, 7, "node-A")]}
        assert resolve_file_line_to_node(lookup, "play.yml", 99) == ""

    def test_resolve_wrong_file(self) -> None:
        """Missing file returns empty string."""
        from apme_engine.validators.ansible import resolve_file_line_to_node

        lookup = {"play.yml": [(4, 7, "node-A")]}
        assert resolve_file_line_to_node(lookup, "other.yml", 5) == ""

    def test_empty_graph_data(self) -> None:
        """Empty input produces empty lookup."""
        from apme_engine.validators.ansible import build_node_lookup

        assert build_node_lookup(b"") == {}
        assert build_node_lookup(b"{}") == {}


# ---------------------------------------------------------------------------
# Graph engine per-node cancellation (N23)
# ---------------------------------------------------------------------------


class TestGraphEngineCancellation:
    """CancelledError propagates; RuntimeError is captured, others complete."""

    async def test_cancelled_error_propagates(self) -> None:
        """A per-node CancelledError aborts the AI phase instead of being swallowed."""
        import asyncio

        from apme_engine.remediation.ai_context import AINodeContext
        from apme_engine.remediation.ai_provider import AINodeFix

        graph = ContentGraph()
        graph.add_node(_make_node("site.yml/plays[0]/tasks[0]", module="ansible.builtin.command"))
        graph.add_node(
            _make_node(
                "site.yml/plays[0]/tasks[1]",
                module="ansible.builtin.command",
                yaml_lines="- name: Other\n  ansible.builtin.command: whoami\n",
                line_start=7,
                line_end=10,
            )
        )

        class _CancellingProvider:
            async def propose_node_fix(self, context: AINodeContext, **_kw: object) -> AINodeFix | None:
                if context.node_id.endswith("tasks[0]"):
                    raise asyncio.CancelledError
                return AINodeFix(
                    fixed_snippet="- name: Other\n  ansible.builtin.command: whoami\n  changed_when: false\n",
                    rule_ids=["L013"],
                )

        engine = GraphRemediationEngine(
            TransformRegistry(),
            graph,
            [],
            ai_provider=_CancellingProvider(),
        )
        tier2: list[ViolationDict] = [
            {
                "rule_id": "L013",
                "path": "site.yml/plays[0]/tasks[0]",
                "file": "/workspace/site.yml",
                "line": 3,
                "message": "Use changed_when",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
            {
                "rule_id": "L013",
                "path": "site.yml/plays[0]/tasks[1]",
                "file": "/workspace/site.yml",
                "line": 7,
                "message": "Use changed_when",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
        ]
        with pytest.raises(asyncio.CancelledError):
            await engine._apply_ai_transforms(graph, tier2, 1, {})

    async def test_runtime_error_captured_others_complete(self) -> None:
        """A per-node RuntimeError is captured; sibling nodes still get fixes."""
        from apme_engine.remediation.ai_context import AINodeContext
        from apme_engine.remediation.ai_provider import AINodeFix

        graph = ContentGraph()
        graph.add_node(_make_node("site.yml/plays[0]/tasks[0]", module="ansible.builtin.command"))
        good_yaml = "- name: Other\n  ansible.builtin.command: whoami\n  changed_when: false\n"
        graph.add_node(
            _make_node(
                "site.yml/plays[0]/tasks[1]",
                module="ansible.builtin.command",
                yaml_lines="- name: Other\n  ansible.builtin.command: whoami\n",
                line_start=7,
                line_end=10,
            )
        )

        class _FlakyProvider:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def propose_node_fix(self, context: AINodeContext, **_kw: object) -> AINodeFix | None:
                self.calls.append(context.node_id)
                if context.node_id.endswith("tasks[0]"):
                    raise RuntimeError("provider boom")
                return AINodeFix(fixed_snippet=good_yaml, rule_ids=["L013"])

        provider = _FlakyProvider()
        engine = GraphRemediationEngine(
            TransformRegistry(),
            graph,
            [],
            ai_provider=provider,
        )
        tier2: list[ViolationDict] = [
            {
                "rule_id": "L013",
                "path": "site.yml/plays[0]/tasks[0]",
                "file": "/workspace/site.yml",
                "line": 3,
                "message": "Use changed_when",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
            {
                "rule_id": "L013",
                "path": "site.yml/plays[0]/tasks[1]",
                "file": "/workspace/site.yml",
                "line": 7,
                "message": "Use changed_when",
                "severity": "medium",
                "source": "native",
                "scope": "task",
            },
        ]
        proposals = await engine._apply_ai_transforms(graph, tier2, 1, {})
        # Failing node yields no proposal; healthy sibling completes.
        assert sorted(provider.calls) == [
            "site.yml/plays[0]/tasks[0]",
            "site.yml/plays[0]/tasks[1]",
        ]
        assert [p.node_id for p in proposals] == ["site.yml/plays[0]/tasks[1]"]
        node = graph.get_node("site.yml/plays[0]/tasks[1]")
        assert node is not None
        assert "changed_when" in (node.yaml_lines or "")


def test_splicing_retains_early_fixes_after_progression_rollover() -> None:
    """Bounded snapshots retain the source baseline and all approved edits."""
    original = "- name: original\n  debug:\n    msg: hello\n"
    graph = ContentGraph()
    node = _make_node(yaml_lines=original, file_path="tasks.yml", line_start=1, line_end=3)
    graph.add_node(node)
    baseline = node.record_state(0, "scanned")
    node.update_from_yaml(original.replace("original", "Original"))
    node.record_state(1, "transformed", source="deterministic")
    for pass_number in range(2, node.MAX_PROGRESSION + 3):
        node.record_state(pass_number, "scanned")
    node.update_from_yaml(node.yaml_lines.replace("debug:", "ansible.builtin.debug:"))
    node.record_state(node.MAX_PROGRESSION + 3, "transformed", source="deterministic")
    graph.approve_pending()

    assert len(node.progression) == node.MAX_PROGRESSION
    assert node.progression[0].id == baseline.id
    assert node.progression[0].yaml_lines == original
    patches = splice_modifications(graph, {"tasks.yml": original})
    assert len(patches) == 1
    assert patches[0].patched == original.replace("original", "Original").replace("debug:", "ansible.builtin.debug:")


def test_splicing_retains_last_approved_snapshot_amid_pending_rollover() -> None:
    """Pending history cannot evict an approved fix or leak unapproved bytes."""
    original = "- name: original\n  debug: {}\n"
    graph = ContentGraph()
    node = _make_node(yaml_lines=original, file_path="tasks.yml", line_start=1, line_end=2)
    graph.add_node(node)
    node.record_state(0, "scanned")
    approved_text = original.replace("original", "Original")
    node.update_from_yaml(approved_text)
    node.record_state(1, "transformed", source="deterministic")
    graph.approve_pending()
    baseline, approved = node.progression
    for pass_number in range(2, node.MAX_PROGRESSION * 3):
        node.update_from_yaml(approved_text.replace("debug:", f"pending_{pass_number}:"))
        node.record_state(pass_number, "transformed", source="ai")

    assert len(node.progression) == node.MAX_PROGRESSION
    assert node.progression[0] is baseline
    assert any(entry is approved for entry in node.progression)
    patches = splice_modifications(graph, {"tasks.yml": original})
    assert len(patches) == 1
    assert patches[0].patched == approved_text
    graph.reject_node(node.node_id)
    assert node.yaml_lines == approved_text
