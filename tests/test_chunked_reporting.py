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


def test_yield_fragments_individually_oversized_violation() -> None:
    """A single repeated item larger than a frame uses serialized fragments."""
    event = reporting_pb2.FixCompletedEvent(
        scan_id="large-item",
        session_id="sess",
        project_path="/p",
        remaining_violations=[_violation(1, "x" * 256)],
    )

    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=40, max_message_bytes=100))

    assert chunks
    assert all(chunk.HasField("serialized_event_fragment") for chunk in chunks)
    assert all(chunk.ByteSize() <= 100 for chunk in chunks)
    assert chunks[-1].last is True
    rebuilt = _reassemble_fix_completed(chunks)
    assert rebuilt == event


def test_yield_fragments_oversized_manifest_header() -> None:
    """A manifest that cannot fit in the first frame uses wire fragments."""
    event = reporting_pb2.FixCompletedEvent(
        scan_id="large-manifest",
        session_id="sess",
        project_path="/p",
        manifest=common_pb2.ProjectManifest(dependency_tree="tree" * 100),
    )

    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=40, max_message_bytes=100))

    assert chunks
    assert all(chunk.HasField("serialized_event_fragment") for chunk in chunks)
    assert all(chunk.ByteSize() <= 100 for chunk in chunks)
    rebuilt = _reassemble_fix_completed(chunks)
    assert rebuilt == event


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


def test_header_strips_nested_report_violations() -> None:
    """Report-nested violations must not ride along in the stream header.

    Regression test: _header_from_event copied event.report wholesale, so the
    first chunk duplicated every violation (nested in header.report plus
    chunked top-level) and stayed unbounded against the 50 MiB ceiling.
    """
    nested = [_violation(100 + i, "y" * 2000) for i in range(10)]
    top = [_violation(i, "x" * 200) for i in range(4)]
    event = reporting_pb2.FixCompletedEvent(
        scan_id="hdr",
        session_id="sess",
        project_path="/p",
        source="cli",
        remaining_violations=top,
        report=engine_pb2.FixReport(
            fixed=2,
            remaining_violations=nested,
            fixed_violations=nested,
        ),
    )
    chunks = list(yield_fix_completed_chunks(event, chunk_max_bytes=100))
    assert len(chunks) >= 2
    header = chunks[0].header
    # Header report keeps scalars, drops nested repeats.
    assert header.report.fixed == 2
    assert not header.report.remaining_violations
    assert not header.report.fixed_violations
    # First chunk stays small: header only, no nested violation bytes.
    # (Before the strip this exceeds 8 KiB with ~44 KiB of nested copies.)
    assert chunks[0].ByteSize() < 8 * 1024
    # Round-trip preserves the top-level payload and scalar report fields.
    rebuilt = _reassemble_fix_completed(chunks)
    assert rebuilt is not None
    assert len(rebuilt.remaining_violations) == 4
    assert rebuilt.report.fixed == 2
    assert not rebuilt.report.remaining_violations
    assert not rebuilt.report.fixed_violations


def test_needs_streaming_threshold() -> None:
    """needs_streaming is False for small events and True near UNARY_MAX_BYTES."""
    small = reporting_pb2.FixCompletedEvent(scan_id="s", session_id="x", project_path="/p")
    assert needs_streaming(small) is False
    assert UNARY_MAX_BYTES == 45 * 1024 * 1024
