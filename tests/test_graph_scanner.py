"""Tests for ContentGraphScanner (ADR-044 Phase 2A)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

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
from apme_engine.graph.rules.R402_list_all_used_variables_graph import ListAllUsedVariablesGraphRule
from apme_engine.graph.scanner import (
    GraphScanReport,
    expand_dirty_node_ids,
    load_graph_rules,
    native_rules_dir,
    scan,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph() -> ContentGraph:
    """Build a minimal graph with a playbook, play, and two tasks.

    Returns:
        A ``ContentGraph`` with playbook, play, and two owned task nodes.
    """
    g = ContentGraph()
    pb = ContentNode(
        identity=NodeIdentity("site.yml", NodeType.PLAYBOOK),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    play = ContentNode(
        identity=NodeIdentity("site.yml::play[0]", NodeType.PLAY),
        file_path="site.yml",
        line_start=1,
        become={"become": True, "become_user": "root"},
        scope=NodeScope.OWNED,
    )
    t1 = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[0]", NodeType.TASK),
        file_path="site.yml",
        line_start=5,
        name="Install package",
        module="ansible.builtin.yum",
        become={"become": True, "become_user": "root"},
        scope=NodeScope.OWNED,
    )
    t2 = ContentNode(
        identity=NodeIdentity("site.yml::play[0]/tasks[1]", NodeType.TASK),
        file_path="site.yml",
        line_start=10,
        name="Copy config",
        module="ansible.builtin.copy",
        scope=NodeScope.OWNED,
    )
    g.add_node(pb)
    g.add_node(play)
    g.add_node(t1)
    g.add_node(t2)
    g.add_edge(pb.node_id, play.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, t1.node_id, EdgeType.CONTAINS)
    g.add_edge(play.node_id, t2.node_id, EdgeType.CONTAINS)
    return g


@dataclass
class _MatchAllTasksRule(GraphRule):
    """Test rule that matches and flags every task node.

    Attributes:
        rule_id: Rule identifier.
        description: Human-readable rule description.
        enabled: Whether the rule participates in scanning.
    """

    rule_id: str = "TEST001"
    description: str = "Test rule matching all tasks"
    enabled: bool = True

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match all task nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node exists and is a task.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.TASK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Flag every task.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A passing ``GraphRuleResult`` for the given node.
        """
        return GraphRuleResult(verdict=True, node_id=node_id)


@dataclass
class _DisabledRule(GraphRule):
    """Test rule that is disabled and should never fire.

    Attributes:
        rule_id: Rule identifier.
        description: Human-readable rule description.
        enabled: Whether the rule participates in scanning.
    """

    rule_id: str = "TEST002"
    description: str = "Disabled rule"
    enabled: bool = False

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Never reached when disabled.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True (unused when the rule is disabled).
        """
        return True  # pragma: no cover

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Never reached when disabled.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            A passing ``GraphRuleResult`` (unused when the rule is disabled).
        """
        return GraphRuleResult(verdict=True, node_id=node_id)  # pragma: no cover


@dataclass
class _ErrorRule(GraphRule):
    """Test rule that raises during process.

    Attributes:
        rule_id: Rule identifier.
        description: Human-readable rule description.
        enabled: Whether the rule participates in scanning.
    """

    rule_id: str = "TEST003"
    description: str = "Error rule"
    enabled: bool = True

    def match(self, graph: ContentGraph, node_id: str) -> bool:
        """Match all task nodes.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to check.

        Returns:
            True if the node exists and is a task.
        """
        node = graph.get_node(node_id)
        return node is not None and node.node_type == NodeType.TASK

    def process(self, graph: ContentGraph, node_id: str) -> GraphRuleResult | None:
        """Raise to test error handling.

        Args:
            graph: The full ContentGraph.
            node_id: ID of the node to evaluate.

        Returns:
            Never returns normally.

        Raises:
            RuntimeError: Always, with a fixed test message.
        """
        msg = "intentional test error"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGraphScanner:
    """Tests for the ContentGraphScanner ``scan`` function."""

    def test_scan_finds_matching_nodes(self) -> None:
        """Verify scan produces results for nodes matching the rule."""
        graph = _make_graph()
        rules: list[GraphRule] = [_MatchAllTasksRule()]
        report = scan(graph, rules)

        assert isinstance(report, GraphScanReport)
        assert report.rules_evaluated == 1
        assert report.nodes_scanned > 0

        flagged_node_ids = {r.node_id for nr in report.node_results for r in nr.rule_results}
        assert "site.yml::play[0]/tasks[0]" in flagged_node_ids
        assert "site.yml::play[0]/tasks[1]" in flagged_node_ids

    def test_scan_skips_disabled_rules(self) -> None:
        """Verify disabled rules produce no results."""
        graph = _make_graph()
        rules: list[GraphRule] = [_DisabledRule()]
        report = scan(graph, rules)

        assert len(report.node_results) == 0

    def test_scan_handles_rule_errors_gracefully(self) -> None:
        """Verify rule exceptions are caught and recorded as error results."""
        graph = _make_graph()
        rules: list[GraphRule] = [_ErrorRule()]
        report = scan(graph, rules)

        error_results = [r for nr in report.node_results for r in nr.rule_results if r.error is not None]
        assert len(error_results) > 0
        assert "intentional test error" in error_results[0].error  # type: ignore[operator]

    def test_scan_respects_owned_only(self) -> None:
        """Verify owned_only=True skips REFERENCED nodes."""
        graph = _make_graph()
        ext_task = ContentNode(
            identity=NodeIdentity("ext.yml::task[0]", NodeType.TASK),
            file_path="ext.yml",
            scope=NodeScope.REFERENCED,
        )
        graph.add_node(ext_task)

        rules: list[GraphRule] = [_MatchAllTasksRule()]
        report_owned = scan(graph, rules, owned_only=True)
        report_all = scan(graph, rules, owned_only=False)

        owned_ids = {r.node_id for nr in report_owned.node_results for r in nr.rule_results}
        all_ids = {r.node_id for nr in report_all.node_results for r in nr.rule_results}

        assert "ext.yml::task[0]" not in owned_ids
        assert "ext.yml::task[0]" in all_ids

    def test_scan_populates_timing(self) -> None:
        """Verify elapsed_ms is populated after scan."""
        graph = _make_graph()
        rules: list[GraphRule] = [_MatchAllTasksRule()]
        report = scan(graph, rules)
        assert report.elapsed_ms >= 0

    def test_scan_empty_graph(self) -> None:
        """Verify empty graph produces empty report."""
        graph = ContentGraph()
        rules: list[GraphRule] = [_MatchAllTasksRule()]
        report = scan(graph, rules)
        assert report.nodes_scanned == 0
        assert len(report.node_results) == 0

    def test_scan_no_rules(self) -> None:
        """Verify scan with no rules produces no results."""
        graph = _make_graph()
        report = scan(graph, [])
        assert report.rules_evaluated == 0
        assert len(report.node_results) == 0


class TestLoadGraphRules:
    """Tests for the graph rule loader."""

    def test_empty_dir_returns_empty(self) -> None:
        """Verify empty rules_dir returns empty list."""
        rules, _ = load_graph_rules(rules_dir="")
        assert rules == []

    def test_nonexistent_dir_returns_empty(self) -> None:
        """Verify non-existent directory is skipped."""
        rules, _ = load_graph_rules(rules_dir="/nonexistent/path")
        assert rules == []

    def test_all_graph_rules_load_without_errors(self) -> None:
        """All native rule modules must load without import errors.

        ``load_classes_in_dir`` imports every ``.py`` file in the rules
        directory (excluding ``_test.py``).  This is intentionally broader
        than just ``*_graph.py`` files so that helper modules and shared
        infrastructure are also validated.

        Regression test for Python 3.14 dataclass loading bug: load_classes_in_dir
        must register modules in sys.modules before exec_module so @dataclass
        can resolve cls.__module__.
        """
        from pathlib import Path

        import apme_engine.graph.rules as rules_pkg
        from apme_engine.engine.utils import load_classes_in_dir
        from apme_engine.graph.rule_base import (
            GraphRule as GraphRuleBase,
        )

        rules_dir = Path(rules_pkg.__file__).parent
        graph_files = list(rules_dir.glob("*_graph.py"))
        assert graph_files, "Expected at least one *_graph.py file"

        classes, errors = load_classes_in_dir(
            str(rules_dir),
            GraphRuleBase,
            only_subclass=True,
            fail_on_error=False,
        )
        assert errors == [], f"Graph rule load errors: {errors}"
        assert len(classes) >= len(graph_files), f"Loaded {len(classes)} rules from {len(graph_files)} *_graph.py files"

    def test_default_load_skips_disabled_by_default_rules(self) -> None:
        """Disabled-by-default rules are omitted when no rule_id_list is given."""
        rules_dir = native_rules_dir()
        rule_ids = {r.rule_id for r in load_graph_rules(rules_dir=rules_dir)[0]}
        assert "R402" not in rule_ids
        assert "R404" not in rule_ids

    def test_graph_rule_opt_in_from_rule_configs(self) -> None:
        """RuleConfig enabled flags map to native graph opt-in IDs."""
        from apme.v1.engine_pb2 import RuleConfig
        from apme_engine.graph.scanner import graph_rule_opt_in_from_rule_configs

        configs = [
            RuleConfig(rule_id="R402", enabled=True),
            RuleConfig(rule_id="L039", enabled=True),
            RuleConfig(rule_id="R404", enabled=False),
        ]
        assert graph_rule_opt_in_from_rule_configs(configs) == ["R402"]

    def test_rule_id_list_opt_in_loads_r402(self) -> None:
        """Explicit rule_id_list opt-in loads disabled-by-default R402."""
        rules_dir = native_rules_dir()
        rules, _ = load_graph_rules(rules_dir=rules_dir, rule_id_list=["R402"])
        rule_ids = {r.rule_id for r in rules}
        assert rule_ids == {"R402"}
        assert all(r.enabled is True for r in rules)

    def test_opt_in_rule_ids_enable_r402_without_whitelist(self) -> None:
        """opt_in_rule_ids loads R402 while keeping other enabled rules."""
        rules_dir = native_rules_dir()
        default_ids = {r.rule_id for r in load_graph_rules(rules_dir=rules_dir)[0]}
        rules, _ = load_graph_rules(rules_dir=rules_dir, opt_in_rule_ids=["R402"])
        rule_ids = {r.rule_id for r in rules}
        assert "R402" in rule_ids
        assert "R404" not in rule_ids
        assert default_ids.issubset(rule_ids)
        assert all(r.enabled for r in rules if r.rule_id == "R402")

    def test_preserve_disabled_defaults_keeps_r402_catalog_enabled_false(self) -> None:
        """Catalog registration loads R402 without flipping enabled=True."""
        rules_dir = native_rules_dir()
        rules, _ = load_graph_rules(
            rules_dir=rules_dir,
            opt_in_rule_ids=["R402"],
            preserve_disabled_defaults=True,
        )
        by_id = {r.rule_id: r for r in rules}
        assert "R402" in by_id
        assert by_id["R402"].enabled is False

    def test_rule_id_list_warns_on_missing_rule(self, caplog: pytest.LogCaptureFixture) -> None:
        """Requested rule IDs that fail to load emit a warning.

        Args:
            caplog: Pytest log capture fixture.
        """
        rules_dir = native_rules_dir()
        with caplog.at_level("WARNING"):
            rules, missing = load_graph_rules(rules_dir=rules_dir, rule_id_list=["R402", "ZZ999"])
        assert {r.rule_id for r in rules} == {"R402"}
        assert missing == ["ZZ999"]
        assert any("ZZ999" in rec.message for rec in caplog.records)

    def test_scan_report_carries_missing_requested_rules(self) -> None:
        """scan() surfaces rule IDs that were requested but not loaded."""
        rules_dir = native_rules_dir()
        rules, missing = load_graph_rules(rules_dir=rules_dir, rule_id_list=["R402", "ZZ999"])
        g = ContentGraph()
        report = scan(g, rules, missing_requested_rule_ids=missing)
        assert report.missing_requested_rule_ids == ["ZZ999"]


def test_expand_dirty_node_ids_includes_play_via_include_edge() -> None:
    """Dirty included tasks expand to enclosing play for play-scoped rules."""
    g = ContentGraph()
    play = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]", node_type=NodeType.PLAY),
        file_path="site.yml",
        scope=NodeScope.OWNED,
    )
    include_task = ContentNode(
        identity=NodeIdentity(path="site.yml/plays[0]/tasks[0]", node_type=NodeType.TASK),
        file_path="site.yml",
        module="ansible.builtin.include_tasks",
        module_options={"file": "included.yml"},
        scope=NodeScope.OWNED,
    )
    included_task = ContentNode(
        identity=NodeIdentity(path="included.yml/tasks[0]", node_type=NodeType.TASK),
        file_path="included.yml",
        module="ansible.builtin.debug",
        module_options={"msg": "{{ included_var }}"},
        scope=NodeScope.OWNED,
    )
    g.add_node(play)
    g.add_node(include_task)
    g.add_node(included_task)
    g.add_edge(play.node_id, include_task.node_id, EdgeType.CONTAINS)
    g.add_edge(include_task.node_id, included_task.node_id, EdgeType.INCLUDE)

    expanded = expand_dirty_node_ids(
        g,
        [ListAllUsedVariablesGraphRule(enabled=True)],
        frozenset({included_task.node_id}),
    )
    assert play.node_id in expanded


class TestRuleClassCache:
    """Tests for cached rule-class discovery (#388)."""

    @pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
    def _clean_rule_class_cache(self) -> Iterator[None]:
        """Isolate the process-global class cache per test.

        Yields:
            None: No value is yielded; clears the cache before and after.
        """
        from apme_engine.graph import scanner as scanner_mod

        scanner_mod.invalidate_graph_rule_cache()
        yield
        scanner_mod.invalidate_graph_rule_cache()

    _RULE_TMPL = (
        "from dataclasses import dataclass\n"
        "from apme_engine.graph.content_graph import ContentGraph, NodeType\n"
        "from apme_engine.graph.rule_base import GraphRule, GraphRuleResult\n"
        "from apme_engine.graph.types import Severity\n"
        "@dataclass\n"
        "class CacheProbeRule{idx}(GraphRule):\n"
        '    rule_id: str = "CACHE{idx}"\n'
        '    description: str = "cache probe"\n'
        '    name: str = "CacheProbe{idx}"\n'
        '    version: str = "v0.0.1"\n'
        "    severity: Severity = Severity.LOW\n"
        "    enabled: bool = True\n"
        "    def match(self, graph: ContentGraph, node_id: str) -> bool:\n"
        "        return False\n"
        "    def process(self, graph: ContentGraph, node_id: str):\n"
        "        return None\n"
    )

    def _write_rule(self, tmp_path: Path, idx: int) -> None:
        """Write a probe rule module into a temp rules dir.

        Args:
            tmp_path: Pytest temporary directory.
            idx: Probe index for unique rule IDs.
        """
        tmp_path.joinpath(f"probe_{idx}_graph.py").write_text(self._RULE_TMPL.format(idx=idx), encoding="utf-8")

    def test_repeated_load_reuses_classes_with_fresh_instances(self, tmp_path: Path) -> None:
        """Second load skips module re-exec but returns new instances.

        Args:
            tmp_path: Pytest temporary directory.
        """
        from apme_engine.graph import scanner as scanner_mod

        self._write_rule(tmp_path, 1)
        rules1, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules1} == {"CACHE1"}
        cache_size = len(scanner_mod._rule_class_cache)
        rules2, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules2} == {"CACHE1"}
        assert len(scanner_mod._rule_class_cache) == cache_size
        assert all(a is not b for a, b in zip(rules1, rules2, strict=True))

    def test_new_file_invalidates(self, tmp_path: Path) -> None:
        """Adding a rule file is picked up without explicit invalidation.

        Args:
            tmp_path: Pytest temporary directory.
        """
        self._write_rule(tmp_path, 1)
        rules1, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules1} == {"CACHE1"}
        self._write_rule(tmp_path, 2)
        rules2, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules2} == {"CACHE1", "CACHE2"}

    def test_explicit_invalidate(self, tmp_path: Path) -> None:
        """invalidate_graph_rule_cache forces rediscovery.

        Args:
            tmp_path: Pytest temporary directory.
        """
        from apme_engine.graph import scanner as scanner_mod

        self._write_rule(tmp_path, 1)
        load_graph_rules(rules_dir=str(tmp_path))
        assert str(tmp_path) in scanner_mod._rule_class_cache
        scanner_mod.invalidate_graph_rule_cache(str(tmp_path))
        assert str(tmp_path) not in scanner_mod._rule_class_cache
        rules, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules} == {"CACHE1"}

    def test_hidden_rule_file_invalidates(self, tmp_path: Path) -> None:
        """Dot-prefixed rule modules are fingerprinted and invalidate on change.

        Args:
            tmp_path: Pytest temporary directory.
        """
        hidden = tmp_path / ".hidden_graph.py"
        hidden.write_text(self._RULE_TMPL.format(idx=9), encoding="utf-8")
        rules1, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules1} == {"CACHE9"}
        hidden.write_text(self._RULE_TMPL.format(idx=10), encoding="utf-8")
        rules2, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules2} == {"CACHE10"}

    def test_unreadable_rule_module_bypasses_cache(self, tmp_path: Path) -> None:
        """Unreadable rule modules force uncached discovery instead of stale cache.

        Args:
            tmp_path: Pytest temporary directory.
        """
        import hashlib
        import os
        from unittest.mock import patch

        from apme_engine.graph import scanner as scanner_mod
        from apme_engine.graph.scanner import _dir_fingerprint

        self._write_rule(tmp_path, 1)
        load_graph_rules(rules_dir=str(tmp_path))
        assert os.path.normpath(str(tmp_path)) in scanner_mod._rule_class_cache

        blocked = tmp_path / "blocked_graph.py"
        blocked.write_text(self._RULE_TMPL.format(idx=2), encoding="utf-8")

        real_digest = hashlib.file_digest

        def guarded_digest(fh: object, algo: str) -> object:
            if Path(getattr(fh, "name", "")).name == "blocked_graph.py":
                raise OSError("permission denied")
            return real_digest(fh, algo)  # type: ignore[arg-type]

        with patch("apme_engine.graph.scanner.hashlib.file_digest", new=guarded_digest):
            assert _dir_fingerprint(str(tmp_path)) is None
            rules, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules} == {"CACHE1", "CACHE2"}

    def test_content_fingerprint_detects_rewrite(self, tmp_path: Path) -> None:
        """SHA-256 fingerprints change when rule file content changes.

        Args:
            tmp_path: Pytest temporary directory.
        """
        from apme_engine.graph.scanner import _dir_fingerprint

        path = tmp_path / "probe_1_graph.py"
        path.write_text(self._RULE_TMPL.format(idx=1), encoding="utf-8")
        fp1 = _dir_fingerprint(str(tmp_path))
        path.write_text(self._RULE_TMPL.format(idx="X"), encoding="utf-8")
        fp2 = _dir_fingerprint(str(tmp_path))
        assert fp1 != fp2

    def test_content_rewrite_invalidates_despite_preserved_stat(self, tmp_path: Path) -> None:
        """Content edits invalidate even when mtime and size are unchanged.

        Args:
            tmp_path: Pytest temporary directory.
        """
        import os

        path = tmp_path / "probe_1_graph.py"
        path.write_text(self._RULE_TMPL.format(idx=1), encoding="utf-8")
        rules1, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules1} == {"CACHE1"}

        stat = path.stat()
        path.write_text(self._RULE_TMPL.format(idx="X"), encoding="utf-8")
        assert path.stat().st_size == stat.st_size
        os.utime(path, (stat.st_atime, stat.st_mtime))

        rules2, _ = load_graph_rules(rules_dir=str(tmp_path))
        assert {r.rule_id for r in rules2} == {"CACHEX"}
