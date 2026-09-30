"""Unit tests for FixCompletedEvent chunk packing (ADR-020 oversized path)."""

from __future__ import annotations

from apme.v1 import common_pb2, engine_pb2, reporting_pb2
from apme_engine.daemon.chunked_reporting import (
    UNARY_MAX_BYTES,
    needs_streaming,
    yield_fix_completed_chunks,
)
from apme_gateway.grpc_reporting.servicer import _reassemble_fix_completed


def _violation(i: int, pad: str = "") -> common_pb2.Violation:
    """Build a Violation with optional padding in the message.

    Args:
        i: Index used in rule_id / file path.
        pad: Extra text to inflate serialized size.

    Returns:
        Proto Violation.
    """
    return common_pb2.Violation(
        rule_id=f"L{i:03d}",
        severity=common_pb2.SEVERITY_ERROR,
        message=f"bad task {i} {pad}",
        file=f"playbooks/task_{i}.yml",
        line=i,
    )


def test_yield_small_event_single_chunk_last() -> None:
    """Empty/small event yields one chunk with header and last=True."""
    event = reporting_pb2.FixCompletedEvent(
        scan_id="s1",
        session_id="sess",
        project_path="/p",
        source="cli",
        summary=common_pb2.ScanSummary(total=1),
    )
    chunks = list(yield_fix_completed_chunks(event))
    assert len(chunks) == 1
    assert chunks[0].last is True
    assert chunks[0].header.scan_id == "s1"
    assert chunks[0].header.session_id == "sess"
    assert not chunks[0].remaining_violations


def test_yield_splits_violations_across_chunks() -> None:
    """Tiny budget forces multiple violation batches; only last has last=True."""
    pad = "x" * 80
    event = reporting_pb2.FixCompletedEvent(
        scan_id="multi",
        session_id="sess",
        project_path="/p",
        remaining_violations=[_violation(i, pad) for i in range(6)],
    )
    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=100))
    assert len(chunks) >= 3
    assert chunks[0].HasField("header")
    assert chunks[0].header.scan_id == "multi"
    assert chunks[0].last is False
    assert all(not c.HasField("header") for c in chunks[1:])
    assert chunks[-1].last is True
    assert all(c.last is False for c in chunks[:-1])

    total_violations = sum(len(c.remaining_violations) for c in chunks)
    assert total_violations == 6


def test_yield_fragments_content_graph_json() -> None:
    """Large content_graph_json is split into UTF-8-safe fragments."""
    # Include multi-byte chars to exercise UTF-8 boundary logic.
    graph = '{"nodes":["αβγ"]}' + ("n" * 500)
    event = reporting_pb2.FixCompletedEvent(
        scan_id="graph",
        session_id="sess",
        project_path="/p",
        content_graph_json=graph,
    )
    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=40))
    assert len(chunks) >= 3
    assert chunks[0].HasField("header")
    fragments = [c.content_graph_json_fragment for c in chunks if c.content_graph_json_fragment]
    assert "".join(fragments) == graph
    assert chunks[-1].last is True


def test_reassemble_round_trip() -> None:
    """Chunk then reassemble restores the original event fields."""
    event = reporting_pb2.FixCompletedEvent(
        scan_id="rt",
        session_id="sess-rt",
        project_path="/proj",
        source="cli",
        remaining_violations=[_violation(1), _violation(2)],
        fixed_violations=[_violation(3)],
        logs=[common_pb2.ProgressUpdate(message="done", phase="complete", progress=1.0)],
        proposals=[
            reporting_pb2.ProposalOutcome(proposal_id="p1", rule_id="L001", status="approved"),
        ],
        content_graph_json='{"nodes":[],"edges":[]}',
        summary=common_pb2.ScanSummary(total=3, auto_fixable=1),
        report=engine_pb2.FixReport(fixed=1),
    )
    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=50))
    rebuilt = _reassemble_fix_completed(chunks)
    assert rebuilt is not None
    assert rebuilt.scan_id == "rt"
    assert rebuilt.session_id == "sess-rt"
    assert rebuilt.project_path == "/proj"
    assert len(rebuilt.remaining_violations) == 2
    assert len(rebuilt.fixed_violations) == 1
    assert len(rebuilt.logs) == 1
    assert len(rebuilt.proposals) == 1
    assert rebuilt.content_graph_json == '{"nodes":[],"edges":[]}'
    assert rebuilt.summary.total == 3
    assert rebuilt.report.fixed == 1


def test_needs_streaming_threshold() -> None:
    """needs_streaming is False for small events and True near UNARY_MAX_BYTES."""
    small = reporting_pb2.FixCompletedEvent(scan_id="s", session_id="x", project_path="/p")
    assert needs_streaming(small) is False
    assert UNARY_MAX_BYTES == 45 * 1024 * 1024
