"""Chunk oversized FixCompletedEvent payloads for streaming ReportFixCompleted.

Mirrors :mod:`apme_engine.daemon.chunked_fs` packing: identity and small
fields travel on the first chunk; large repeated fields and
``content_graph_json`` are batched under a 1 MiB budget so each gRPC frame
stays under the 50 MiB channel ceiling (ADR-020).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol

from apme.v1 import engine_pb2, reporting_pb2

# Soft per-chunk budget — same as chunked_fs.CHUNK_MAX_BYTES.
CHUNK_MAX_BYTES = 1024 * 1024  # 1 MiB

# Route to streaming when the unary payload would approach the 50 MiB ceiling.
UNARY_MAX_BYTES = 45 * 1024 * 1024  # 45 MiB


class _SizedMessage(Protocol):
    """Minimal protobuf surface needed for size-based batching."""

    def ByteSize(self) -> int:
        """Return the serialized size of this message in bytes.

        Returns:
            Serialized size in bytes.
        """
        ...


def needs_streaming(event: reporting_pb2.FixCompletedEvent) -> bool:
    """Return True when the event should use ReportFixCompletedStream.

    Args:
        event: Fully built FixCompletedEvent.

    Returns:
        True if serialized size is at or above :data:`UNARY_MAX_BYTES`.
    """
    return event.ByteSize() >= UNARY_MAX_BYTES


def _header_report_from_report(report: engine_pb2.FixReport) -> engine_pb2.FixReport:
    """Copy scalar FixReport fields; strip nested violations.

    The nested ``remaining_violations`` / ``fixed_violations`` duplicate the
    top-level repeated fields of :class:`FixCompletedEvent`, which travel in
    chunked payload batches. Copying them into the header would duplicate
    every violation on the wire and leave the first chunk unbounded against
    the 50 MiB ceiling. The Gateway persist path only reads ``report.fixed``.

    Args:
        report: Source FixReport.

    Returns:
        Header-safe FixReport with only scalar fields set.
    """
    return engine_pb2.FixReport(
        passes=report.passes,
        fixed=report.fixed,
        remaining_ai=report.remaining_ai,
        remaining_manual=report.remaining_manual,
        oscillation_detected=report.oscillation_detected,
    )


def _header_from_event(event: reporting_pb2.FixCompletedEvent) -> reporting_pb2.FixCompletedEvent:
    """Copy scalars and small nested messages; omit large repeated/blob fields.

    Args:
        event: Source FixCompletedEvent.

    Returns:
        Header-only FixCompletedEvent suitable for the first chunk.
    """
    return reporting_pb2.FixCompletedEvent(
        scan_id=event.scan_id,
        session_id=event.session_id,
        project_path=event.project_path,
        source=event.source,
        diagnostics=event.diagnostics,
        summary=event.summary,
        report=_header_report_from_report(event.report),
        manifest=event.manifest,
    )


def _batch_messages[T: _SizedMessage](items: Sequence[T], chunk_max_bytes: int) -> Iterator[list[T]]:
    """Yield batches of protobuf messages under a serialized-size budget.

    Args:
        items: Messages to pack.
        chunk_max_bytes: Soft max cumulative ByteSize per batch.

    Yields:
        list[T]: Non-empty lists of messages.
    """
    batch: list[T] = []
    batch_bytes = 0
    for item in items:
        size = item.ByteSize()
        if batch and batch_bytes + size > chunk_max_bytes:
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(item)
        batch_bytes += size
    if batch:
        yield batch


def _utf8_fragments(text: str, max_bytes: int) -> Iterator[str]:
    """Split *text* into UTF-8-safe fragments each encoding to at most *max_bytes*.

    Args:
        text: Unicode string to fragment.
        max_bytes: Maximum UTF-8 byte length per fragment.

    Yields:
        str: Non-empty string fragments that concatenate to *text*.

    Raises:
        ValueError: If *max_bytes* is less than 1.
    """
    if not text:
        return
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    data = text.encode("utf-8")
    offset = 0
    while offset < len(data):
        end = min(offset + max_bytes, len(data))
        # Back up if *end* lands inside a multi-byte UTF-8 sequence.
        while end > offset and end < len(data) and (data[end] & 0xC0) == 0x80:
            end -= 1
        if end == offset:
            # Single codepoint larger than max_bytes — emit one codepoint.
            end = offset + 1
            while end < len(data) and (data[end] & 0xC0) == 0x80:
                end += 1
        yield data[offset:end].decode("utf-8")
        offset = end


def yield_fix_completed_chunks(
    event: reporting_pb2.FixCompletedEvent,
    chunk_max_bytes: int = CHUNK_MAX_BYTES,
) -> Iterator[reporting_pb2.FixCompletedChunk]:
    """Yield FixCompletedChunk messages packing *event* under gRPC size limits.

    First chunk carries a header with scalars and small nested messages.
    Subsequent chunks batch large repeated fields and UTF-8 fragments of
    ``content_graph_json``. The final chunk sets ``last=True``.

    Args:
        event: Fully built FixCompletedEvent to stream.
        chunk_max_bytes: Soft max serialized size per large-field batch.

    Yields:
        reporting_pb2.FixCompletedChunk: Chunk messages for ReportFixCompletedStream.
    """
    remaining = list(event.remaining_violations)
    fixed = list(event.fixed_violations)
    patches = list(event.patches)
    logs = list(event.logs)
    proposals = list(event.proposals)
    graph = event.content_graph_json or ""

    has_payload = bool(remaining or fixed or patches or logs or proposals or graph)

    header = _header_from_event(event)
    if not has_payload:
        yield reporting_pb2.FixCompletedChunk(header=header, last=True)
        return

    yield reporting_pb2.FixCompletedChunk(header=header, last=False)

    pending: list[reporting_pb2.FixCompletedChunk] = []

    for rem_batch in _batch_messages(remaining, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(remaining_violations=rem_batch))
    for fixed_batch in _batch_messages(fixed, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(fixed_violations=fixed_batch))
    for patch_batch in _batch_messages(patches, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(patches=patch_batch))
    for log_batch in _batch_messages(logs, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(logs=log_batch))
    for proposal_batch in _batch_messages(proposals, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(proposals=proposal_batch))
    for fragment in _utf8_fragments(graph, chunk_max_bytes):
        pending.append(reporting_pb2.FixCompletedChunk(content_graph_json_fragment=fragment))

    if not pending:
        # Defensive: has_payload was True but nothing queued — mark header as last.
        # Unreachable in normal packing; kept so callers always see last=True.
        yield reporting_pb2.FixCompletedChunk(last=True)
        return

    *body, final = pending
    for chunk in body:
        chunk.last = False
        yield chunk
    final.last = True
    yield final
