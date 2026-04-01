"""Gateway session client — WebSocket-to-FixSession gRPC bridge.

Replaces the SSE-based scan_client with a bidirectional WebSocket
transport that maps onto Primary's FixSession gRPC stream (ADR-028/029).
Supports both scan-only (enable_ai=false) and interactive fix sessions
with AI proposal approval.

Protocol (browser -> gateway, JSON over WS)::

    {"type": "start",      "options": {...}}
    {"type": "file",       "path": "...", "content": "<base64>"}
    {"type": "files_done"}
    {"type": "approve",    "approved_ids": ["id1", ...]}
    {"type": "extend"}
    {"type": "close"}

Protocol (gateway -> browser, JSON over WS)::

    {"type": "session_created", "session_id": "...", "ttl_seconds": N}
    {"type": "progress",        "phase": "...", "message": "...", "level": N}
    {"type": "tier1_complete",   ...}
    {"type": "proposals",        "proposals": [...], "tier": N}
    {"type": "approval_ack",     "applied_count": N, "status": "..."}
    {"type": "result",           ...}
    {"type": "error",            "message": "..."}
    {"type": "closed"}
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import grpc
import grpc.aio
from fastapi import WebSocket, WebSocketDisconnect
from google.protobuf.json_format import MessageToDict

from apme.v1 import primary_pb2_grpc
from apme.v1.common_pb2 import GalaxyServerDef
from apme.v1.primary_pb2 import (
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixOptions,
    ResumeRequest,
    ScanChunk,
    SessionCommand,
)
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.severity_defaults import severity_from_proto, severity_to_label

logger = logging.getLogger(__name__)

_STATUS_NAMES: dict[int, str] = {
    0: "SESSION_STATUS_UNSPECIFIED",
    1: "AWAITING_APPROVAL",
    2: "PROCESSING",
    3: "COMPLETE",
}


def _sanitize_path(relative_path: str) -> str:
    """Sanitize a user-provided relative path to prevent directory traversal.

    Args:
        relative_path: Raw path from the upload filename.

    Returns:
        Sanitized relative path string.

    Raises:
        ValueError: If the path contains ``..`` components or resolves to
            an empty / directory-only path (e.g. ``"."``).
    """
    cleaned = PurePosixPath(relative_path.replace("\\", "/"))
    parts = [p for p in cleaned.parts if p not in ("/", "\\", ".")]
    if ".." in parts:
        raise ValueError(f"Path traversal detected: {relative_path!r}")
    if not parts:
        raise ValueError(f"Invalid file path: {relative_path!r}")
    return str(PurePosixPath(*parts))


def _status_name(status: int) -> str:
    """Convert SessionStatus enum int to its proto name.

    Args:
        status: Integer value of the SessionStatus enum.

    Returns:
        Human-readable status name.
    """
    return _STATUS_NAMES.get(status, "UNKNOWN")


async def _collect_uploads(ws: WebSocket, temp_dir: Path) -> dict[str, Any]:
    """Read start/file/files_done messages and write files to *temp_dir*.

    Args:
        ws: Active WebSocket connection.
        temp_dir: Directory to write uploaded files into.

    Returns:
        Options dict from the ``start`` message.

    Raises:
        ValueError: If no files were received before ``files_done``.
    """
    options: dict[str, Any] = {}
    files_received = 0

    while True:
        msg = await ws.receive_json()
        msg_type = msg.get("type")

        if msg_type == "start":
            raw_options = msg.get("options") or {}
            if not isinstance(raw_options, dict):
                await ws.send_json({"type": "error", "message": "Invalid 'options' value: expected an object"})
                raw_options = {}
            options = raw_options

        elif msg_type == "file":
            safe = _sanitize_path(msg["path"])
            try:
                content = base64.b64decode(msg["content"], validate=True)
            except Exception:
                await ws.send_json({"type": "error", "message": f"Invalid base64 content for {safe}"})
                continue
            dest = temp_dir / safe
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            files_received += 1

        elif msg_type == "files_done":
            break

        else:
            await ws.send_json({"type": "error", "message": f"Unexpected message during upload: {msg_type}"})

    if files_received == 0:
        raise ValueError("No files received")

    return options


async def _ws_command_reader(
    ws: WebSocket,
    queue: asyncio.Queue[SessionCommand | None],
    done: asyncio.Event,
) -> None:
    """Read interactive commands from the WebSocket and enqueue for gRPC.

    Keeps the command stream alive until ``done`` is set so a browser
    disconnect during scan processing does not prematurely terminate the
    gRPC FixSession stream.

    Args:
        ws: Active WebSocket connection.
        queue: Queue feeding the gRPC command stream.
        done: Event signalling the session is finished.
    """
    ws_alive = True
    try:
        while not done.is_set():
            if not ws_alive:
                await asyncio.sleep(0.5)
                continue
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                ws_alive = False
                logger.info("WebSocket disconnected; keeping gRPC stream alive until session completes")
                continue

            msg_type = msg.get("type")

            if msg_type == "approve":
                ids = msg.get("approved_ids", [])
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    logger.warning("Invalid approved_ids: expected list of strings, got %r", type(ids).__name__)
                    continue
                logger.info("Received approval for %d proposal(s): %s", len(ids), ids)
                await queue.put(SessionCommand(approve=ApprovalRequest(approved_ids=ids)))
            elif msg_type == "extend":
                await queue.put(SessionCommand(extend=ExtendRequest()))
            elif msg_type == "close":
                await queue.put(SessionCommand(close=CloseRequest()))
                break
            else:
                logger.warning("Ignoring unknown WS command: %s", msg_type)
    except Exception:
        logger.debug("WS reader stopped", exc_info=True)
    finally:
        await queue.put(None)


async def _command_stream(
    chunks: Iterator[ScanChunk],
    queue: asyncio.Queue[SessionCommand | None],
) -> AsyncIterator[SessionCommand]:
    """Yield upload chunks then queued interactive commands.

    Args:
        chunks: ScanChunk upload messages (consumed lazily).
        queue: Queue of interactive commands from the WebSocket reader.

    Yields:
        SessionCommand: Messages for the gRPC FixSession stream.
    """
    for chunk in chunks:
        yield SessionCommand(upload=chunk)

    while True:
        cmd = await queue.get()
        if cmd is None:
            break
        yield cmd


async def _resume_stream(
    session_id: str,
    queue: asyncio.Queue[SessionCommand | None],
) -> AsyncIterator[SessionCommand]:
    """Yield a resume command then queued interactive commands.

    Args:
        session_id: ID of the session to resume.
        queue: Queue of interactive commands from the WebSocket reader.

    Yields:
        SessionCommand: Messages for the gRPC FixSession stream.
    """
    yield SessionCommand(resume=ResumeRequest(session_id=session_id))

    while True:
        cmd = await queue.get()
        if cmd is None:
            break
        yield cmd


async def _safe_send(ws: WebSocket, data: dict[str, Any]) -> bool:
    """Send JSON to the WebSocket, returning False on disconnect.

    Args:
        ws: WebSocket connection.
        data: JSON-serialisable dict.

    Returns:
        True if the message was sent, False if the socket is closed.
    """
    try:
        await ws.send_json(data)
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


async def _forward_events(
    response_stream: Any,
    ws: WebSocket,
    scan_id: str,
    done: asyncio.Event,
) -> None:
    """Read SessionEvents from gRPC and forward as JSON to the WebSocket.

    Tolerates a closed WebSocket: continues draining gRPC events so the
    Primary finishes normally and persists scan results to the gateway.

    Args:
        response_stream: Async iterator of gRPC SessionEvent messages.
        ws: Active WebSocket connection.
        scan_id: Scan UUID for inclusion in result messages.
        done: Event to set when the session closes.
    """
    async for event in response_stream:
        oneof = event.WhichOneof("event")

        if oneof == "created":
            await _safe_send(
                ws,
                {
                    "type": "session_created",
                    "session_id": event.created.session_id,
                    "scan_id": scan_id,
                    "ttl_seconds": event.created.ttl_seconds,
                },
            )

        elif oneof == "progress":
            p = event.progress
            await _safe_send(
                ws,
                {
                    "type": "progress",
                    "phase": p.phase,
                    "message": p.message,
                    "level": p.level,
                },
            )

        elif oneof == "tier1_complete":
            t1 = event.tier1_complete
            await _safe_send(
                ws,
                {
                    "type": "tier1_complete",
                    "idempotency_ok": t1.idempotency_ok,
                    "patches": [
                        {
                            "file": p.path,
                            "diff": p.diff,
                            "applied_rules": list(p.applied_rules),
                            "patched": base64.b64encode(p.patched).decode() if p.patched else None,
                        }
                        for p in t1.applied_patches
                    ],
                    "format_diffs": [{"file": d.path, "diff": d.diff} for d in t1.format_diffs],
                    "report": MessageToDict(t1.report) if t1.HasField("report") else None,
                },
            )

        elif oneof == "proposals":
            pr = event.proposals
            await _safe_send(
                ws,
                {
                    "type": "proposals",
                    "tier": pr.tier,
                    "status": _status_name(pr.status),
                    "proposals": [
                        {
                            "id": p.id,
                            "file": p.file,
                            "rule_id": p.rule_id,
                            "line_start": p.line_start,
                            "line_end": p.line_end,
                            "before_text": p.before_text,
                            "after_text": p.after_text,
                            "diff_hunk": p.diff_hunk,
                            "confidence": p.confidence,
                            "explanation": p.explanation,
                            "tier": p.tier,
                        }
                        for p in pr.proposals
                    ],
                },
            )

        elif oneof == "approval_ack":
            ack = event.approval_ack
            await _safe_send(
                ws,
                {
                    "type": "approval_ack",
                    "applied_count": ack.applied_count,
                    "status": _status_name(ack.status),
                    "ttl_seconds": ack.ttl_seconds,
                },
            )

        elif oneof == "result":
            r = event.result
            await _safe_send(
                ws,
                {
                    "type": "result",
                    "scan_id": scan_id,
                    "patches": [
                        {
                            "file": p.path,
                            "diff": p.diff,
                            "applied_rules": list(p.applied_rules),
                            "patched": base64.b64encode(p.patched).decode() if p.patched else None,
                        }
                        for p in r.patches
                    ],
                    "report": MessageToDict(r.report) if r.HasField("report") else None,
                    "remaining_violations": [
                        {
                            "rule_id": v.rule_id,
                            "severity": severity_to_label(severity_from_proto(v.severity)),
                            "message": v.message,
                            "file": v.file,
                        }
                        for v in r.remaining_violations
                    ],
                },
            )
            await _safe_send(ws, {"type": "closed"})
            done.set()
            break

        elif oneof == "expiring":
            await _safe_send(
                ws,
                {
                    "type": "expiring",
                    "ttl_seconds": event.expiring.ttl_seconds,
                },
            )

        elif oneof == "closed":
            await _safe_send(ws, {"type": "closed"})
            done.set()
            break


async def handle_session(
    ws: WebSocket,
    primary_address: str,
    *,
    resume_session_id: str | None = None,
    resume_scan_id: str | None = None,
) -> None:
    """Bridge a WebSocket connection to a Primary FixSession gRPC stream.

    Orchestrates the full lifecycle: file upload collection, gRPC
    FixSession initiation, bidirectional event forwarding, and cleanup.

    When *resume_session_id* is provided, skips file uploads and sends a
    ``ResumeRequest`` to reconnect to an existing server-side session.
    The Primary replays tier1/proposal state so the UI can pick up where
    it left off.

    No client-side gRPC deadline is applied.  Session lifetime is managed
    by the Primary's session store (``APME_SESSION_TTL``, default 1800s).

    Args:
        ws: Accepted FastAPI WebSocket.
        primary_address: gRPC address of the Primary orchestrator.
        resume_session_id: If set, resume this existing session instead
            of starting a new upload.
        resume_scan_id: Original scan_id for the session being resumed,
            so event forwarding preserves scan-based links.
    """
    from apme_gateway._galaxy_inject import load_galaxy_server_defs  # noqa: PLC0415

    temp_dir: Path | None = None
    galaxy_servers: list[GalaxyServerDef] = []
    try:
        if resume_session_id:
            scan_id = resume_scan_id or resume_session_id
            logger.info("Resuming session %s (scan_id=%s)", resume_session_id, scan_id)
        else:
            galaxy_servers = await load_galaxy_server_defs()
            temp_dir = Path(tempfile.mkdtemp(prefix="apme-gw-session-"))
            options = await _collect_uploads(ws, temp_dir)

            ansible_version: str = options.get("ansible_version", "")
            collections: list[str] = options.get("collections", [])
            enable_ai: bool = options.get("enable_ai", True)
            ai_model: str = options.get("ai_model", "")

            scan_id = str(uuid.uuid4())

        command_queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()
        done = asyncio.Event()

        channel = grpc.aio.insecure_channel(primary_address)
        try:
            stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]

            if resume_session_id:

                async def _cmd_iter() -> AsyncIterator[SessionCommand]:
                    async for cmd in _resume_stream(resume_session_id, command_queue):
                        yield cmd

            else:

                def _chunks_with_fix_options() -> Iterator[ScanChunk]:
                    assert temp_dir is not None  # noqa: S101
                    chunk_iter = yield_scan_chunks(
                        temp_dir,
                        scan_id=scan_id,
                        project_root_name="upload",
                        ansible_core_version=ansible_version or None,
                        collection_specs=collections or None,
                        galaxy_servers=galaxy_servers or None,
                    )
                    first_chunk = next(chunk_iter, None)
                    if first_chunk is None:
                        return
                    fix_opts = FixOptions(
                        ansible_core_version=ansible_version,
                        collection_specs=collections or [],
                        enable_ai=enable_ai,
                        ai_model=ai_model,
                        galaxy_servers=galaxy_servers or [],
                    )
                    first_chunk.fix_options.CopyFrom(fix_opts)  # type: ignore[union-attr]
                    yield first_chunk
                    yield from chunk_iter

                async def _cmd_iter() -> AsyncIterator[SessionCommand]:
                    async for cmd in _command_stream(_chunks_with_fix_options(), command_queue):
                        yield cmd

            response_stream = stub.FixSession(_cmd_iter())

            reader_task = asyncio.create_task(_ws_command_reader(ws, command_queue, done))

            try:
                await _forward_events(response_stream, ws, scan_id, done)
            except grpc.aio.AioRpcError as e:
                logger.warning("gRPC FixSession error (scan_id=%s): %s", scan_id, e.details())
                await _safe_send(
                    ws,
                    {
                        "type": "error",
                        "message": f"Engine error: {e.details()}",
                    },
                )
                done.set()
            finally:
                if not done.is_set():
                    logger.warning(
                        "gRPC stream ended without result/closed (scan_id=%s)",
                        scan_id,
                    )
                    await _safe_send(
                        ws,
                        {
                            "type": "error",
                            "message": "Session ended unexpectedly — the engine connection was lost",
                        },
                    )
                done.set()
                reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader_task
        finally:
            await channel.close(grace=None)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during session")
    except ValueError as exc:
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": str(exc)})
    except Exception as exc:
        logger.exception("Session failed: %s", exc)
        with contextlib.suppress(Exception):
            await ws.send_json(
                {
                    "type": "error",
                    "message": f"Operation failed ({type(exc).__name__})",
                }
            )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
