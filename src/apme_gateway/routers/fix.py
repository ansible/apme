"""WebSocket /api/v1/fix — FixSession bidi gRPC bridge (ADR-028 / ADR-029).

Each WebSocket connection maps 1:1 to a server-side FixSession bidi gRPC
stream.  The gateway translates between JSON WebSocket messages and protobuf
SessionCommand/SessionEvent messages.

Lifecycle:
  1. Browser opens WS, sends ``{"type": "start", "project_path": "...", ...}``
  2. Gateway discovers files, opens FixSession bidi stream, uploads chunks
  3. Server events (progress, proposals, results) are relayed as JSON
  4. Browser sends approve/extend/close commands via WS
  5. WS disconnect or explicit close tears down the gRPC stream
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from google.protobuf.json_format import MessageToDict

from apme.v1.common_pb2 import File
from apme.v1.primary_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixOptions,
    ResumeRequest,
    ScanChunk,
    SessionCommand,
)
from apme_gateway.config import load_config
from apme_gateway.deps import get_primary_client
from apme_gateway.models.schemas import WSCommand
from apme_gateway.services.grpc_client import PrimaryClient
from apme_gateway.services.scan_service import CHUNK_MAX_BYTES, _discover_files

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["fix"])


async def _upload_chunks(
    files: list[tuple[str, bytes]],
    fix_opts: FixOptions,
    queue: asyncio.Queue[SessionCommand | None],
) -> None:
    """Read files and push upload SessionCommands into the queue.

    Args:
        files: Discovered (rel_path, content) pairs.
        fix_opts: Fix options for the first chunk.
        queue: Shared command queue feeding the bidi stream.
    """
    batch: list[File] = []
    batch_bytes = 0
    first = True

    for rel, content in files:
        msg_size = len(rel.encode()) + len(content)
        if batch and batch_bytes + msg_size > CHUNK_MAX_BYTES:
            kwargs: dict[str, object] = {"files": batch, "last": False}
            if first:
                kwargs.update(scan_id="", project_root="project", fix_options=fix_opts)
                first = False
            await queue.put(SessionCommand(upload=ScanChunk(**kwargs)))
            batch = []
            batch_bytes = 0
        batch.append(File(path=rel, content=content))
        batch_bytes += msg_size

    final: dict[str, object] = {"files": batch, "last": True}
    if first:
        final.update(scan_id="", project_root="project", fix_options=fix_opts)
    await queue.put(SessionCommand(upload=ScanChunk(**final)))


async def _command_iter(queue: asyncio.Queue[SessionCommand | None]) -> AsyncIterator[SessionCommand]:
    """Yield SessionCommand messages until a None sentinel.

    Args:
        queue: Async queue of commands.

    Yields:
        SessionCommand protobuf messages.
    """
    while True:
        cmd = await queue.get()
        if cmd is None:
            return
        yield cmd


def _event_to_json(event: object) -> dict[str, object]:
    """Convert a SessionEvent protobuf to a JSON-safe dict for the browser.

    Args:
        event: SessionEvent protobuf message.

    Returns:
        JSON-serialisable dict with a ``type`` key.
    """
    d: dict[str, object] = MessageToDict(event, preserving_proto_field_name=True)  # type: ignore[arg-type]
    event_field = getattr(event, "WhichOneof", lambda _: None)("event")
    if event_field:
        d["type"] = event_field
    return d


@router.websocket("/fix")
async def fix_websocket(
    ws: WebSocket,
    client: PrimaryClient = Depends(get_primary_client),
) -> None:
    """WebSocket endpoint bridging browser ↔ FixSession gRPC stream."""
    await ws.accept()

    try:
        raw = await ws.receive_text()
        start_msg = WSCommand.model_validate_json(raw)
    except (WebSocketDisconnect, Exception):
        return

    if start_msg.type != "start" or not start_msg.project_path:
        await ws.send_json({"type": "error", "message": "First message must be type=start with project_path"})
        await ws.close()
        return

    config = load_config()
    workspace = Path(config.workspace_root).resolve()
    root = Path(start_msg.project_path).resolve()
    if not str(root).startswith(str(workspace) + "/") and root != workspace:
        await ws.send_json({"type": "error", "message": "Path outside workspace root"})
        await ws.close()
        return
    if not root.exists():
        await ws.send_json({"type": "error", "message": f"Path not found: {start_msg.project_path}"})
        await ws.close()
        return

    files = _discover_files(root)

    fix_opts = FixOptions(enable_ai=start_msg.enable_ai)
    if start_msg.ansible_core_version:
        fix_opts.ansible_core_version = start_msg.ansible_core_version
    if start_msg.collection_specs:
        fix_opts.collection_specs.extend(start_msg.collection_specs)

    cmd_queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()

    await _upload_chunks(files, fix_opts, cmd_queue)

    stream = client.fix_session(_command_iter(cmd_queue))

    async def _relay_events() -> None:
        """Read gRPC events and relay them to the WebSocket."""
        try:
            async for event in stream:
                payload = _event_to_json(event)
                await ws.send_json(payload)
        except Exception:
            logger.exception("gRPC event relay error")

    relay_task = asyncio.create_task(_relay_events())

    try:
        while True:
            raw = await ws.receive_text()
            msg = WSCommand.model_validate_json(raw)

            if msg.type == "approve":
                await cmd_queue.put(
                    SessionCommand(approve=ApprovalRequest(approved_ids=msg.ids)),
                )
            elif msg.type == "extend":
                await cmd_queue.put(SessionCommand(extend=ExtendRequest()))
            elif msg.type == "close":
                await cmd_queue.put(SessionCommand(close=CloseRequest()))
                await cmd_queue.put(None)
                break
            elif msg.type == "resume":
                await cmd_queue.put(
                    SessionCommand(resume=ResumeRequest(session_id=msg.session_id or "")),
                )
            else:
                await ws.send_json({"type": "error", "message": f"Unknown command: {msg.type}"})
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        await cmd_queue.put(None)
        relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay_task
