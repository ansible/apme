"""Tests for graph-aware remediation engine (ADR-044 Phase 3, PR 3).

Covers ``rescan_dirty``, ``GraphRemediationEngine``, and
``splice_modifications``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apme_engine.engine.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeType,
)
from apme_engine.engine.graph_scanner import (
    graph_report_to_violations,
    rescan_dirty,
    scan,
)
from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.graph_engine import (
    GraphRemediationEngine,
    splice_modifications,
)
from apme_engine.remediation.registry import TransformRegistry
from apme_engine.validators.native.rules.graph_rule_base import (
    GraphRule,
    GraphRuleResult,
)

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
            detail={"message": f"Use FQCN for {node.module}"},
        )


class _AlwaysPassRule(GraphRule):
    """Mock rule that never fires."""

    def __init__(self) -> None:
        super().__init__(rule_id="L999", description="Always passes", enabled=True, precedence=10)

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        return True

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        return GraphRuleResult(verdict=False, node_id=node_id)


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
        """Dirty nodes confirmed clean after rescan get an empty-violations scanned entry."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)
        rules: list[GraphRule] = [_FQCNRule()]
        registry = _build_registry_with_fqcn()

        engine = GraphRemediationEngine(registry, graph, rules)
        await engine.remediate()

        scanned_states = [ns for ns in node.progression if ns.phase == "scanned"]
        assert len(scanned_states) >= 2
        # First scanned state has the violation
        assert "M001" in scanned_states[0].violations
        # Final scanned state confirms clean (empty violations)
        assert scanned_states[-1].violations == ()

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


# ---------------------------------------------------------------------------
# native_rules_dir
# ---------------------------------------------------------------------------


class TestNativeRulesDir:
    """Tests for ``graph_scanner.native_rules_dir``."""

    def test_returns_existing_directory(self) -> None:
        """native_rules_dir returns a path that actually exists on disk."""
        import os

        from apme_engine.engine.graph_scanner import native_rules_dir

        path = native_rules_dir()
        assert os.path.isdir(path), f"Expected directory to exist: {path}"
        assert path.endswith(os.path.join("validators", "native", "rules"))


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

        # Record initial state, transform, record transformed state
        node.record_state(0, "scanned", ("M001",))
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
        graph = ContentGraph()
        n1 = _make_node(
            "site.yml/plays[0]/tasks[0]",
            module="apt",
            file_path="/workspace/site.yml",
            line_start=4,
            line_end=7,
        )
        n2 = _make_node(
            "site.yml/plays[0]/tasks[1]",
            module="copy",
            yaml_lines=_TASK_YAML_COPY,
            file_path="/workspace/site.yml",
            line_start=8,
            line_end=11,
        )
        graph.add_node(n1)
        graph.add_node(n2)

        # Mark both as modified
        n1.record_state(0, "scanned", ("M001",))
        n1.update_from_yaml(_TASK_YAML_FQCN)
        n1.record_state(1, "transformed")

        n2.record_state(0, "scanned", ("M001",))
        n2.update_from_yaml("- name: Copy file\n  ansible.builtin.copy:\n    src: a.txt\n    dest: /tmp/a.txt\n")
        n2.record_state(1, "transformed")
        graph.approve_pending()

        originals = {"/workspace/site.yml": self._ORIGINAL}
        patches = splice_modifications(graph, originals)

        assert len(patches) == 1
        p = patches[0]
        assert "ansible.builtin.apt" in p.patched
        assert "ansible.builtin.copy" in p.patched

    def test_missing_original_skipped(self) -> None:
        """Nodes whose file is not in originals are silently skipped."""
        graph = ContentGraph()
        node = _make_node(file_path="/unknown/path.yml")
        graph.add_node(node)
        node.record_state(0, "scanned")
        node.update_from_yaml(_TASK_YAML_FQCN)
        node.record_state(1, "transformed")
        graph.approve_pending()

        patches = splice_modifications(graph, {})
        assert patches == []

    def test_unchanged_content_no_patch(self) -> None:
        """If progression exists but content hash is unchanged, no patch."""
        graph = ContentGraph()
        node = _make_node(module="apt")
        graph.add_node(node)

        # Record state twice but don't change content
        node.record_state(0, "scanned", ("M001",))
        node.record_state(1, "scanned")

        patches = splice_modifications(graph, {"/workspace/site.yml": self._ORIGINAL})
        assert patches == []

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

        node.record_state(0, "scanned", ("M001",))
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
        node.record_state(0, "scanned", ("M001",))
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

        node.record_state(0, "scanned", ("M001",))
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
