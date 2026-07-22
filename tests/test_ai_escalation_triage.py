"""AI escalation triage — engine filter helpers and Gateway pause wiring."""

from __future__ import annotations

import asyncio

from apme_gateway.operation_types import OperationStatus, SSEEventType


def test_awaiting_ai_triage_status_is_non_terminal() -> None:
    """AWAITING_AI_TRIAGE must not be treated as terminal.

    Returns:
        None.
    """
    from apme_gateway.operation_types import TERMINAL_STATUSES

    assert OperationStatus.AWAITING_AI_TRIAGE not in TERMINAL_STATUSES
    assert OperationStatus.AWAITING_AI_TRIAGE.value == "awaiting_ai_triage"


def test_ai_triage_sse_event_type() -> None:
    """AI_TRIAGE event type is registered for SSE clients.

    Returns:
        None.
    """
    assert SSEEventType.AI_TRIAGE.value == "ai_triage"


def test_session_status_name_maps_awaiting_ai_triage() -> None:
    """WS clients must see AWAITING_AI_TRIAGE, not AWAITING_APPROVAL.

    Returns:
        None.
    """
    from apme_gateway.session_client import _status_name

    assert _status_name(4) == "AWAITING_AI_TRIAGE"
    assert _status_name(1) == "AWAITING_APPROVAL"


def test_ai_triage_candidates_use_current_yaml_not_scan_baseline() -> None:
    """AI escalation snippets must reflect post–Quick-fix node YAML.

    Returns:
        None.
    """
    from apme_engine.daemon.primary_server import _collect_ai_triage_candidates
    from apme_engine.daemon.session import SessionState
    from apme_engine.engine.models import RemediationClass
    from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

    baseline = "- name: old\n  debug:\n    msg: before\n"
    current = "- name: new\n  ansible.builtin.debug:\n    msg: after-t1\n"
    path = "play.yml/plays[0]/tasks[0]"
    node = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.TASK),
        file_path="play.yml",
        line_start=1,
        line_end=3,
        yaml_lines=baseline,
    )
    node.record_state(0, "scanned")
    node.yaml_lines = current
    node.record_state(1, "transformed", source="deterministic")

    graph = ContentGraph()
    graph.add_node(node)
    graph.register_violations(
        [
            {
                "path": path,
                "rule_id": "L050",
                "message": "needs AI",
                "file": "play.yml",
                "remediation_class": RemediationClass.AI_CANDIDATE,
            }
        ],
        0,
    )

    session = SessionState(session_id="esc-yaml")
    session.content_graph = graph
    cands = _collect_ai_triage_candidates(session)
    assert len(cands) == 1
    assert cands[0]["original_yaml"] == current
    assert cands[0]["original_yaml"] != baseline


def test_decline_skipped_ai_escalation_keeps_allow_list_open() -> None:
    """Skipped AI-candidates become sticky declined; Included stay open.

    Returns:
        None.
    """
    from apme_engine.daemon.primary_server import _decline_skipped_ai_escalation
    from apme_engine.daemon.session import SessionState
    from apme_engine.engine.models import RemediationClass, ViolationDict
    from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType

    graph = ContentGraph()
    for path in ("a::0", "b::1"):
        graph.add_node(
            ContentNode(
                identity=NodeIdentity(path=path, node_type=NodeType.TASK),
                file_path="play.yml",
                line_start=1,
                line_end=2,
                yaml_lines="- debug: msg=hi\n",
            )
        )
    viols: list[ViolationDict] = [
        {
            "path": "a::0",
            "rule_id": "L050",
            "message": "ai",
            "file": "play.yml",
            "remediation_class": RemediationClass.AI_CANDIDATE,
        },
        {
            "path": "b::1",
            "rule_id": "L050",
            "message": "ai",
            "file": "play.yml",
            "remediation_class": RemediationClass.AI_CANDIDATE,
        },
    ]
    graph.register_violations(viols, 0)

    session = SessionState(session_id="esc-decline")
    session.content_graph = graph
    session.ai_escalate_targets = [("a::0", frozenset())]

    n = _decline_skipped_ai_escalation(session)
    assert n == 1
    open_paths = {str(v.get("path")) for v in graph.query_violations(status="open")}
    declined_paths = {str(v.get("path")) for v in graph.query_violations(status="declined")}
    assert open_paths == {"a::0"}
    assert declined_paths == {"b::1"}

    # Sticky: re-register must not reopen declined
    graph.register_violations(
        [
            {
                "path": "b::1",
                "rule_id": "L050",
                "message": "ai",
                "file": "play.yml",
            }
        ],
        1,
    )
    assert "b::1" not in {str(v.get("path")) for v in graph.query_violations(status="open")}


def test_filter_violations_by_escalate_targets() -> None:
    """Empty targets skip AI; path match with empty rule_ids allows all rules.

    Returns:
        None.
    """
    from apme_engine.daemon.primary_server import _filter_violations_by_escalate_targets
    from apme_engine.engine.models import ViolationDict

    viols: list[ViolationDict] = [
        {"path": "a::0", "rule_id": "L001"},
        {"path": "a::0", "rule_id": "L002"},
        {"path": "b::1", "rule_id": "L001"},
    ]
    assert _filter_violations_by_escalate_targets(viols, None) == viols
    assert _filter_violations_by_escalate_targets(viols, []) == []
    whole_a = _filter_violations_by_escalate_targets(viols, [("a::0", frozenset())])
    assert {v["rule_id"] for v in whole_a} == {"L001", "L002"}
    one_rule = _filter_violations_by_escalate_targets(viols, [("a::0", frozenset({"L002"}))])
    assert [v["rule_id"] for v in one_rule] == ["L002"]


def test_set_ai_triage_creates_future_and_status() -> None:
    """set_ai_triage transitions to AWAITING_AI_TRIAGE and creates escalate future.

    Returns:
        None.
    """
    from apme_gateway.operation_registry import OperationRegistry

    async def _run() -> None:
        registry = OperationRegistry()
        op_id = "op-ai-triage"
        registry.create(
            operation_id=op_id,
            project_id="proj-ai",
            scan_id="scan-ai",
            scan_type="remediate",
        )
        registry.transition(op_id, OperationStatus.APPLYING)
        registry.set_ai_triage(
            op_id,
            [{"rule_id": "L001", "path": "p::0", "message": "ai"}],
        )
        op = registry.get(op_id)
        assert op is not None
        assert op.status == OperationStatus.AWAITING_AI_TRIAGE
        assert op.escalate_ai_future is not None
        assert not op.escalate_ai_future.done()
        assert op.ai_triage_candidates is not None
        assert op.ai_triage_candidates[0]["rule_id"] == "L001"
        snap = op.to_snapshot()
        assert snap["ai_triage_candidates"][0]["path"] == "p::0"

    asyncio.run(_run())


def test_set_ai_triage_ignores_after_escalate() -> None:
    """Replayed AiTriageReady must not regress APPLYING after escalate-ai.

    Covers both bridge-cleared future (None) and done-but-not-yet-cleared.

    Returns:
        None.
    """
    from apme_gateway.operation_registry import OperationRegistry

    async def _run() -> None:
        registry = OperationRegistry()
        op_id = "op-ai-no-regress"
        registry.create(
            operation_id=op_id,
            project_id="proj-ai-2",
            scan_id="scan-ai-2",
            scan_type="remediate",
        )
        registry.set_ai_triage(op_id, [{"rule_id": "L001", "message": "first"}])
        op = registry.get(op_id)
        assert op is not None
        # Window after POST /escalate-ai: future done, status APPLYING, bridge
        # has not cleared escalate_ai_future yet.
        assert op.escalate_ai_future is not None
        op.escalate_ai_future.set_result([{"path": "p::0", "rule_ids": []}])
        registry.transition(op_id, OperationStatus.APPLYING)
        registry.set_ai_triage(op_id, [{"rule_id": "L002", "message": "late-done"}])
        mid = registry.get(op_id)
        assert mid is not None
        assert mid.status == OperationStatus.APPLYING
        assert mid.ai_triage_candidates is not None
        assert mid.ai_triage_candidates[0]["rule_id"] == "L001"

        # After bridge clears the future.
        mid.escalate_ai_future = None
        registry.set_ai_triage(op_id, [{"rule_id": "L003", "message": "late-none"}])
        again = registry.get(op_id)
        assert again is not None
        assert again.status == OperationStatus.APPLYING
        assert again.ai_triage_candidates is not None
        assert again.ai_triage_candidates[0]["rule_id"] == "L001"

    asyncio.run(_run())


def test_escalate_ai_endpoint_resolves_future() -> None:
    """POST escalate-ai resolves the future and transitions to APPLYING.

    Returns:
        None.
    """
    from apme_gateway.api.operation_router import EscalateAiRequest, escalate_ai
    from apme_gateway.operation_registry import get_operation_registry

    async def _run() -> None:
        registry = get_operation_registry()
        await registry.shutdown()
        try:
            op_id = "op-esc"
            project_id = "proj-esc"
            registry.create(
                operation_id=op_id,
                project_id=project_id,
                scan_id="scan-esc",
                scan_type="remediate",
            )
            registry.set_ai_triage(op_id, [{"rule_id": "L001", "path": "x::0", "message": "m"}])
            body = EscalateAiRequest.model_validate({"targets": [{"path": "x::0", "rule_ids": []}]})
            result = await escalate_ai(project_id, body)
            assert result == {"status": "escalate_ai"}
            op = registry.get(op_id)
            assert op is not None
            assert op.status == OperationStatus.APPLYING
            assert op.escalate_ai_future is not None
            assert op.escalate_ai_future.done()
            assert op.escalate_ai_future.result() == [{"path": "x::0", "rule_ids": []}]
        finally:
            await registry.shutdown()

    asyncio.run(_run())
