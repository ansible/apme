"""Gateway session client — WebSocket-to-FixSession gRPC bridge.

Replaces the SSE-based scan_client with a bidirectional WebSocket
transport that maps onto Engine's FixSession gRPC stream (ADR-028/029).
Supports both scan-only (enable_ai=false) and interactive fix sessions
with AI proposal approval.

Protocol (browser -> gateway, JSON over WS)::

    {"type": "start",      "options": {...}}
    {"type": "file",       "path": "...", "content": "<base64>"}
    {"type": "files_done"}
    {"type": "approve",    "approved_ids": ["id1", ...]}
    {"type": "escalate_ai", "targets": [{"path": "...", "rule_ids": []}]}
    {"type": "extend"}
    {"type": "close"}

Protocol (gateway -> browser, JSON over WS)::

    {"type": "session_created", "session_id": "...", "ttl_seconds": N}
    {"type": "progress",        "phase": "...", "message": "...", "level": N}
    {"type": "tier1_complete",   ...}
    {"type": "ai_triage",        "candidates": [...], "status": "...", "ttl_seconds": N}
    {"type": "proposals",        "proposals": [...], "tier": N}
    {"type": "approval_ack",     "applied_count": N, "status": "...", "unpatched_count": N, "unpatched_files": [...]}
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
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import grpc
import grpc.aio
from fastapi import WebSocket, WebSocketDisconnect
from google.protobuf.json_format import MessageToDict

from apme.v1 import engine_pb2_grpc
from apme.v1.common_pb2 import GalaxyServerDef
from apme.v1.engine_pb2 import (
    AiEscalateRequest,
    AiEscalateTarget,
    ApprovalRequest,
    CloseRequest,
    ExtendRequest,
    FixOptions,
    ResumeRequest,
    RuleConfig,
    ScanChunk,
    SessionCommand,
)
from apme_engine.config_env import get_env_float, get_env_int
from apme_engine.daemon.chunked_fs import yield_scan_chunks
from apme_engine.graph.severity import severity_from_proto, severity_to_label
from apme_engine.rule_ids import normalize_rule_id
from apme_gateway.db import get_session
from apme_gateway.db.queries import list_rules_with_resolved_config
from apme_gateway.scan.driver import coerce_option_bool

logger = logging.getLogger(__name__)

# PE-26: same 50 MiB send/receive limits as scan/driver.py (_GRPC_MAX_MSG).
_GRPC_MAX_MSG = 50 * 1024 * 1024  # 50 MiB — matches Engine

# Upload ingress caps (mirror Engine PE-35 aggregates). Parsed per-call via
# helpers below so tests can monkeypatch env without reimport.
_UPLOAD_IDLE_TIMEOUT_DEFAULT_S = 60.0
# Interactive command-phase idle timeout: reviewing proposals routinely
# takes longer than the 60s upload idle bound, so the command reader gets
# its own limit aligned with the Engine session TTL (APME_SESSION_TTL,
# default 1800s) instead of reusing the upload timeout.
_COMMAND_IDLE_TIMEOUT_DEFAULT_S = 1800.0
_UPLOAD_MAX_FILE_BYTES_DEFAULT = 10 * 1024 * 1024  # 10 MiB per file
_UPLOAD_MAX_TOTAL_BYTES_DEFAULT = 256 * 1024 * 1024  # 256 MiB aggregate
_UPLOAD_MAX_FILES_DEFAULT = 2000
_UPLOAD_MAX_MESSAGES_DEFAULT = 2000
_UPLOAD_MAX_DURATION_S_DEFAULT = 300.0


def _upload_idle_timeout_s() -> float:
    """Return idle timeout between upload WS messages in seconds.

    Returns:
        Idle timeout in seconds from ``APME_UPLOAD_IDLE_TIMEOUT_S``.
    """
    return get_env_float(
        "APME_UPLOAD_IDLE_TIMEOUT_S",
        _UPLOAD_IDLE_TIMEOUT_DEFAULT_S,
        positive_only=True,
    )


def _command_idle_timeout_s() -> float:
    """Return idle timeout for interactive command WS messages in seconds.

    Interactive review (reading proposals before approving) routinely
    exceeds the upload idle bound, so this separate, session-TTL-aligned
    limit applies to the command phase only.

    Returns:
        Idle timeout in seconds from ``APME_COMMAND_IDLE_TIMEOUT_S``.
    """
    return get_env_float(
        "APME_COMMAND_IDLE_TIMEOUT_S",
        _COMMAND_IDLE_TIMEOUT_DEFAULT_S,
        positive_only=True,
    )


def _upload_max_file_bytes() -> int:
    """Return per-file upload cap in bytes.

    Returns:
        Per-file cap from ``APME_UPLOAD_MAX_FILE_BYTES``.
    """
    return get_env_int(
        "APME_UPLOAD_MAX_FILE_BYTES",
        _UPLOAD_MAX_FILE_BYTES_DEFAULT,
        min_value=1,
    )


def _upload_max_total_bytes() -> int:
    """Return aggregate upload cap in bytes.

    Returns:
        Aggregate cap from ``APME_UPLOAD_MAX_TOTAL_BYTES``.
    """
    return get_env_int(
        "APME_UPLOAD_MAX_TOTAL_BYTES",
        _UPLOAD_MAX_TOTAL_BYTES_DEFAULT,
        min_value=1,
    )


def _upload_max_files() -> int:
    """Return aggregate upload file-count cap.

    Returns:
        File-count cap from ``APME_UPLOAD_MAX_FILES``.
    """
    return get_env_int(
        "APME_UPLOAD_MAX_FILES",
        _UPLOAD_MAX_FILES_DEFAULT,
        min_value=1,
    )


def _upload_max_messages() -> int:
    """Return total WebSocket message bound for one upload.

    Counts file frames plus invalid/unknown frames (anything except
    ``start``/``files_done``) so an active sender cannot pin the upload
    loop with frames that never trip the file/byte caps, while a
    legitimate upload of exactly ``APME_UPLOAD_MAX_FILES`` files still
    fits: the ``start`` + ``files_done`` framing overhead is excluded
    from the bound.

    Returns:
        Message cap from ``APME_UPLOAD_MAX_MESSAGES``.
    """
    return get_env_int(
        "APME_UPLOAD_MAX_MESSAGES",
        _UPLOAD_MAX_MESSAGES_DEFAULT,
        min_value=1,
    )


def _upload_max_duration_s() -> float:
    """Return wall-clock bound for one whole upload.

    Monotonic deadline complementing the per-message idle timeout:
    the idle timeout catches a silent client, this catches a chatty
    one that keeps sending invalid/unknown frames.

    Returns:
        Duration cap from ``APME_UPLOAD_MAX_DURATION_S``.
    """
    return get_env_float(
        "APME_UPLOAD_MAX_DURATION_S",
        _UPLOAD_MAX_DURATION_S_DEFAULT,
        positive_only=True,
    )


_STATUS_NAMES: dict[int, str] = {
    0: "SESSION_STATUS_UNSPECIFIED",
    1: "AWAITING_APPROVAL",
    2: "PROCESSING",
    3: "COMPLETE",
    4: "AWAITING_AI_TRIAGE",
}


def _sanitize_path(relative_path: str) -> str:
    """Sanitize a user-provided relative path to prevent directory traversal.

    Delegates to the engine's canonical
    :func:`apme_engine.daemon.fs_utils.canonicalize_upload_relpath` so
    WebSocket uploads, ``write_chunked_fs``, and FixSession ingress accept
    or reject one payload identically (backslash-tolerant,
    ``./``/``//``-collapsing; absolute/``..`` escapes rejected).

    Args:
        relative_path: Raw path from the upload filename.

    Returns:
        Sanitized relative path string.

    Raises:
        ValueError: If the path escapes the session root or is empty.
    """
    from apme_engine.daemon.fs_utils import canonicalize_upload_relpath

    try:
        return canonicalize_upload_relpath(relative_path)
    except ValueError as exc:
        # Preserve legacy message fragments expected by existing callers:
        # empty/blank/dot-only names -> "Invalid file path", everything
        # else -> "Path traversal detected". Branch on the input (not the
        # wrapped message) so the traversal branch is reachable.
        if not isinstance(relative_path, str) or not relative_path.strip() or set(relative_path.strip()) <= {".", "/"}:
            raise ValueError(f"Invalid file path: {relative_path!r}") from exc
        raise ValueError(f"Path traversal detected: {relative_path!r}") from exc


def _status_name(status: int) -> str:
    """Convert SessionStatus enum int to its proto name.

    Args:
        status: Integer value of the SessionStatus enum.

    Returns:
        Human-readable status name.
    """
    return _STATUS_NAMES.get(status, "UNKNOWN")


def _decode_upload_b64(content: str) -> bytes:
    """Base64-decode one uploaded file's content (blocking helper).

    Runs in a worker thread via :func:`asyncio.to_thread` so large
    decodes never stall the event loop.

    Args:
        content: Base64-encoded file content from the WS ``file`` message.

    Returns:
        Decoded file bytes (invalid input propagates the decoder's
        exception to the caller).
    """
    return base64.b64decode(content, validate=True)


def _write_upload_file(temp_dir: Path, safe: str, content: bytes) -> None:
    """Write one decoded upload to *temp_dir* (blocking helper).

    Runs in a worker thread via :func:`asyncio.to_thread` so per-file
    ``mkdir`` + ``write_bytes`` syscalls never stall the event loop.

    Args:
        temp_dir: Session upload directory (already created).
        safe: Sanitized relative path for this file.
        content: Decoded file bytes to write.
    """
    dest = temp_dir / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


async def _load_scan_rule_configs() -> list[RuleConfig]:
    """Load resolved rule configs from the gateway DB for Engine ``ScanOptions``.

    Best-effort: returns an empty list if the DB is unavailable, the query
    fails, or no rules are registered yet.

    Rule IDs are canonicalized to bare form at the source so the Engine
    never receives ``native:``-prefixed twins of the same rule.  Rows that
    collapse onto one bare ID with conflicting flags fail the session
    fast with the conflicting IDs named: omitting them while sending
    ``rule_configs_complete=True`` would trip the Engine's bidirectional
    audit with a misleading "catalog out of sync" error, and silently
    picking a winner would guess operator intent.

    Returns:
        ``RuleConfig`` messages suitable for ``ScanOptions.rule_configs``.

    Raises:
        ValueError: When any canonical rule ID has conflicting
            configuration flags across duplicate rows.
    """
    try:
        async with get_session() as db:
            rows = await list_rules_with_resolved_config(db)
    except Exception:
        logger.warning("Failed to load rule_configs for scan — proceeding without overrides", exc_info=True)
        return []
    conflicts: set[str] = set()
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        bare = normalize_rule_id(str(row["rule_id"]))
        if bare in conflicts:
            continue
        flags = (row["severity"], row["enabled"], row["enforced"])
        if bare in by_id:
            prior = by_id[bare]
            prior_flags = (prior["severity"], prior["enabled"], prior["enforced"])
            if prior_flags != flags:
                logger.error(
                    "Conflicting gateway rule rows normalize to %r — omitting from scan config",
                    bare,
                )
                conflicts.add(bare)
                del by_id[bare]
                continue
        by_id[bare] = {"severity": row["severity"], "enabled": row["enabled"], "enforced": row["enforced"]}
    if conflicts:
        raise ValueError(
            f"Gateway rule configuration has conflicting rows for {sorted(conflicts)}; "
            "resolve the duplicate rule rows before scanning"
        )
    return [
        RuleConfig(
            rule_id=bare,
            severity=cast(int, entry["severity"]),
            enabled=bool(entry["enabled"]),
            enforced=bool(entry["enforced"]),
        )
        for bare, entry in by_id.items()
    ]


async def _collect_uploads(ws: WebSocket, temp_dir: Path) -> dict[str, Any]:
    """Read start/file/files_done messages and write files to *temp_dir*.

    Each ``receive_json`` is bounded by ``APME_UPLOAD_IDLE_TIMEOUT_S``
    (default 60s). Per-file (``APME_UPLOAD_MAX_FILE_BYTES``, default 10MiB)
    and aggregate (``APME_UPLOAD_MAX_TOTAL_BYTES`` default 256MiB,
    ``APME_UPLOAD_MAX_FILES`` default 2000, mirroring Engine PE-35) caps
    fail fast with an ``error`` frame followed by ``ValueError``.

    Cap accounting:

    * Message cap (``APME_UPLOAD_MAX_MESSAGES``) counts per-frame file
      data plus invalid/unknown frames, excluding the ``start`` and
      ``files_done`` framing overhead — so exactly ``max_files`` files
      in one upload stays reachable when both caps are 2000.
    * File-count cap counts unique canonical paths (``seen`` set): an
      exact repeat upload of the same path overwrites the file on disk
      without consuming another file slot, while two distinct raw paths
      collapsing to one canonical key fail fast instead of dropping a
      file (#17).
    * Byte cap counts unique-file bytes (overwrite replaces the prior
      size instead of double-counting).
    * Malformed ``file`` frames (missing path, traversal, non-string or
      undecodable content) surface an ``error`` frame and are skipped
      without writing or counting toward file/byte caps (they still
      count toward the message cap when applicable).

    Args:
        ws: Active WebSocket connection.
        temp_dir: Directory to write uploaded files into.

    Returns:
        Options dict from the ``start`` message.

    Raises:
        ValueError: If no files were received before ``files_done``, on
            idle timeout, or when any upload cap is exceeded.
    """
    options: dict[str, Any] = {}
    seen_sizes: dict[str, int] = {}
    seen_raw: dict[str, str] = {}
    files_received = 0
    total_bytes = 0
    messages_received = 0
    upload_start = time.monotonic()
    idle_timeout = _upload_idle_timeout_s()
    max_file_bytes = _upload_max_file_bytes()
    max_total_bytes = _upload_max_total_bytes()
    max_files = _upload_max_files()
    max_messages = _upload_max_messages()
    max_duration_s = _upload_max_duration_s()
    # Encoded-size fast gate: base64 expands ~4/3 over raw bytes; +1KiB
    # slack covers JSON framing/padding so legitimate files never trip.
    max_encoded_bytes = (max_file_bytes * 4 + 2) // 3 + 1024

    while True:
        try:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=idle_timeout)
        except TimeoutError:
            err = f"Upload idle timeout after {idle_timeout:g}s waiting for next message"
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": err})
            raise ValueError(err) from None
        msg_type = msg.get("type") if isinstance(msg, dict) else None
        # Total upload bound: count file/invalid/unknown frames (every
        # message except the start/files_done framing overhead) plus a
        # monotonic whole-upload deadline, so a chatty sender cannot
        # hold the WS/temp dir indefinitely without tripping the idle
        # timeout, while max_files files remain reachable when
        # max_messages == max_files.
        if msg_type not in ("start", "files_done"):
            messages_received += 1
            if messages_received > max_messages:
                err = f"Upload message limit exceeded: {messages_received} messages (max {max_messages})"
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "error", "message": err})
                raise ValueError(err)
        if time.monotonic() - upload_start > max_duration_s:
            err = f"Upload time limit exceeded: {max_duration_s:g}s (max {max_duration_s:g}s)"
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": err})
            raise ValueError(err)

        if msg_type == "start":
            raw_options = msg.get("options") or {}
            if not isinstance(raw_options, dict):
                await ws.send_json({"type": "error", "message": "Invalid 'options' value: expected an object"})
                raw_options = {}
            options = raw_options

        elif msg_type == "file":
            raw_path = msg.get("path") if isinstance(msg, dict) else None
            try:
                safe = _sanitize_path(raw_path)  # type: ignore[arg-type]
            except (ValueError, KeyError, TypeError) as exc:
                detail = str(exc) if isinstance(exc, ValueError) else f"Invalid file path: {raw_path!r}"
                await ws.send_json({"type": "error", "message": detail})
                continue
            assert isinstance(raw_path, str)
            # Distinct raw paths collapsing to one canonical key would
            # silently drop a file (#17); trivial respellings
            # (``a.yml`` vs ``./a.yml``) and exact repeats stay last-wins.
            from apme_engine.daemon.fs_utils import is_trivial_respelling  # noqa: PLC0415

            first_raw = seen_raw.setdefault(safe, raw_path)
            if first_raw != raw_path and not is_trivial_respelling(first_raw, raw_path):
                err = (
                    f"Upload paths collide after canonicalization: {first_raw!r} and {raw_path!r} "
                    f"both map to {safe!r}; rename one file"
                )
                await ws.send_json({"type": "error", "message": err})
                raise ValueError(err)
            seen_raw[safe] = raw_path
            old_size = seen_sizes.get(safe, 0)
            raw_content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(raw_content, str):
                # Pre-decode fast gate: reject an oversized base64 frame
                # before buffering/decoding it (fail fast prior to the
                # ~2-3x transient allocation of str+bytes). Post-decode
                # checks below remain as the second gate.
                if len(raw_content) > max_encoded_bytes:
                    err = (
                        f"File {safe} exceeds per-file limit: "
                        f"encoded {len(raw_content)} bytes (max {max_file_bytes} bytes)"
                    )
                    await ws.send_json({"type": "error", "message": err})
                    raise ValueError(err)
                # Aggregate fast gate on estimated decoded size (no
                # allocation) caps total WS buffered bytes. Overwrites
                # replace the prior size instead of double-counting.
                estimated_bytes = (len(raw_content) * 3) // 4
                if total_bytes - old_size + estimated_bytes > max_total_bytes:
                    err = (
                        f"Upload size limit exceeded: "
                        f"~{total_bytes - old_size + estimated_bytes} bytes (max {max_total_bytes} bytes)"
                    )
                    await ws.send_json({"type": "error", "message": err})
                    raise ValueError(err)
            else:
                await ws.send_json({"type": "error", "message": f"Invalid base64 content for {safe}"})
                continue
            try:
                content = await asyncio.to_thread(_decode_upload_b64, raw_content)
            except Exception:
                await ws.send_json({"type": "error", "message": f"Invalid base64 content for {safe}"})
                continue
            if len(content) > max_file_bytes:
                err = f"File {safe} exceeds per-file limit: {len(content)} bytes (max {max_file_bytes} bytes)"
                await ws.send_json({"type": "error", "message": err})
                raise ValueError(err)
            new_total = total_bytes - old_size + len(content)
            if new_total > max_total_bytes:
                err = f"Upload size limit exceeded: {new_total} bytes (max {max_total_bytes} bytes)"
                await ws.send_json({"type": "error", "message": err})
                raise ValueError(err)
            new_unique = len(seen_sizes) + (0 if safe in seen_sizes else 1)
            if new_unique > max_files:
                err = f"Upload file limit exceeded: {new_unique} files (max {max_files} files)"
                await ws.send_json({"type": "error", "message": err})
                raise ValueError(err)
            await asyncio.to_thread(_write_upload_file, temp_dir, safe, content)
            seen_sizes[safe] = len(content)
            total_bytes = new_total
            files_received = len(seen_sizes)

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
    gRPC FixSession stream. Command-phase receives are bounded by a
    dedicated idle timeout (``APME_COMMAND_IDLE_TIMEOUT_S``, default
    aligned with the Engine session TTL) so a silent-but-connected client
    cannot park the reader task, gRPC stream, and temp dir indefinitely
    (#24) while normal interactive review (minutes, not seconds) is
    unaffected; the engine drain still finishes on its own.

    Args:
        ws: Active WebSocket connection.
        queue: Queue feeding the gRPC command stream.
        done: Event signalling the session is finished.
    """
    ws_alive = True
    idle_timeout = _command_idle_timeout_s()
    try:
        while not done.is_set():
            if not ws_alive:
                await asyncio.sleep(0.5)
                continue
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=idle_timeout)
            except TimeoutError:
                logger.warning("WebSocket command idle timeout after %gs; closing command reader", idle_timeout)
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "error", "message": f"Command idle timeout after {idle_timeout:g}s"})
                break
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
            elif msg_type == "escalate_ai":
                raw_targets = msg.get("targets", [])
                if not isinstance(raw_targets, list):
                    logger.warning("Invalid escalate_ai targets: expected list, got %r", type(raw_targets).__name__)
                    continue
                targets: list[AiEscalateTarget] = []
                for t in raw_targets:
                    if not isinstance(t, dict):
                        continue
                    path = str(t.get("path") or "")
                    if not path:
                        continue
                    raw_rules = t.get("rule_ids") or []
                    rule_ids = [str(r) for r in raw_rules] if isinstance(raw_rules, list) else []
                    targets.append(AiEscalateTarget(path=path, rule_ids=rule_ids))
                await queue.put(
                    SessionCommand(ai_escalate=AiEscalateRequest(targets=targets)),
                )
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
    Engine finishes normally and the Gateway can persist the streamed result.

    Args:
        response_stream: Async iterator of gRPC SessionEvent messages.
        ws: Active WebSocket connection.
        scan_id: Scan UUID for inclusion in result messages.
        done: Event to set when the session closes.
    """
    async for event in response_stream:
        oneof = event.WhichOneof("event")

        if oneof == "created":
            payload: dict[str, object] = {
                "type": "session_created",
                "session_id": event.created.session_id,
                "scan_id": scan_id,
                "ttl_seconds": event.created.ttl_seconds,
            }
            if event.created.operation_budget_seconds:
                payload["operation_budget_seconds"] = event.created.operation_budget_seconds
            await _safe_send(ws, payload)

        elif oneof == "progress":
            p = event.progress
            progress_payload: dict[str, object] = {
                "type": "progress",
                "phase": p.phase,
                "message": p.message,
                "level": p.level,
            }
            if p.budget_seconds:
                progress_payload["budget_seconds"] = p.budget_seconds
            if p.ai_total:
                progress_payload["ai_completed"] = p.ai_completed
                progress_payload["ai_total"] = p.ai_total
            await _safe_send(ws, progress_payload)

        elif oneof == "error":
            err = event.error
            await _safe_send(
                ws,
                {
                    "type": "error",
                    "code": err.code,
                    "message": err.message,
                },
            )
            done.set()
            return

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
                            "status": p.status,
                            "source": p.source,
                            "suggestion": p.suggestion,
                            "path": p.path,
                            "node_type": getattr(p, "node_type", "") or "",
                        }
                        for p in pr.proposals
                    ],
                },
            )

        elif oneof == "ai_triage":
            triage = event.ai_triage
            await _safe_send(
                ws,
                {
                    "type": "ai_triage",
                    "status": _status_name(triage.status),
                    "ttl_seconds": triage.ttl_seconds,
                    "candidates": [
                        {
                            "rule_id": v.rule_id,
                            "severity": severity_to_label(severity_from_proto(v.severity)),
                            "message": v.message,
                            "file": v.file,
                            "path": v.path or "",
                            "node_type": getattr(v, "node_type", "") or "",
                            "remediation_class": (int(v.remediation_class) if v.remediation_class else 0),
                            "source": v.source or "",
                            "original_yaml": v.original_yaml or "",
                            "fixed_yaml": v.fixed_yaml or "",
                            "co_fixes": list(v.co_fixes) if v.co_fixes else [],
                            "node_line_start": (int(getattr(v, "node_line_start", 0) or 0) or None),
                        }
                        for v in triage.candidates
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
                    # Additive ApprovalAck surfacing (unpatched splice
                    # skips); getattr keeps mixed-version stubs working.
                    "unpatched_count": int(getattr(ack, "unpatched_count", 0) or 0),
                    "unpatched_files": list(getattr(ack, "unpatched_files", []) or []),
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
    engine_address: str,
    *,
    resume_session_id: str | None = None,
    resume_scan_id: str | None = None,
) -> None:
    """Bridge a WebSocket connection to an Engine FixSession gRPC stream.

    Orchestrates the full lifecycle: file upload collection, gRPC
    FixSession initiation, bidirectional event forwarding, and cleanup.

    When *resume_session_id* is provided, skips file uploads and sends a
    ``ResumeRequest`` to reconnect to an existing server-side session.
    The Engine replays tier1/proposal state so the UI can pick up where
    it left off.

    No client-side gRPC deadline is applied.  Session lifetime is managed
    by the Engine's session store (``APME_SESSION_TTL``, default 1800s).

    Args:
        ws: Accepted FastAPI WebSocket.
        engine_address: gRPC address of the Engine orchestrator.
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
            enable_ai: bool = coerce_option_bool(options.get("enable_ai", False))
            ai_model: str = options.get("ai_model", "")
            interactive: bool = coerce_option_bool(options.get("interactive", False))
            # PE-40: forward validator-skip flags into ScanOptions. These are
            # the actual engine.proto ScanOptions fields
            # (skip_collection_health, skip_dep_audit — ADR-051); there are no
            # skip_gitleaks/skip_validators fields. Defaults preserved when absent.
            skip_collection_health: bool = coerce_option_bool(options.get("skip_collection_health", False))
            skip_dep_audit: bool = coerce_option_bool(options.get("skip_dep_audit", False))
            for _opt_key in options:
                if _opt_key.startswith("skip_") and _opt_key not in (
                    "skip_collection_health",
                    "skip_dep_audit",
                ):
                    logger.warning(
                        "Unknown validator-skip option %r ignored (supported: skip_collection_health, skip_dep_audit)",
                        _opt_key,
                    )

            scan_id = str(uuid.uuid4())
            scan_rule_configs = await _load_scan_rule_configs()

        command_queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()
        done = asyncio.Event()

        channel = grpc.aio.insecure_channel(
            engine_address,
            options=[
                ("grpc.max_send_message_length", _GRPC_MAX_MSG),
                ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
            ],
        )
        try:
            stub = engine_pb2_grpc.EngineStub(channel)  # type: ignore[no-untyped-call]

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
                        skip_collection_health=skip_collection_health,
                        skip_dep_audit=skip_dep_audit,
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
                        interactive=interactive,
                    )
                    first_chunk.fix_options.CopyFrom(fix_opts)  # type: ignore[union-attr]
                    assert first_chunk.options is not None  # noqa: S101 — set by yield_scan_chunks
                    first_chunk.options.rule_configs.extend(scan_rule_configs)
                    if scan_rule_configs:
                        first_chunk.options.rule_configs_complete = True
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
