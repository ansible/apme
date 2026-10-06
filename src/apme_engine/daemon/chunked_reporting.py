"""Chunk oversized FixCompletedEvent payloads for streaming ReportFixCompleted.

Identity and small fields travel on the first chunk; repeated fields and
``content_graph_json`` are yielded lazily under a 1 MiB soft budget. A field
too large for one gRPC frame switches to bounded fragments of the serialized
event (ADR-020).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from apme.v1 import engine_pb2, reporting_pb2

# Soft per-chunk budget — same as chunked_fs.CHUNK_MAX_BYTES.
CHUNK_MAX_BYTES = 1024 * 1024  # 1 MiB

# Route to streaming when the unary payload would approach the 50 MiB ceiling.
UNARY_MAX_BYTES = 45 * 1024 * 1024  # 45 MiB
GRPC_MAX_MESSAGE_BYTES = 50 * 1024 * 1024  # 50 MiB


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


def _header_from_event(
    event: reporting_pb2.FixCompletedEvent,
    *,
    include_manifest: bool = True,
) -> reporting_pb2.FixCompletedEvent:
    """Copy scalars and small nested messages; omit large repeated/blob fields.

    Args:
        event: Source FixCompletedEvent.
        include_manifest: Whether to copy the manifest into the header.

    Returns:
        Header-only FixCompletedEvent suitable for the first chunk.
    """
    header = reporting_pb2.FixCompletedEvent(
        scan_id=event.scan_id,
        session_id=event.session_id,
        project_path=event.project_path,
        source=event.source,
        diagnostics=event.diagnostics,
        summary=event.summary,
        report=_header_report_from_report(event.report),
    )
    if include_manifest and event.HasField("manifest"):
        header.manifest.CopyFrom(event.manifest)
    return header


def _batch_messages[T: _SizedMessage](items: Iterable[T], chunk_max_bytes: int) -> Iterator[list[T]]:
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
        item_wire_size = size + 10
        if batch and batch_bytes + item_wire_size > chunk_max_bytes:
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(item)
        batch_bytes += item_wire_size
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
    fragment = bytearray()
    for character in text:
        encoded = character.encode("utf-8")
        if fragment and len(fragment) + len(encoded) > max_bytes:
            yield bytes(fragment).decode("utf-8")
            fragment.clear()
        fragment.extend(encoded)
    if fragment:
        yield bytes(fragment).decode("utf-8")


def _requires_serialized_fragments(
    event: reporting_pb2.FixCompletedEvent,
    max_message_bytes: int,
) -> bool:
    """Return whether an individual field would exceed the gRPC frame limit.

    Args:
        event: Full event being streamed.
        max_message_bytes: Maximum serialized size of a gRPC frame.

    Returns:
        True if the header or a single repeated message cannot fit in one frame.
    """
    header_without_manifest = _header_from_event(event, include_manifest=False)
    base_header_size = reporting_pb2.FixCompletedChunk(header=header_without_manifest).ByteSize()
    if base_header_size > max_message_bytes:
        return True
    if event.HasField("manifest") and event.manifest.ByteSize() + base_header_size + 10 > max_message_bytes:
        return True

    # Reserve space for the repeated-field tag and length prefix around each
    # item. The normal batch budget is far below the channel limit; this guard
    # handles a lone item that would otherwise bypass that budget.
    item_limit = max_message_bytes - 10
    if item_limit < 0:
        return True
    return any(
        item.ByteSize() > item_limit
        for items in (
            event.remaining_violations,
            event.fixed_violations,
            event.patches,
            event.logs,
            event.proposals,
        )
        for item in items
    )


def _serialized_event_chunks(
    event: reporting_pb2.FixCompletedEvent,
    chunk_max_bytes: int,
    max_message_bytes: int,
) -> Iterator[reporting_pb2.FixCompletedChunk]:
    """Split a serialized event into bounded frames for an oversized field.

    Args:
        event: Full event to serialize and fragment.
        chunk_max_bytes: Soft max payload size per fragment.
        max_message_bytes: Maximum serialized size of a gRPC frame.

    Yields:
        reporting_pb2.FixCompletedChunk: Frames whose fragments concatenate to the event.

    Raises:
        ValueError: If the frame limit cannot accommodate a fragment.
    """
    # Leave room for the optional bytes field tag/length and the final marker.
    fragment_max_bytes = min(chunk_max_bytes, max_message_bytes - 16)
    if fragment_max_bytes < 1:
        raise ValueError("max_message_bytes must be greater than 16")

    serialized = event.SerializeToString()
    for offset in range(0, len(serialized), fragment_max_bytes):
        end = min(offset + fragment_max_bytes, len(serialized))
        yield reporting_pb2.FixCompletedChunk(
            serialized_event_fragment=serialized[offset:end],
            last=end == len(serialized),
        )


def _body_chunks(
    event: reporting_pb2.FixCompletedEvent,
    chunk_max_bytes: int,
) -> Iterator[reporting_pb2.FixCompletedChunk]:
    """Yield payload chunks without retaining already emitted batches.

    Args:
        event: Full event being streamed.
        chunk_max_bytes: Soft max serialized size per large-field batch.

    Yields:
        reporting_pb2.FixCompletedChunk: Messages containing one field batch each.
    """
    for violation_batch in _batch_messages(event.remaining_violations, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(remaining_violations=violation_batch)
    for fixed_batch in _batch_messages(event.fixed_violations, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(fixed_violations=fixed_batch)
    for patch_batch in _batch_messages(event.patches, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(patches=patch_batch)
    for log_batch in _batch_messages(event.logs, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(logs=log_batch)
    for proposal_batch in _batch_messages(event.proposals, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(proposals=proposal_batch)
    for fragment in _utf8_fragments(event.content_graph_json, chunk_max_bytes):
        yield reporting_pb2.FixCompletedChunk(content_graph_json_fragment=fragment)


def yield_fix_completed_chunks(
    event: reporting_pb2.FixCompletedEvent,
    chunk_max_bytes: int = CHUNK_MAX_BYTES,
    max_message_bytes: int = GRPC_MAX_MESSAGE_BYTES,
) -> Iterator[reporting_pb2.FixCompletedChunk]:
    """Yield FixCompletedChunk messages packing *event* under gRPC size limits.

    First chunk carries a header with scalars and small nested messages.
    Subsequent chunks lazily batch repeated fields and UTF-8 fragments of
    ``content_graph_json``. If one field cannot fit in a gRPC frame, the full
    event is serialized and fragmented into bounded wire chunks instead. The
    final chunk sets ``last=True``.

    Args:
        event: Fully built FixCompletedEvent to stream.
        chunk_max_bytes: Soft max serialized size per large-field batch.
        max_message_bytes: Hard maximum serialized size of any gRPC frame.

    Yields:
        reporting_pb2.FixCompletedChunk: Chunk messages for ReportFixCompletedStream.

    Raises:
        ValueError: If the frame limit cannot accommodate a fragment.
    """
    if _requires_serialized_fragments(event, max_message_bytes):
        yield from _serialized_event_chunks(event, chunk_max_bytes, max_message_bytes)
        return
    body_chunk_max_bytes = min(chunk_max_bytes, max_message_bytes - 16)
    if body_chunk_max_bytes < 1:
        raise ValueError("max_message_bytes must be greater than 16")
    header = _header_from_event(event)

    has_payload = bool(
        event.remaining_violations
        or event.fixed_violations
        or event.patches
        or event.logs
        or event.proposals
        or event.content_graph_json
    )
    if not has_payload:
        yield reporting_pb2.FixCompletedChunk(header=header, last=True)
        return

    yield reporting_pb2.FixCompletedChunk(header=header, last=False)

    body = iter(_body_chunks(event, body_chunk_max_bytes))
    previous = next(body, None)
    if previous is None:
        # Defensive: has_payload was true but no body chunks were produced.
        yield reporting_pb2.FixCompletedChunk(last=True)
        return

    for current in body:
        previous.last = False
        yield previous
        previous = current
    previous.last = True
    yield previous
