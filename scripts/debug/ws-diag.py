#!/usr/bin/env python3
"""WebSocket diagnostic client for APME gateway.

Connects directly to the gateway WS endpoint, uploads files, and monitors
the connection with precise timestamps to diagnose disconnect issues.

Usage:
    python scripts/debug/ws-diag.py [TARGET] [--url URL] [--model MODEL] [--auto-approve]

    TARGET: file or directory to scan (default: tests/fixtures/terrible-playbook)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

try:
    import websockets  # type: ignore[import-not-found]
except ImportError:
    sys.exit("Install websockets: pip install websockets")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def _elapsed(t0: float) -> str:
    return f"{time.monotonic() - t0:.1f}s"


async def run(
    target: Path,
    ws_url: str,
    model: str,
    auto_approve: bool,
) -> None:
    """Connect to the gateway WS endpoint, upload files, and monitor events.

    Args:
        target: File or directory containing YAML files to upload.
        ws_url: WebSocket URL of the gateway endpoint.
        model: AI model identifier (empty string disables AI).
        auto_approve: If True, automatically approve all AI proposals.
    """
    t0 = time.monotonic()
    last_msg_at = time.monotonic()

    yaml_files: list[tuple[str, bytes]] = []
    if target.is_file():
        yaml_files.append((target.name, target.read_bytes()))
    elif target.is_dir():
        for f in sorted(target.rglob("*.yml")) + sorted(target.rglob("*.yaml")):
            rel = str(f.relative_to(target))
            yaml_files.append((rel, f.read_bytes()))

    if not yaml_files:
        sys.exit(f"No YAML files found in {target}")

    _log(f"[{_ts()}] Connecting to {ws_url}")
    _log(f"[{_ts()}] Files: {len(yaml_files)} ({sum(len(c) for _, c in yaml_files)} bytes)")
    _log(f"[{_ts()}] Model: {model or '(none)'}")
    _log(f"[{_ts()}] Auto-approve: {auto_approve}")
    print()

    try:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=30,
            close_timeout=10,
            max_size=50 * 1024 * 1024,
        ) as ws:
            _log(f"[{_ts()}] Connected (ping_interval=20s)")

            # Phase 1: Upload
            options: dict[str, object] = {"enable_ai": bool(model)}
            if model:
                options["ai_model"] = model
            await ws.send(json.dumps({"type": "start", "options": options}))

            for path, content in yaml_files:
                await ws.send(
                    json.dumps(
                        {
                            "type": "file",
                            "path": path,
                            "content": base64.b64encode(content).decode(),
                        }
                    )
                )
            await ws.send(json.dumps({"type": "files_done"}))
            _log(f"[{_ts()}] [{_elapsed(t0)}] Upload complete")

            # Phase 2: Listen for events
            proposal_count = 0
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=300)
                except asyncio.TimeoutError:
                    _log(f"[{_ts()}] [{_elapsed(t0)}] TIMEOUT: no message for 300s")
                    break

                now = time.monotonic()
                gap = now - last_msg_at
                last_msg_at = now

                if isinstance(raw, bytes):
                    _log(f"[{_ts()}] [{_elapsed(t0)}] BINARY ({len(raw)} bytes, gap={gap:.1f}s)")
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    _log(f"[{_ts()}] [{_elapsed(t0)}] UNPARSEABLE (gap={gap:.1f}s): {raw[:100]}")
                    continue

                msg_type = msg.get("type", "?")

                if msg_type == "progress":
                    phase = msg.get("phase", "")
                    message = msg.get("message", "")
                    _log(f"[{_ts()}] [{_elapsed(t0)}] progress [{phase}] {message} (gap={gap:.1f}s)")

                elif msg_type == "session_created":
                    sid = msg.get("session_id", "?")
                    ttl = msg.get("ttl_seconds", "?")
                    _log(f"[{_ts()}] [{_elapsed(t0)}] SESSION {sid} (ttl={ttl}s, gap={gap:.1f}s)")

                elif msg_type == "tier1_complete":
                    report = msg.get("report", {})
                    fixed = report.get("fixed", 0)
                    ai = report.get("remaining_ai", 0)
                    manual = report.get("remaining_manual", 0)
                    patches = len(msg.get("applied_patches", []))
                    _log(
                        f"[{_ts()}] [{_elapsed(t0)}] TIER1 COMPLETE"
                        f" fixed={fixed} ai={ai} manual={manual} patches={patches} (gap={gap:.1f}s)"
                    )

                elif msg_type == "proposals":
                    proposals = msg.get("proposals", [])
                    proposal_count += len(proposals)
                    _log(
                        f"[{_ts()}] [{_elapsed(t0)}] PROPOSALS: {len(proposals)} received"
                        f" (total={proposal_count}, gap={gap:.1f}s)"
                    )
                    for p in proposals:
                        pid = p.get("id", "?")
                        rule = p.get("rule_id", "?")
                        conf = p.get("confidence", 0)
                        line_s, line_e = p.get("line_start", "?"), p.get("line_end", "?")
                        _log(f"    [{rule}] {p.get('file', '?')} L{line_s}-{line_e} conf={conf:.0%} id={pid}")

                    if auto_approve and proposals:
                        approved = [p["id"] for p in proposals]
                        await ws.send(json.dumps({"type": "approve", "approved_ids": approved}))
                        _log(f"[{_ts()}] [{_elapsed(t0)}] AUTO-APPROVED {len(approved)} proposal(s)")

                elif msg_type == "approval_ack":
                    count = msg.get("applied_count", 0)
                    _log(f"[{_ts()}] [{_elapsed(t0)}] APPROVAL ACK: {count} applied (gap={gap:.1f}s)")

                elif msg_type == "result":
                    remaining = len(msg.get("remaining_violations", []))
                    patches = len(msg.get("patches", []))
                    _log(
                        f"[{_ts()}] [{_elapsed(t0)}] RESULT: {patches} patches, {remaining} remaining (gap={gap:.1f}s)"
                    )
                    await ws.send(json.dumps({"type": "close"}))

                elif msg_type == "error":
                    _log(f"[{_ts()}] [{_elapsed(t0)}] ERROR: {msg.get('message', '?')} (gap={gap:.1f}s)")

                elif msg_type == "closed":
                    _log(f"[{_ts()}] [{_elapsed(t0)}] CLOSED (gap={gap:.1f}s)")
                    break

                else:
                    _log(f"[{_ts()}] [{_elapsed(t0)}] {msg_type}: {json.dumps(msg)[:120]} (gap={gap:.1f}s)")

    except websockets.exceptions.ConnectionClosedError as e:
        _log(f"\n[{_ts()}] [{_elapsed(t0)}] CONNECTION CLOSED: code={e.code} reason={e.reason!r}")
        _log(f"    Last message was {time.monotonic() - last_msg_at:.1f}s ago")
    except websockets.exceptions.ConnectionClosedOK:
        _log(f"\n[{_ts()}] [{_elapsed(t0)}] Connection closed cleanly")
    except Exception as e:
        _log(f"\n[{_ts()}] [{_elapsed(t0)}] EXCEPTION: {type(e).__name__}: {e}")
        _log(f"    Last message was {time.monotonic() - last_msg_at:.1f}s ago")

    _log(f"\n[{_ts()}] Total elapsed: {_elapsed(t0)}")


def main() -> None:
    """CLI entry point for ws-diag."""
    parser = argparse.ArgumentParser(description="APME WebSocket diagnostic client")
    parser.add_argument(
        "target", nargs="?", default="tests/fixtures/terrible-playbook", help="File or directory to scan"
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8081/api/v1/ws/session",
        help="WebSocket URL (default: ws://localhost:8081/api/v1/ws/session)",
    )
    parser.add_argument("--model", default="", help="AI model to use (enables AI)")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all proposals")
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        sys.exit(f"Target not found: {args.target}")

    asyncio.run(run(target, args.url, args.model, args.auto_approve))


if __name__ == "__main__":
    main()
