"""Ledger keys and partition routing agree on prefixed rule IDs (PE-4).

The violation ledger (``ContentGraph``) and remediation routing
(``partition``) must share a single rule-ID identity: a violation reported
as ``native:L042`` must land under the ``L042`` ledger key and route the
same as a bare ``L042`` violation.
"""

from __future__ import annotations

from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
from apme_engine.graph.types import ViolationDict
from apme_engine.remediation.partition import (
    is_finding_resolvable,
    normalize_rule_id,
    partition_violations,
)
from apme_engine.remediation.registry import TransformRegistry

TASK_ID = "site.yml::play[0]/tasks[0]"


def _make_graph() -> ContentGraph:
    """Build a graph with a single task node.

    Returns:
        Graph containing the task node.
    """
    graph = ContentGraph()
    graph.add_node(
        ContentNode(
            identity=NodeIdentity(TASK_ID, NodeType.TASK),
            file_path="site.yml",
        )
    )
    return graph


def _violation(rule_id: str) -> ViolationDict:
    """Build a minimal violation dict for the task node.

    Args:
        rule_id: Raw rule ID (bare or prefixed).

    Returns:
        Violation dict suitable for ledger registration and partitioning.
    """
    return {
        "path": TASK_ID,
        "rule_id": rule_id,
        "severity": "error",
        "message": "test violation",
    }


class TestRuleIdNormalizerAgreement:
    """Ledger keys and partition routing share one rule-ID identity."""

    def test_canonical_normalizer_strips_native_prefix(self) -> None:
        """Prefixed IDs normalize to their bare form; bare IDs pass through."""
        assert normalize_rule_id("native:L042") == "L042"
        assert normalize_rule_id("L042") == "L042"

    def test_canonical_normalizer_leaves_other_prefixes(self) -> None:
        """Only the legacy ``native:`` prefix is stripped (backend contract)."""
        assert normalize_rule_id("opa:L003") == "opa:L003"
        assert normalize_rule_id("ansible:R101") == "ansible:R101"
        assert normalize_rule_id("unknown:X1") == "unknown:X1"
        assert normalize_rule_id("") == ""
        assert normalize_rule_id("a:b:c") == "a:b:c"
        assert normalize_rule_id("NATIVE:L042") == "NATIVE:L042"

    def test_ledger_key_uses_canonical_rule_id(self) -> None:
        """A prefixed violation registers under the bare-ID ledger key."""
        graph = _make_graph()
        graph.register_violations([_violation("native:L042")], pass_number=0)
        node = graph.get_node(TASK_ID)
        assert node is not None
        assert (TASK_ID, "L042") in node.violation_ledger

    def test_partition_routing_agrees_on_prefixed_id(self) -> None:
        """Prefixed and bare violations route to the same tier (Tier 2)."""
        prefixed_tiers = partition_violations([_violation("native:L042")], TransformRegistry())
        bare_tiers = partition_violations([_violation("L042")], TransformRegistry())
        assert prefixed_tiers[0] == [] and prefixed_tiers[2] == []
        assert bare_tiers[0] == [] and bare_tiers[2] == []
        assert len(prefixed_tiers[1]) == 1
        assert len(bare_tiers[1]) == 1

    def test_tier1_routing_agrees_on_prefixed_id(self) -> None:
        """Prefixed and bare violations both resolve via a bare-ID transform."""
        registry = TransformRegistry()
        registry.register("L042", node=lambda _node, _violation: True)
        assert is_finding_resolvable(_violation("native:L042"), registry)
        assert is_finding_resolvable(_violation("L042"), registry)
        prefixed_tiers = partition_violations([_violation("native:L042")], registry)
        bare_tiers = partition_violations([_violation("L042")], registry)
        assert len(prefixed_tiers[0]) == 1
        assert len(bare_tiers[0]) == 1

    def test_partition_tier_identity_matches_routed_key(self) -> None:
        """Routed tier carries the violation whose ledger key matches."""
        from apme_engine.graph.content_graph import _violation_key

        registry = TransformRegistry()
        registry.register("L042", node=lambda _node, _violation: True)
        violation = _violation("native:L042")
        tiers = partition_violations([violation], registry)
        assert len(tiers[0]) == 1
        assert tiers[0][0]["rule_id"] == "native:L042"
        assert _violation_key(violation) == (TASK_ID, "L042")

    def test_get_scope_defaults_and_passthrough(self) -> None:
        """_get_scope defaults to task and passes explicit scopes through."""
        from apme_engine.remediation.partition import _get_scope

        assert _get_scope({}) == "task"
        assert _get_scope({"scope": "play"}) == "play"
        assert _get_scope({"scope": ""}) == "task"
