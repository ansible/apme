"""Preserve argspec coverage and argument diagnostics through output and rescans."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from apme.v1.common_pb2 import SEVERITY_HIGH
from apme.v1.engine_pb2 import RuleConfig
from apme_engine.cli.output import deduplicate_violations
from apme_engine.daemon.engine_server import _apply_rule_configs, _deduplicate_violations, _sort_violations
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType, _violation_key
from apme_engine.graph.types import ViolationDict
from apme_engine.remediation.graph_engine import _resolve_dirty_violations
from apme_engine.validators.ansible.cache import PluginCache
from apme_engine.validators.ansible.module_defaults import resolve_module_defaults
from apme_engine.validators.ansible.rules import L058_argspec_doc

_NODE_ID = "site.yml/plays[0]/tasks[0]"


def _diagnostics(rule_id: str = "L058") -> list[ViolationDict]:
    """Return coverage and argument diagnostics on the same physical task.

    Args:
        rule_id: Argspec rule producing the diagnostics.

    Returns:
        Warning followed by an argument error, as emitted by the adapter.
    """
    common: ViolationDict = {
        "rule_id": rule_id,
        "file": "site.yml",
        "line": 12,
        "path": _NODE_ID,
        "scope": "task",
        "remediation_class": "manual-review",
    }
    return [
        {**common, "severity": "warning", "message": "Module defaults need unavailable collection context"},
        {**common, "severity": "error", "message": "Missing required parameter 'url' for ansible.builtin.uri"},
    ]


def _graph() -> ContentGraph:
    """Create a graph containing the physical task shared by the diagnostics.

    Returns:
        A one-task graph with valid source identity and content.
    """
    graph = ContentGraph()
    graph.add_node(
        ContentNode(
            identity=NodeIdentity(path=_NODE_ID, node_type=NodeType.TASK),
            file_path="site.yml",
            line_start=12,
            line_end=14,
            module="ansible.builtin.uri",
            yaml_lines="- name: Call service\n  ansible.builtin.uri: {}\n",
        )
    )
    return graph


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
def test_server_and_cli_preserve_distinct_argspec_diagnostics(rule_id: str, reverse: bool) -> None:
    """Warnings and different errors survive while exact repeats collapse.

    Args:
        rule_id: Argspec rule sharing a location across diagnostics.
        reverse: Whether the argument error precedes the coverage warning.
    """
    warning, error = _diagnostics(rule_id)
    another_error = {**error, "message": "Unsupported parameters for ansible.builtin.uri: misspelled"}
    inputs = [warning, error, another_error]
    if reverse:
        inputs.reverse()
    repeated = inputs + [dict(finding) for finding in inputs]
    assert _deduplicate_violations(repeated) == inputs
    assert deduplicate_violations(repeated) == inputs


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
def test_argspec_identity_ignores_severity_overrides_without_merging_coverage(rule_id: str) -> None:
    """Presentation severity changes neither identity nor distinct diagnostic kinds.

    Args:
        rule_id: Argspec rule with configured presentation severity.
    """
    raw = _diagnostics(rule_id)
    configured = _apply_rule_configs(
        [dict(finding) for finding in raw], [RuleConfig(rule_id=rule_id, enabled=True, severity=SEVERITY_HIGH)]
    )
    assert all(finding["severity"] == "high" for finding in configured)
    assert [_violation_key(finding) for finding in raw] == [_violation_key(finding) for finding in configured]
    assert _deduplicate_violations(configured + raw) == configured
    assert deduplicate_violations(configured + raw) == configured
    graph = _graph()
    graph.register_violations(configured, pass_number=0)
    graph.register_violations(raw, pass_number=1)
    _resolve_dirty_violations(graph, raw, frozenset({_NODE_ID}), fixed_by="deterministic", pass_number=1)
    assert graph.query_violations(status="fixed") == []
    assert len(graph.query_violations(status="open")) == 2
    node = graph.get_node(_NODE_ID)
    assert node is not None
    assert all(record.discovered_in_pass == 0 for record in node.violation_ledger.values())


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("rule_id", "kind"),
    [(rule, kind) for rule in ("L058", "L059") for kind in ("unsupported", "required", "choice")]
    + [("L059", "exclusive"), ("L059", "together")],
)
def test_fqcn_rewrite_does_not_resolve_unchanged_argument_errors(tmp_path: Path, rule_id: str, kind: str) -> None:
    """Real argspec messages may change module spelling without indicating a fix.

    Args:
        tmp_path: Missing runtime, bypassed by supplied argument specs.
        rule_id: Docstring or captured-spec rule emitting the argument error.
        kind: Argument diagnostic whose module spelling changes.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    parameters: dict[str, object] = {}
    constraints: dict[str, object] = {}
    arguments: dict[str, object] = {}
    if kind == "unsupported":
        arguments["unknown"] = "literal"
    elif kind == "required":
        parameters["url"] = {"required": True}
    elif kind == "choice":
        parameters["state"] = {"choices": ["present", "absent"]}
        arguments["state"] = "invalid"
    elif kind == "exclusive":
        parameters.update({"src": {}, "content": {}})
        arguments.update({"src": "source", "content": "contents"})
        constraints["mutually_exclusive"] = [["src", "content"]]
    else:
        parameters.update({"username": {}, "password": {}})
        arguments["username"] = "known"
        constraints["required_together"] = [["username", "password"]]
    field = "options" if rule_id == "L058" else "argument_spec"
    spec = {field: parameters, **constraints}
    task: dict[str, object] = {
        "key": _NODE_ID,
        "file": "site.yml",
        "line": [12, 14],
        "module_options": arguments,
    }
    with (
        patch.object(rule, "plugin_cache", PluginCache()),
        patch.object(rule, "_fetch_specs", return_value={"uri": spec, "ansible.builtin.uri": spec}),
    ):
        initial = rule.run([{**task, "module": "uri"}], tmp_path, defaults_resolved=True)
        rescanned = rule.run([{**task, "module": "ansible.builtin.uri"}], tmp_path, defaults_resolved=True)
    assert len(initial) == len(rescanned) == 1
    initial_error = cast(ViolationDict, initial[0])
    rescanned_error = cast(ViolationDict, rescanned[0])
    assert initial_error["message"] != rescanned_error["message"]
    assert _violation_key(initial_error) == _violation_key(rescanned_error)
    assert len(_deduplicate_violations([initial_error, rescanned_error])) == 1
    assert len(deduplicate_violations([initial_error, rescanned_error])) == 1
    graph = _graph()
    graph.register_violations([initial_error], pass_number=0)
    graph.register_violations([rescanned_error], pass_number=1)
    _resolve_dirty_violations(graph, [rescanned_error], frozenset({_NODE_ID}), fixed_by="deterministic", pass_number=1)
    assert graph.query_violations(status="fixed") == []
    assert [finding["message"] for finding in graph.query_violations(status="open")] == [rescanned_error["message"]]
    node = graph.get_node(_NODE_ID)
    assert node is not None
    assert len(node.violation_ledger) == 1
    assert next(iter(node.violation_ledger.values())).discovered_in_pass == 0


def test_argspec_deduplication_normalizes_list_and_tuple_source_ranges() -> None:
    """Source-range representation does not prevent exact duplicate collapse."""
    warning, error = _diagnostics()
    warning["line"] = [12, 14]
    error["line"] = [12, 14]
    tuple_warning = {**warning, "line": (12, 14)}
    # Tuple ranges also arrive from internal callers using this existing API.
    inputs = [warning, error, tuple_warning]
    assert len(_deduplicate_violations(inputs)) == 2  # type: ignore[arg-type]
    assert len(deduplicate_violations(inputs)) == 2  # type: ignore[arg-type]


def test_other_rules_keep_existing_per_source_and_per_node_identity() -> None:
    """Changed messages do not create new lifecycle records for other rules."""
    first, second = _diagnostics("M001")
    assert _deduplicate_violations([first, second]) == [first]
    assert deduplicate_violations([first, second]) == [first]
    graph = _graph()
    graph.register_violations([first, second], pass_number=0)
    assert len(graph.query_violations()) == 1
    assert _violation_key(first) == (_NODE_ID, "M001")


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
def test_argspec_ledger_identity_survives_order_changes_and_serialization(rule_id: str) -> None:
    """Registration, wire conversion, and graph round trips retain both rows.

    Args:
        rule_id: Argspec rule whose diagnostics share a physical task.
    """
    diagnostics = _diagnostics(rule_id)
    transported = [violation_proto_to_dict(violation_dict_to_proto(finding)) for finding in diagnostics]
    engine_findings = _deduplicate_violations(_sort_violations(transported + transported))
    assert len(engine_findings) == 2
    graph = _graph()
    graph.register_violations(engine_findings, pass_number=0)
    graph.register_violations(list(reversed(engine_findings)), pass_number=1)
    node = graph.get_node(_NODE_ID)
    assert node is not None
    assert len(node.violation_ledger) == 2
    assert all(record.discovered_in_pass == 0 for record in node.violation_ledger.values())
    assert all(key[:2] == (_NODE_ID, rule_id) for key in node.violation_ledger)
    restored = ContentGraph.from_dict(json.loads(json.dumps(graph.to_dict())))
    output = deduplicate_violations(restored.query_violations(status="open"))
    assert {finding["message"] for finding in output} == {finding["message"] for finding in diagnostics}
    assert len(output) == 2
    assert any(finding["severity"] == "error" for finding in output)
    assert all(finding["remediation_class"] == "manual-review" for finding in output)


@pytest.mark.parametrize("remaining_index", [0, 1])  # type: ignore[untyped-decorator]
def test_rescan_resolves_only_the_absent_argspec_diagnostic(remaining_index: int) -> None:
    """A remaining warning cannot keep a disappeared argument error open.

    Args:
        remaining_index: Whether coverage or the argument error remains.
    """
    diagnostics = _diagnostics()
    graph = _graph()
    graph.register_violations(diagnostics, pass_number=0)
    remaining = diagnostics[remaining_index]
    absent = diagnostics[1 - remaining_index]
    _resolve_dirty_violations(graph, [remaining], frozenset({_NODE_ID}), fixed_by="deterministic", pass_number=1)
    assert [finding["message"] for finding in graph.query_violations(status="open")] == [remaining["message"]]
    assert [finding["message"] for finding in graph.query_violations(status="fixed")] == [absent["message"]]
    graph.register_violations([remaining], pass_number=1)
    assert len(graph.query_violations(status="fixed")) == 1
    graph.register_violations([absent], pass_number=2)
    assert len(graph.query_violations(status="open")) == 2
    assert graph.query_violations(status="fixed") == []


def test_declining_one_argspec_diagnostic_does_not_decline_its_peer() -> None:
    """Diagnostic-specific transitions target the exact ledger row."""
    warning, error = _diagnostics()
    graph = _graph()
    graph.register_violations([warning, error], pass_number=0)
    assert graph.decline_open_violations([warning]) == 1
    graph.register_violations([error, warning], pass_number=1)
    assert [finding["message"] for finding in graph.query_violations(status="declined")] == [warning["message"]]
    assert [finding["message"] for finding in graph.query_violations(status="open")] == [error["message"]]


def test_legacy_argspec_ledger_keys_upgrade_without_duplicate_registration() -> None:
    """Persisted two-component argspec identities remain usable after loading."""
    warning, error = _diagnostics()
    graph = _graph()
    graph.register_violations([warning], pass_number=0)
    payload = graph.to_dict()
    for entry in cast(list[dict[str, object]], payload["nodes"]):
        data = cast(dict[str, object], entry["data"])
        for record in cast(list[dict[str, object]], data.get("violation_ledger", [])):
            record["key"] = cast(list[str], record["key"])[:2]
    restored = ContentGraph.from_dict(payload)
    restored.register_violations([warning, error], pass_number=1)
    assert len(restored.query_violations(status="open")) == 2


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
def test_persisted_presentation_based_argspec_keys_migrate(rule_id: str) -> None:
    """Old three-part keys cannot retain false identities after a graph reload.

    Args:
        rule_id: Argspec rule whose earlier ledger keys included severity.
    """
    diagnostics = _diagnostics(rule_id)
    graph = _graph()
    graph.register_violations(diagnostics, pass_number=0)
    payload = graph.to_dict()
    for entry in cast(list[dict[str, object]], payload["nodes"]):
        data = cast(dict[str, object], entry["data"])
        for record in cast(list[dict[str, object]], data.get("violation_ledger", [])):
            finding = cast(ViolationDict, record["violation"])
            old_identity = [str(finding["severity"]), str(finding["message"])]
            old_digest = hashlib.sha256(json.dumps(old_identity, ensure_ascii=False).encode("utf-8")).hexdigest()
            record["key"] = [_NODE_ID, rule_id, old_digest]
    restored = ContentGraph.from_dict(payload)
    updated = [{**finding, "severity": "high"} for finding in diagnostics]
    restored.register_violations(updated, pass_number=1)
    _resolve_dirty_violations(restored, updated, frozenset({_NODE_ID}), fixed_by="deterministic", pass_number=1)
    assert restored.query_violations(status="fixed") == []
    assert len(restored.query_violations(status="open")) == 2


def test_real_l058_coverage_and_argument_error_survive_full_identity_pipeline(tmp_path: Path) -> None:
    """The reviewed warning-first adapter pattern retains its real argspec error.

    Args:
        tmp_path: Missing runtime, bypassed by a supplied documentation spec.
    """
    task: dict[str, object] = {
        "key": _NODE_ID,
        "file": "site.yml",
        "line": [12, 14],
        "module": "ansible.builtin.uri",
        "module_options": {},
        "module_defaults_used": True,
    }
    coverage = resolve_module_defaults([{**task, "module_defaults_incomplete": True}], tmp_path / "missing").violations
    with (
        patch.object(L058_argspec_doc, "plugin_cache", PluginCache()),
        patch.object(
            L058_argspec_doc,
            "_fetch_specs",
            return_value={"ansible.builtin.uri": {"options": {"url": {"required": True}}}},
        ),
    ):
        errors = L058_argspec_doc.run([task], tmp_path / "missing", defaults_resolved=True)
    assert len(coverage) == len(errors) == 1
    assert coverage[0]["severity"] == "warning"
    assert errors[0]["severity"] == "error"
    findings = _deduplicate_violations(_sort_violations(cast(list[ViolationDict], coverage + errors)))
    graph = _graph()
    graph.register_violations(findings, pass_number=0)
    output = deduplicate_violations(graph.query_violations(status="open"))
    assert len(output) == 2
    assert {finding["severity"] for finding in output} == {"warning", "error"}
    assert all(finding["remediation_class"] == "manual-review" for finding in output)
