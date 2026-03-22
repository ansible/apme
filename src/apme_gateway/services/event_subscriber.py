"""Persistent gRPC subscriber for ScanCompletedEvent (ADR-020).

On gateway startup, opens a server-streaming RPC to Primary's
SubscribeScanEvents and persists each event to the local database.
Auto-reconnects on disconnect with exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import grpc
import grpc.aio

from apme.v1 import primary_pb2_grpc
from apme.v1.primary_pb2 import ScanCompletedEvent, SubscribeRequest
from apme_gateway.models.tables import Scan, Violation

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF = 2
_MAX_BACKOFF = 60
_task: asyncio.Task[None] | None = None


async def _persist_event(
    event: ScanCompletedEvent,
    session_factory: object,
) -> None:
    """Write a ScanCompletedEvent to the database.

    Idempotent: skips if a scan with the same scan_id already exists.
    Uses INSERT + IntegrityError catch to handle races where two events
    with the same scan_id arrive near-simultaneously.

    Args:
        event: The protobuf event from Primary.
        session_factory: SQLAlchemy async_sessionmaker.
    """
    import json

    from google.protobuf.json_format import MessageToDict
    from sqlalchemy.exc import IntegrityError

    async with session_factory() as session:  # type: ignore[operator]
        existing = await session.get(Scan, event.scan_id)
        if existing is not None:
            logger.debug("Skipping duplicate event scan_id=%s (already persisted)", event.scan_id)
            return

        diag_json = None
        if event.HasField("diagnostics") and event.diagnostics is not None:
            diag_json = json.dumps(MessageToDict(event.diagnostics, preserving_proto_field_name=True))

        now = datetime.now(tz=timezone.utc).isoformat()

        summary_json = None
        if event.HasField("summary") and event.summary is not None:
            summary_json = json.dumps(MessageToDict(event.summary, preserving_proto_field_name=True))

        scan = Scan(
            id=event.scan_id,
            project_path=event.project_path,
            created_at=now,
            status="completed",
            total_violations=len(event.violations),
            source=event.source or "engine",
            diagnostics=diag_json,
            summary_json=summary_json,
        )

        violations_orm = []
        for v in event.violations:
            line_val = None
            variant = v.WhichOneof("line_oneof")
            if variant == "line":
                line_val = v.line
            elif variant == "line_range":
                line_val = v.line_range.start

            violations_orm.append(
                Violation(
                    id=uuid.uuid4().hex,
                    scan_id=event.scan_id,
                    rule_id=v.rule_id,
                    level=v.level,
                    message=v.message,
                    file=v.file,
                    line=line_val,
                    path=v.path,
                ),
            )

        session.add(scan)
        session.add_all(violations_orm)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.debug("Race-condition duplicate scan_id=%s — already persisted by another task", event.scan_id)
            return

        logger.info(
            "Persisted scan event: scan_id=%s violations=%d source=%s",
            event.scan_id,
            len(violations_orm),
            event.source,
        )


async def _subscribe_loop(
    primary_address: str,
    session_factory: object,
    max_msg_bytes: int,
) -> None:
    """Connect to Primary and consume ScanCompletedEvent stream forever.

    Reconnects with exponential backoff on any failure.

    Args:
        primary_address: Primary gRPC host:port.
        session_factory: SQLAlchemy async_sessionmaker for persistence.
        max_msg_bytes: Max gRPC message size.
    """
    backoff = _INITIAL_BACKOFF

    while True:
        channel = None
        try:
            channel = grpc.aio.insecure_channel(
                primary_address,
                options=[
                    ("grpc.max_receive_message_length", max_msg_bytes),
                ],
            )
            stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
            logger.info("Subscribing to scan events from %s", primary_address)

            stream = stub.SubscribeScanEvents(SubscribeRequest())
            backoff = _INITIAL_BACKOFF

            async for event in stream:
                try:
                    await _persist_event(event, session_factory)
                except Exception:
                    logger.exception("Failed to persist scan event %s", event.scan_id)

        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                logger.warning(
                    "Primary unavailable for scan events — retrying in %ds",
                    backoff,
                )
            else:
                logger.warning(
                    "Scan event stream error (%s) — retrying in %ds",
                    exc.code(),
                    backoff,
                )
        except asyncio.CancelledError:
            logger.info("Scan event subscriber cancelled")
            return
        except Exception:
            logger.exception("Unexpected error in scan event subscriber — retrying in %ds", backoff)
        finally:
            if channel is not None:
                await channel.close(grace=None)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _MAX_BACKOFF)


def start(primary_address: str, session_factory: object, max_msg_bytes: int) -> None:
    """Launch the subscriber background task.

    Safe to call multiple times — only starts once.

    Args:
        primary_address: Primary gRPC host:port.
        session_factory: SQLAlchemy async_sessionmaker.
        max_msg_bytes: Max gRPC message size.
    """
    global _task  # noqa: PLW0603

    if _task is not None and not _task.done():
        return

    _task = asyncio.create_task(
        _subscribe_loop(primary_address, session_factory, max_msg_bytes),
    )
    logger.info("Scan event subscriber started — listening to %s", primary_address)


async def stop() -> None:
    """Cancel the subscriber task."""
    global _task  # noqa: PLW0603
    import contextlib

    if _task is not None and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
